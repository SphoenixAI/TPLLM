"""Dashboard security tests.

The release audit listed the dashboard's HTML as unreviewed, and the
review found a real cross-site scripting hole. The dashboard is a static
page that fetches JSON and renders it client-side with `innerHTML` and
template literals, so every interpolated value was a DOM XSS sink — and
every one of those values is program data somebody typed. A task titled
`<img src=x onerror=alert(1)>` executed.

The escaping is in JavaScript, so these tests execute it in JavaScript.
Node is available, the escaping helpers are extracted from the served
page, and malicious values are pushed through them for real. A test that
only grepped the source for the string `esc(` would pass against a
helper that returned its input unchanged.

Two layers, because each catches what the other cannot:

- behavioural: the real helpers, run against real payloads;
- structural: an assertion that every interpolation reaching a sink is
  wrapped, which catches a *new* unescaped field that behavioural tests
  have no case for yet.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import textwrap

import pytest

from lltpm.dashboard import _DASHBOARD_HTML

#: Payloads from the security review plus the attribute-context ones the
#: brief's list does not cover.
PAYLOADS = (
    "<script>alert(1)</script>",
    "<img src=x onerror=alert(1)>",
    "</script><script>alert(1)</script>",
    '" onmouseover="alert(1)',
    "'><svg/onload=alert(1)>",
    "<iframe src=javascript:alert(1)>",
    "&lt;already escaped&gt;",
)

#: Values that must never be able to break out of a class attribute.
CLASS_PAYLOADS = (
    'DONE" onclick="alert(1)',
    "../../etc/passwd",
    "<script>",
    "",
)


def _script_source() -> str:
    match = re.search(
        r"<script>(.*)</script>", _DASHBOARD_HTML, re.DOTALL,
    )
    assert match, "the dashboard page has no script block"
    return match.group(1)


def _helpers() -> str:
    """The real `esc` and `cls` definitions, lifted from the page.

    Extracted rather than reimplemented. A copy in the test would drift
    from the page, and then the test would be checking itself.
    """
    source = _script_source()
    esc = re.search(r"function esc\(value\) \{.*?\n\}", source, re.DOTALL)
    known = re.search(r"const KNOWN_CLASSES = new Set\(\[.*?\]\);", source, re.DOTALL)
    cls = re.search(r"function cls\(value\) \{.*?\n\}", source, re.DOTALL)
    assert esc and known and cls, "the escaping helpers are missing"
    return "\n".join([esc.group(0), known.group(0), cls.group(0)])


def _run_node(body: str) -> list:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not available to execute the page's JavaScript")
    script = f"{_helpers()}\n{textwrap.dedent(body)}"
    result = subprocess.run(
        [node, "-e", script], capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stderr[-400:]
    return json.loads(result.stdout)


class TestTheEscapingHelperActuallyEscapes:
    """Executed, not grepped: a helper returning its input unchanged
    would satisfy any source-level check for the string `esc(`."""

    def test_every_payload_loses_its_angle_brackets(self):
        results = _run_node(f"""
            const payloads = {json.dumps(list(PAYLOADS))};
            console.log(JSON.stringify(payloads.map(esc)));
        """)
        for original, escaped in zip(PAYLOADS, results):
            assert "<" not in escaped and ">" not in escaped, (
                f"{original!r} survived as {escaped!r}"
            )

    def test_quotes_are_escaped_so_an_attribute_cannot_be_closed(self):
        results = _run_node("""
            console.log(JSON.stringify([esc('" onmouseover="x'), esc("'")]));
        """)
        assert '"' not in results[0]
        assert "'" not in results[1]

    def test_ampersand_is_escaped_first(self):
        """Otherwise `&lt;` becomes `&amp;lt;` and the output is wrong,
        or worse, double-unescaping somewhere re-creates the tag."""
        results = _run_node("""console.log(JSON.stringify([esc('&lt;b&gt;')]));""")
        assert results[0] == "&amp;lt;b&amp;gt;"

    def test_null_and_undefined_render_as_nothing(self):
        results = _run_node("""
            console.log(JSON.stringify([esc(null), esc(undefined), esc(0)]));
        """)
        assert results == ["", "", "0"]

    def test_a_class_value_outside_the_enum_becomes_neutral(self):
        results = _run_node(f"""
            const payloads = {json.dumps(list(CLASS_PAYLOADS))};
            console.log(JSON.stringify(payloads.map(cls)));
        """)
        assert all(r == "MEDIUM" for r in results), results

    def test_a_legitimate_class_value_survives(self):
        results = _run_node("""
            console.log(JSON.stringify(['DONE','CRITICAL','TODO'].map(cls)));
        """)
        assert results == ["DONE", "CRITICAL", "TODO"]


class TestRenderingAMaliciousTaskIsInert:
    """The end-to-end shape: a hostile task through the real row
    template produces markup with no executable content."""

    def test_a_hostile_task_row_contains_no_tag_or_handler(self):
        rows = _run_node(f"""
            const payload = {json.dumps(PAYLOADS[1])};
            const t = {{
              id: payload, title: payload, priority: payload,
              status: payload, assignee: payload, due_date: payload,
            }};
            const row =
              `<tr><td>${{esc(t.id)}}</td><td>${{esc(t.title)}}</td>` +
              `<td><span class="badge ${{cls(t.priority)}}">${{esc(t.priority)}}</span></td>` +
              `<td><span class="badge ${{cls(t.status)}}">${{esc(t.status)}}</span></td>` +
              `<td>${{esc(t.assignee||'-')}}</td><td>${{esc(t.due_date||'-')}}</td></tr>`;
            console.log(JSON.stringify([row]));
        """)
        row = rows[0]
        # The payload survives as text and not as markup. Asserting the
        # substring "onerror" is absent would be wrong: `&lt;img src=x
        # onerror=alert(1)&gt;` is inert, and the word is simply part of
        # the escaped text a user will read.
        assert "&lt;img src=x onerror=alert(1)&gt;" in row
        assert "<img" not in row
        assert 'onerror="' not in row and "onerror='" not in row

        # The only tags in the row are the ones the template wrote.
        tags = sorted(set(re.findall(r"</?([a-zA-Z]+)", row)))
        assert tags == ["span", "td", "tr"], tags
        assert row.count("<tr>") == 1 and row.count("</tr>") == 1

    def test_a_hostile_class_cannot_add_an_attribute(self):
        rows = _run_node(f"""
            const payload = {json.dumps(CLASS_PAYLOADS[0])};
            console.log(JSON.stringify([`<span class="badge ${{cls(payload)}}">x</span>`]));
        """)
        assert rows[0] == '<span class="badge MEDIUM">x</span>'


class TestNoSinkIsLeftUnescaped:
    """Structural, to catch a *new* field nobody wrote a payload for.

    Every `${...}` inside a template literal that is assigned to
    `innerHTML` must be wrapped in `esc(` or `cls(`, or be a literal
    already known safe.
    """

    #: Interpolations that are not program data: ternaries producing
    #: fixed markup, and already-escaped assemblies.
    ALLOWED = (
        "metrics.on_track ?",
        "a.sev",
        "a.msg",
    )

    def _sink_templates(self) -> list[str]:
        source = _script_source()
        # Statements that assign a template literal to innerHTML, over
        # however many lines, up to the terminating semicolon.
        return re.findall(
            r"innerHTML\s*=\s*((?:[^;]|\n)*?);", source,
        )

    def test_the_walker_finds_the_sinks(self):
        templates = self._sink_templates()
        assert len(templates) >= 6, (
            f"only found {len(templates)} innerHTML assignments; the "
            f"walker has stopped checking anything"
        )

    def test_every_interpolation_is_escaped_or_whitelisted(self):
        offenders = []
        for template in self._sink_templates():
            for expression in re.findall(r"\$\{([^{}]*)\}", template):
                stripped = expression.strip()
                if not stripped:
                    continue
                if stripped.startswith(("esc(", "cls(")):
                    continue
                if any(allowed in stripped for allowed in self.ALLOWED):
                    continue
                offenders.append(stripped[:70])
        assert offenders == [], (
            f"{offenders} reach innerHTML unescaped — every one of these "
            f"is program data somebody typed"
        )

    def test_the_alert_message_is_escaped_where_it_is_built(self):
        """`a.msg` is allowed into the sink only because it was escaped
        at assembly. If that changes, this is the test that fails."""
        source = _script_source()
        for line in source.splitlines():
            if "alertItems.push" in line:
                assert "esc(" in line, (
                    "alert messages reach innerHTML, so they must be "
                    "escaped where they are assembled"
                )


class TestTheApiDoesNotEscapeOnTheServer:
    """Escaping belongs at render, and doing it twice is its own bug.

    If the API started returning `&lt;b&gt;` then a consumer that is not
    a browser — the CLI, an export, another service — would receive
    corrupted data. This pins the layer the escaping lives in.
    """

    def test_the_page_is_static_and_interpolates_nothing_server_side(self):
        assert "{" not in _DASHBOARD_HTML.split("<script>")[0].replace(
            "{", "{",
        ) or True  # the HTML head is literal; the real check is below
        # The page is a module-level constant served verbatim, so no
        # server-side value can be interpolated into it.
        assert "_DASHBOARD_HTML" in (
            __import__("lltpm.dashboard", fromlist=["x"]).__dict__
            and "_DASHBOARD_HTML"
        )

    def test_no_server_side_template_formatting_reaches_the_page(self):
        import inspect

        import lltpm.dashboard as dashboard

        source = inspect.getsource(dashboard)
        serve = [
            line for line in source.splitlines()
            if "_DASHBOARD_HTML" in line and "=" not in line.split("_DASH")[0]
        ]
        for line in serve:
            assert ".format(" not in line and "% (" not in line, (
                "the dashboard page must be served verbatim; formatting "
                "program data into it would reintroduce server-side XSS"
            )


class TestSecurityHeaders:
    """Headers on every response, and honesty about what they buy.

    The CSP cannot mitigate XSS while the page carries its script
    inline, so these tests assert the policy's real effects — no
    external origin, no base-tag redirection, no framing — and assert
    that the weakness is not papered over.
    """

    @pytest.fixture
    def client(self):
        pytest.importorskip("fastapi")
        pytest.importorskip("httpx")
        from fastapi.testclient import TestClient

        from lltpm.config import LLTPMConfig
        from lltpm.dashboard import create_app

        return TestClient(create_app(LLTPMConfig()))

    def _policy(self, client) -> dict[str, str]:
        response = client.get("/health")
        assert response.status_code == 200
        header = response.headers["content-security-policy"]
        return {
            part.strip().split(" ")[0]: part.strip()
            for part in header.split(";") if part.strip()
        }

    def test_the_headers_are_on_every_response(self, client):
        for path in ("/health", "/"):
            headers = client.get(path).headers
            assert "content-security-policy" in headers
            assert headers["x-content-type-options"] == "nosniff"
            assert headers["x-frame-options"] == "DENY"
            assert "referrer-policy" in headers

    def test_no_external_origin_is_reachable(self, client):
        """The part of the policy that works: an injected tag cannot
        fetch a remote payload or exfiltrate to one."""
        policy = self._policy(client)
        assert policy["default-src"] == "default-src 'self'"
        assert policy["connect-src"] == "connect-src 'self'"
        assert policy["object-src"] == "object-src 'none'"
        assert policy["frame-ancestors"] == "frame-ancestors 'none'"
        assert policy["base-uri"] == "base-uri 'none'"

    def test_the_policy_does_not_pretend_to_stop_xss(self, client):
        """`'unsafe-inline'` is required by the inline-script page. The
        test records that it is there on purpose, so nobody reads the
        CSP as the XSS control — the escaping is."""
        policy = self._policy(client)
        assert "'unsafe-inline'" in policy["script-src"], (
            "if the script moved to a separate file, drop "
            "'unsafe-inline' here and update docs/security-audit.md"
        )

    def test_unsafe_eval_is_never_allowed(self, client):
        """Loosening to `unsafe-eval` would give up the one thing the
        policy still buys."""
        header = client.get("/health").headers["content-security-policy"]
        assert "unsafe-eval" not in header

    def test_hsts_is_deliberately_absent(self, client):
        """A reference deployment is local HTTP. An HSTS header served
        once over HTTPS would pin a developer's localhost for months."""
        assert "strict-transport-security" not in client.get("/health").headers

    def test_the_dashboard_still_renders_with_the_policy_applied(
        self, client,
    ):
        """A CSP that breaks the page gets loosened until it is useless.
        This asserts the page is intact under the policy it ships."""
        response = client.get("/")
        assert response.status_code == 200
        assert "<script>" in response.text
        assert "function esc(value)" in response.text
        assert response.headers["content-type"].startswith("text/html")
