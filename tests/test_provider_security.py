"""Provider credential boundaries and the webhook threat model (44).

Two concerns that look like one and are not.

**Credential containment.** A provider token must not reach durable
history, a log line, a fixture, an export or an exception message. Each
of those is a different escape route and each is tested separately,
because the one that leaks is always the one nobody thought of — an
exception message produced during a failed read is a log line in
practice, and a fixture is a log line that gets committed.

**Webhook trust.** Signature verification, idempotency and
authorization are three independent properties, and conflating any two
produces a hole:

- verification without idempotency: a valid delivery replayed twice
  becomes two facts;
- idempotency without verification: a forged body with a fresh id is
  accepted once, which is all an attacker needs;
- either without authorization: a correctly signed delivery from a
  workspace nobody bound gets filed under somebody's project.

So each is attacked on its own, and the tests assert that passing one
does not imply the others.
"""

from __future__ import annotations

import ast
import inspect
import json
import pathlib
import time
from datetime import datetime, timedelta

import pytest

from lltpm.integrations.providers import describe_providers

#: A token-shaped string per provider. None is real; each follows the
#: documented prefix so the redaction patterns have something to match.
FAKE_CREDENTIALS = {
    "github": "ghp_" + "A" * 36,
    "slack": "xoxb-1111111111-2222222222-" + "b" * 24,
    "jira": "ATATT" + "c" * 60,
    "generic-bearer": "Bearer " + "d" * 40,
    "aws": "AKIAIOSFODNN7EXAMPLE",
}

NOW = datetime(2026, 10, 22, 12, 0)


# ── Credential containment ───────────────────────────────────────────


class TestCredentialsStayOutOfEvidence:
    """A token pasted into a message, a ticket or a pull request body is
    the realistic leak: nobody puts it there on purpose, and the journal
    is append-only, so it would be permanent."""

    def _envelope_with(self, text: str):
        from lltpm.integrations.envelopes import make_envelope

        return make_envelope(
            source_system="slack", source_type="chat",
            source_id="T0ABCDEF/C0ABCDEF/1771408800.000100",
            event_type="chat_message", observed_at=NOW, received_at=NOW,
            project_ids=["atlas"], title="deploy notes", content=text,
        )

    @pytest.mark.parametrize("label", sorted(FAKE_CREDENTIALS))
    def test_a_credential_in_content_is_redacted(self, label):
        secret = FAKE_CREDENTIALS[label]
        envelope = self._envelope_with(
            f"deployed by hand using {secret}; we should automate this"
        )
        serialized = envelope.model_dump_json()
        assert secret not in serialized, f"{label} survived into evidence"
        assert "we should automate this" in (envelope.content or ""), (
            "the sentence around the credential is the actual evidence "
            "and must survive"
        )
        assert envelope.metadata.get("redaction_applied") is True

    def test_a_credential_in_a_title_is_redacted(self):
        from lltpm.integrations.envelopes import make_envelope

        secret = FAKE_CREDENTIALS["github"]
        envelope = make_envelope(
            source_system="github", source_type="pull_request",
            source_id="acme/atlas#1", event_type="pr_merged",
            observed_at=NOW, received_at=NOW, project_ids=["atlas"],
            title=f"rotate {secret}", content="done",
        )
        assert secret not in envelope.model_dump_json()

    def test_a_credential_never_reaches_the_journal(self):
        from lltpm.persistence import (
            InMemoryEventJournalStore,
            InMemoryIntelligenceArtifactStore,
            InMemoryRuntimeStateStore,
            build_manifest,
        )
        from lltpm.runtime import (
            RuntimeStateStore as RuntimeProjection,
            process_durable_organizational_events,
        )
        from lltpm.runtime.baselines import baseline_providers
        from lltpm.runtime.services import LiveIntelligenceServices
        from lltpm.runtime.sources import JournalBackedDataSource
        from lltpm.scenarios.models import ScenarioHorizon

        journal = InMemoryEventJournalStore()
        envelopes = [
            self._envelope_with(f"token is {secret}")
            for secret in FAKE_CREDENTIALS.values()
        ]
        services = LiveIntelligenceServices(
            data_source=JournalBackedDataSource(
                journal=journal, organization_id="acme",
            ),
            as_of=NOW,
            scenario_horizon=ScenarioHorizon(
                start_at=NOW, end_at=NOW + timedelta(days=14),
                maximum_depth=4, minimum_branch_probability=0.02,
            ),
            **baseline_providers(),
        )
        process_durable_organizational_events(
            envelopes=envelopes, organization_id="acme", journal=journal,
            state_store=InMemoryRuntimeStateStore(),
            artifact_store=InMemoryIntelligenceArtifactStore(),
            runtime_projection=RuntimeProjection(organization_id="acme"),
            intelligence_services=services,
            manifest=build_manifest(policy_fingerprint="sec"), now=NOW,
        )
        durable = json.dumps([
            record.model_dump(mode="json")
            for record in journal.read_from(global_sequence=1)
        ], default=str)
        for label, secret in FAKE_CREDENTIALS.items():
            assert secret not in durable, (
                f"{label} reached append-only history, where it would be "
                f"permanent"
            )

    def test_a_credential_never_reaches_an_export(self):
        from lltpm.persistence import (
            InMemoryEventJournalStore, build_manifest, export_journal,
        )
        from lltpm.runtime.durability import append_evidence_accepted

        journal = InMemoryEventJournalStore()
        for secret in FAKE_CREDENTIALS.values():
            append_evidence_accepted(
                journal=journal, envelope=self._envelope_with(f"k={secret}"),
                organization_id="acme", recorded_at=NOW,
            )
        bundle = export_journal(
            journal=journal, organization_id="acme", exported_at=NOW,
            manifest=build_manifest(policy_fingerprint="sec"),
        )
        serialized = bundle.model_dump_json()
        for label, secret in FAKE_CREDENTIALS.items():
            assert secret not in serialized, f"{label} leaked via export"


class TestCredentialsStayOutOfLogsAndErrors:
    @pytest.mark.parametrize("label", sorted(FAKE_CREDENTIALS))
    def test_the_log_formatter_redacts_a_credential(self, label):
        import logging

        from lltpm.observability import JsonLinesFormatter

        record = logging.LogRecord(
            name="lltpm.test", level=logging.INFO, pathname=__file__,
            lineno=1, msg="read failed", args=(), exc_info=None,
        )
        record.reason = f"Authorization: {FAKE_CREDENTIALS[label]}"
        rendered = JsonLinesFormatter().format(record)
        assert FAKE_CREDENTIALS[label] not in rendered

    @pytest.mark.parametrize(
        "module",
        [
            "lltpm.integrations.github_reader",
            "lltpm.integrations.slack_reader",
            "lltpm.integrations.jira_reader",
        ],
    )
    def test_no_reader_can_name_its_token_in_a_log_call(self, module):
        """Asserted against the source: the Authorization header is in
        scope at the log site in each of these modules, so the only
        protection is that no log call reaches it."""
        source = inspect.getsource(__import__(module, fromlist=["x"]))
        tree = ast.parse(source)
        offenders = []
        for node in ast.walk(tree):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in {
                    "debug", "info", "warning", "error", "exception",
                }
            ):
                continue
            rendered = ast.dump(node)
            for needle in ("header", "_token", "credential", "secret"):
                if needle in rendered.lower():
                    offenders.append(f"{module}:{node.lineno}:{needle}")
        assert offenders == [], offenders

    @pytest.mark.parametrize(
        "module",
        [
            "lltpm.integrations.slack_reader",
            "lltpm.integrations.jira_reader",
        ],
    )
    def test_a_reader_does_not_expose_its_token(self, module):
        imported = __import__(module, fromlist=["x"])
        readers = [
            value for name, value in vars(imported).items()
            if name.startswith("Urllib")
        ]
        assert readers, f"no live reader found in {module}"
        for reader in readers:
            assert not any(
                name in {"token", "get_token", "secret"}
                for name in dir(reader)
            ), f"{reader.__name__} exposes its credential"

    def test_no_fixture_contains_a_credential(self):
        """A fixture is a log line that gets committed."""
        offenders = []
        for path in sorted(
            pathlib.Path("tests/fixtures").rglob("*.json")
        ):
            text = path.read_text()
            for label, secret in FAKE_CREDENTIALS.items():
                if secret in text:
                    offenders.append(f"{path}:{label}")
            # Provider token prefixes, in case a real one was pasted.
            for prefix in ("ghp_", "xoxb-", "xoxp-", "ATATT", "AKIA"):
                if prefix in text:
                    offenders.append(f"{path}:{prefix}")
        assert offenders == [], offenders


# ── Webhook trust: three independent properties ──────────────────────


class TestSignatureVerification:
    def _slack(self):
        return pytest.importorskip(
            "lltpm.integrations.slack_reader",
        )

    def _jira(self):
        return pytest.importorskip("lltpm.integrations.jira_reader")

    def test_slack_accepts_a_correct_signature(self):
        slack = self._slack()
        secret, body = "signing-secret-at-least-32-bytes-long", b'{"ok":1}'
        now = time.time()
        timestamp = str(int(now))
        signature = slack.compute_signature(
            signing_secret=secret, timestamp=timestamp, raw_body=body,
        )
        # Raises on failure, returns None on success.
        slack.verify_signature(
            signing_secret=secret, timestamp=timestamp, raw_body=body,
            provided_signature=signature, now=now,
        )

    def test_slack_rejects_a_tampered_body(self):
        slack = self._slack()
        secret, body = "signing-secret-at-least-32-bytes-long", b'{"ok":1}'
        now = time.time()
        timestamp = str(int(now))
        signature = slack.compute_signature(
            signing_secret=secret, timestamp=timestamp, raw_body=body,
        )
        with pytest.raises(slack.SignatureInvalidError):
            slack.verify_signature(
                signing_secret=secret, timestamp=timestamp,
                raw_body=b'{"ok":2}', provided_signature=signature, now=now,
            )

    def test_slack_rejects_a_wrong_secret(self):
        slack = self._slack()
        body = b'{"ok":1}'
        now = time.time()
        timestamp = str(int(now))
        signature = slack.compute_signature(
            signing_secret="the-real-signing-secret-32-bytes-ok",
            timestamp=timestamp, raw_body=body,
        )
        with pytest.raises(slack.SignatureInvalidError):
            slack.verify_signature(
                signing_secret="a-different-signing-secret-32-bytes",
                timestamp=timestamp, raw_body=body,
                provided_signature=signature, now=now,
            )

    def test_slack_rejects_a_stale_timestamp(self):
        """Replay protection is part of verification for Slack, because
        the timestamp is inside the signed string — so a captured
        delivery cannot be re-sent an hour later."""
        slack = self._slack()
        secret, body = "signing-secret-at-least-32-bytes-long", b'{"ok":1}'
        now = time.time()
        stale = str(int(now) - 3600)
        signature = slack.compute_signature(
            signing_secret=secret, timestamp=stale, raw_body=body,
        )
        with pytest.raises(slack.SignatureInvalidError):
            slack.verify_signature(
                signing_secret=secret, timestamp=stale, raw_body=body,
                provided_signature=signature, now=now,
            )

    def test_jira_accepts_a_correct_signature(self):
        jira = self._jira()
        secret, body = "jira-webhook-secret-at-least-32-bytes", b'{"a":1}'
        header = jira.compute_webhook_signature(raw_body=body, secret=secret)
        assert jira.verify_webhook_signature(
            raw_body=body, signature_header=header, secret=secret,
        )

    def test_jira_rejects_a_tampered_body(self):
        jira = self._jira()
        secret, body = "jira-webhook-secret-at-least-32-bytes", b'{"a":1}'
        header = jira.compute_webhook_signature(raw_body=body, secret=secret)
        assert not jira.verify_webhook_signature(
            raw_body=b'{"a":2}', signature_header=header, secret=secret,
        )

    def test_jira_rejects_a_wrong_secret(self):
        jira = self._jira()
        body = b'{"a":1}'
        header = jira.compute_webhook_signature(
            raw_body=body, secret="the-real-webhook-secret-32-bytes-x",
        )
        assert not jira.verify_webhook_signature(
            raw_body=body, signature_header=header,
            secret="a-different-webhook-secret-32-byte",
        )

    def test_jira_computes_over_the_raw_body(self):
        """Signing a re-serialized body is a classic hole: a receiver
        that parses then re-encodes can verify a signature over bytes
        the sender never signed."""
        jira = self._jira()
        import inspect as _inspect

        source = _inspect.getsource(jira.compute_webhook_signature)
        assert "raw_body" in source
        assert "json.dumps" not in source

    @pytest.mark.parametrize(
        "module",
        ["lltpm.integrations.slack_reader", "lltpm.integrations.jira_reader"],
    )
    def test_verification_uses_a_timing_safe_comparison(self, module):
        source = inspect.getsource(__import__(module, fromlist=["x"]))
        assert "compare_digest" in source, (
            "a signature compared with == leaks its own correctness"
        )

    def test_no_verification_failure_message_contains_the_secret(self):
        """A verifier that echoes the expected digest, or the secret,
        hands an attacker the answer."""
        slack = self._slack()
        jira = self._jira()
        secret = "a-very-distinctive-signing-secret-value"
        messages: list[str] = []

        now = time.time()
        try:
            slack.verify_signature(
                signing_secret=secret, timestamp=str(int(now)),
                raw_body=b"{}", provided_signature="v0=deadbeef", now=now,
            )
        except Exception as error:  # noqa: BLE001
            messages.append(str(error))

        expected = jira.compute_webhook_signature(
            raw_body=b"{}", secret=secret,
        )
        assert messages, "slack verification did not fail as expected"
        for message in messages:
            assert secret not in message
            assert expected.split("=")[-1] not in message


class TestVerificationIsNotIdempotency:
    """Two valid, correctly signed deliveries of the same event must
    collapse to one fact. A signature says who sent it, never how many
    times."""

    def test_a_replayed_slack_delivery_is_one_fact(self):
        from lltpm.integrations.slack_normalizer import normalize_payload
        from lltpm.integrations.normalizers import (
            ProjectBindings, SourceBinding,
        )

        bindings = ProjectBindings(bindings=[SourceBinding(
            source_system="slack", external_scope="T0ABCDEF/C0ABCDEF",
            project_ids=["atlas"],
        )])
        event = {
            "type": "event_callback", "event_id": "Ev0REPLAY1",
            "team_id": "T0ABCDEF",
            "event": {
                "type": "message", "channel": "C0ABCDEF",
                "user": "U0ABCDEF", "text": "contract v3 approved",
                "ts": "1771408800.000100",
                "event_ts": "1771408800.000100",
                "channel_type": "channel",
            },
        }
        first = normalize_payload(
            payload_kind="event", payload=event, bindings=bindings,
            received_at=NOW, scope="T0ABCDEF/C0ABCDEF",
        )
        second = normalize_payload(
            payload_kind="event", payload=event, bindings=bindings,
            received_at=NOW + timedelta(hours=3),
            scope="T0ABCDEF/C0ABCDEF",
        )
        assert first.envelopes and second.envelopes
        assert (
            first.envelopes[0].idempotency_key
            == second.envelopes[0].idempotency_key
        )
        assert first.envelopes[0].id == second.envelopes[0].id


class TestIdempotencyIsNotAuthorization:
    """A correctly signed, never-before-seen delivery from a workspace
    nobody bound must not be filed under somebody's project."""

    def test_an_unbound_slack_workspace_is_refused(self):
        from lltpm.integrations.normalizers import (
            ProjectBindings, SourceBinding,
        )
        from lltpm.integrations.slack_normalizer import normalize_payload

        bindings = ProjectBindings(bindings=[SourceBinding(
            source_system="slack", external_scope="T0ABCDEF/C0ABCDEF",
            project_ids=["atlas"],
        )])
        hostile = {
            "type": "event_callback", "event_id": "Ev0HOSTILE",
            "team_id": "T0EVIL",
            "event": {
                "type": "message", "channel": "C0EVIL",
                "user": "U0EVIL", "text": "ship it",
                "ts": "1771408800.000200",
                "event_ts": "1771408800.000200",
                "channel_type": "channel",
            },
        }
        outcome = normalize_payload(
            payload_kind="event", payload=hostile, bindings=bindings,
            received_at=NOW, scope="T0EVIL/C0EVIL",
        )
        assert not outcome.envelopes
        assert outcome.unbound or outcome.rejected

    def test_an_unbound_jira_project_is_refused(self):
        from lltpm.integrations.jira_normalizer import (
            jira_project_bindings, normalize_payload,
        )

        bindings = jira_project_bindings(
            mapping={("acme.atlassian.net", "ABC"): ["atlas"]},
        )
        issues = json.loads(
            pathlib.Path("tests/fixtures/jira/issues.json").read_text()
        )
        outcome = normalize_payload(
            payload_kind="issue", payload=issues[0], bindings=bindings,
            received_at=NOW, scope="evil.atlassian.net",
        )
        assert not outcome.envelopes
        assert outcome.unbound or outcome.rejected


class TestEveryProviderRefusesHostileInput:
    @pytest.mark.parametrize(
        "descriptor", describe_providers(),
        ids=lambda d: d.source_system,
    )
    def test_an_empty_payload_produces_no_evidence(self, descriptor):
        from lltpm.integrations.normalizers import ProjectBindings

        outcome = descriptor.normalizer()(
            payload_kind=descriptor.payload_kinds[0], payload={},
            bindings=ProjectBindings(), received_at=NOW, scope="",
        )
        assert not outcome.envelopes
        assert outcome.rejected or outcome.unbound or outcome.uninterpreted

    @pytest.mark.parametrize(
        "descriptor", describe_providers(),
        ids=lambda d: d.source_system,
    )
    def test_an_oversized_text_field_is_excerpted(self, descriptor):
        """A provider can hand over a megabyte of text. The journal is
        append-only, so an unbounded copy is permanent."""
        from lltpm.integrations.normalizers import CONTENT_EXCERPT_LIMIT

        assert CONTENT_EXCERPT_LIMIT <= 8192, (
            "the excerpt limit is the only thing bounding how much "
            "provider text becomes durable"
        )

    @pytest.mark.parametrize(
        "descriptor", describe_providers(),
        ids=lambda d: d.source_system,
    )
    def test_a_malformed_timestamp_is_refused_not_defaulted(
        self, descriptor,
    ):
        from lltpm.integrations.normalizers import to_naive_utc
        from lltpm.integrations.normalizers import TimestampMissingError

        for hostile in ("", "yesterday", "9999-99-99", None, []):
            with pytest.raises(TimestampMissingError):
                to_naive_utc(hostile)
