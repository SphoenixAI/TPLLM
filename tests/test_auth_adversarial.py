"""Adversarial tests against the authentication that exists.

The release audit left authentication unreviewed. The brief for this one
was explicit: attack the implementation rather than redesign it. So
these tests forge, expire, replay, retype and re-sign tokens, and they
check the RBAC decisions against roles that should not have them.

The review found two things worth more than the tests themselves.

**A refresh token was accepted as an access token.** The dashboard's
auth dependency called `validate_jwt` and never looked at
`token_type`, so a credential with a seven-day life satisfied every
endpoint that a thirty-minute access token was meant to. `expect_type`
now defaults to `"access"`, which makes the restrictive answer the
default rather than something a caller has to know to ask for.

**Twenty-two of twenty-eight routes enforce no authentication at all.**
The JWT and RBAC machinery is real and well built; it guards the
mutating and admin routes and nothing else, while the project
advertised "FastAPI with JWT authentication". That is true of the
machinery and false of the surface. The open set is now enumerated in
`dashboard.UNAUTHENTICATED_READ_ROUTES`, and the test below fails if a
route joins or leaves it — so the boundary is a decision somebody makes
rather than a fact nobody checked.
"""

from __future__ import annotations

import pathlib
import tempfile
from datetime import datetime, timedelta, timezone

import pytest

from tpllm.artifacts.store import ArtifactStore
from tpllm.models.security import JWTConfig, Permission, Role
from tpllm.workflows.security_manager import SecurityManager

#: 32+ bytes, per the project convention, so no insecure-key warning
#: muddies the output.
SECRET = "adversarial-test-secret-key-at-least-32-bytes"
OTHER_SECRET = "a-different-secret-key-also-at-least-32-bytes"


def _manager(secret: str = SECRET, **overrides) -> SecurityManager:
    store = ArtifactStore(pathlib.Path(tempfile.mkdtemp()))
    config = JWTConfig(secret_key=secret, **overrides)
    return SecurityManager(store, jwt_config=config)


@pytest.fixture
def manager() -> SecurityManager:
    return _manager()


def _jwt():
    return pytest.importorskip("jwt")


# ── Forgery and tampering ────────────────────────────────────────────


class TestForgedTokens:
    def test_a_token_signed_with_another_secret_is_rejected(self, manager):
        issued = _manager(OTHER_SECRET).issue_token_pair("bob", Role.ADMIN)
        assert manager.validate_jwt(issued.access_token) is None

    def test_an_unsigned_token_is_rejected(self, manager):
        """`alg: none` is the classic. The decoder pins the algorithm
        list, so this should never have worked — asserted because the
        cost of being wrong is total."""
        jwt = _jwt()
        forged = jwt.encode(
            {
                "sub": "attacker", "role": "admin", "scopes": [],
                "iss": manager._jwt_config.issuer,
                "aud": manager._jwt_config.audience,
                "iat": int(datetime.now(timezone.utc).timestamp()),
                "exp": int(
                    (datetime.now(timezone.utc) + timedelta(hours=1))
                    .timestamp()
                ),
                "jti": "forged", "token_type": "access",
            },
            key="", algorithm="none",
        )
        assert manager.validate_jwt(forged) is None

    def test_a_role_cannot_be_escalated_by_editing_the_payload(
        self, manager,
    ):
        """Editing a claim invalidates the signature. The point is that
        the role is *inside* the signed payload rather than taken from
        anywhere else."""
        jwt = _jwt()
        pair = manager.issue_token_pair("viewer", Role.VIEWER)
        claims = jwt.decode(
            pair.access_token, SECRET, algorithms=["HS256"],
            issuer=manager._jwt_config.issuer,
            audience=manager._jwt_config.audience,
        )
        claims["role"] = "admin"
        tampered = jwt.encode(claims, OTHER_SECRET, algorithm="HS256")
        assert manager.validate_jwt(tampered) is None

    @pytest.mark.parametrize(
        "token",
        ["", "not-a-jwt", "a.b.c", "Bearer x", "..", "null", "{}" * 50],
    )
    def test_malformed_input_is_rejected_without_raising(
        self, manager, token,
    ):
        assert manager.validate_jwt(token) is None

    def test_a_token_missing_a_required_claim_is_rejected(self, manager):
        jwt = _jwt()
        for omitted in ("sub", "iss", "aud", "exp", "iat"):
            payload = {
                "sub": "bob", "role": "admin", "scopes": [],
                "iss": manager._jwt_config.issuer,
                "aud": manager._jwt_config.audience,
                "iat": int(datetime.now(timezone.utc).timestamp()),
                "exp": int(
                    (datetime.now(timezone.utc) + timedelta(hours=1))
                    .timestamp()
                ),
                "jti": "x", "token_type": "access",
            }
            payload.pop(omitted)
            token = jwt.encode(payload, SECRET, algorithm="HS256")
            assert manager.validate_jwt(token) is None, (
                f"a token with no '{omitted}' was accepted"
            )


class TestWrongAudienceOrIssuer:
    def test_a_token_for_another_audience_is_rejected(self, manager):
        other = _manager(SECRET, audience="some-other-service")
        issued = other.issue_token_pair("bob", Role.ADMIN)
        assert manager.validate_jwt(issued.access_token) is None

    def test_a_token_from_another_issuer_is_rejected(self, manager):
        other = _manager(SECRET, issuer="some-other-issuer")
        issued = other.issue_token_pair("bob", Role.ADMIN)
        assert manager.validate_jwt(issued.access_token) is None

    def test_the_same_secret_is_not_enough_on_its_own(self, manager):
        """Shared-secret systems get compromised by a second service
        that happens to use the same key. Issuer and audience are what
        stop its tokens working here."""
        other = _manager(
            SECRET, issuer="neighbour", audience="neighbour-api",
        )
        issued = other.issue_token_pair("bob", Role.ADMIN)
        assert manager.validate_jwt(issued.access_token) is None


# ── Expiry and replay ────────────────────────────────────────────────


class TestExpiryAndReplay:
    def test_an_expired_access_token_is_rejected(self, manager):
        jwt = _jwt()
        past = datetime.now(timezone.utc) - timedelta(hours=2)
        token = jwt.encode(
            {
                "sub": "bob", "role": "admin", "scopes": [],
                "iss": manager._jwt_config.issuer,
                "aud": manager._jwt_config.audience,
                "iat": int(past.timestamp()),
                "exp": int((past + timedelta(minutes=30)).timestamp()),
                "jti": "expired", "token_type": "access",
            },
            SECRET, algorithm="HS256",
        )
        assert manager.validate_jwt(token) is None

    def test_a_revoked_token_stops_working(self, manager):
        pair = manager.issue_token_pair("bob", Role.ADMIN)
        claims = manager.validate_jwt(pair.access_token)
        assert claims is not None
        manager.revoke_token(claims.jti)
        assert manager.validate_jwt(pair.access_token) is None

    def test_a_refresh_token_cannot_be_replayed(self, manager):
        """Rotation: the first exchange revokes the token it consumed."""
        pair = manager.issue_token_pair("bob", Role.ENGINEER)
        first = manager.refresh_access_token(pair.refresh_token)
        assert first is not None
        again = manager.refresh_access_token(pair.refresh_token)
        assert again is None, "a refresh token was accepted twice"

    def test_rotation_issues_a_different_refresh_token(self, manager):
        pair = manager.issue_token_pair("bob", Role.ENGINEER)
        rotated = manager.refresh_access_token(pair.refresh_token)
        assert rotated is not None
        assert rotated.refresh_token != pair.refresh_token

    def test_an_access_token_cannot_be_exchanged_for_a_pair(self, manager):
        pair = manager.issue_token_pair("bob", Role.ENGINEER)
        assert manager.refresh_access_token(pair.access_token) is None


class TestTokenTypeConfusion:
    """The finding. A refresh token lives for seven days; an access
    token for thirty minutes. Accepting the first where the second was
    meant turns a short window into a long one."""

    def test_a_refresh_token_is_not_an_access_token(self, manager):
        pair = manager.issue_token_pair("bob", Role.ADMIN)
        assert manager.validate_jwt(pair.refresh_token) is None

    def test_the_default_is_the_restrictive_one(self, manager):
        """So a future caller is safe without knowing any of this."""
        import inspect

        signature = inspect.signature(manager.validate_jwt)
        assert signature.parameters["expect_type"].default == "access"

    def test_the_refresh_path_still_accepts_a_refresh_token(self, manager):
        pair = manager.issue_token_pair("bob", Role.ADMIN)
        claims = manager.validate_jwt(
            pair.refresh_token, expect_type="refresh",
        )
        assert claims is not None and claims.token_type == "refresh"

    def test_an_access_token_is_not_a_refresh_token(self, manager):
        pair = manager.issue_token_pair("bob", Role.ADMIN)
        assert manager.validate_jwt(
            pair.access_token, expect_type="refresh",
        ) is None


# ── Roles and permissions ────────────────────────────────────────────


class TestRoleBoundaries:
    @pytest.mark.parametrize(
        "role,permission",
        [
            (Role.VIEWER, Permission.PROGRAM_WRITE),
            (Role.VIEWER, Permission.PROGRAM_DELETE),
            (Role.VIEWER, Permission.DASHBOARD_ADMIN),
            (Role.ENGINEER, Permission.PROGRAM_DELETE),
            (Role.ENGINEER, Permission.DASHBOARD_ADMIN),
            (Role.ENGINEER, Permission.INTEGRATION_CONFIGURE),
            (Role.MANAGER, Permission.DASHBOARD_ADMIN),
        ],
    )
    def test_a_role_does_not_hold_a_permission_above_it(
        self, manager, role, permission,
    ):
        assert not manager.check_permission(role, permission)

    def test_a_viewer_can_still_read(self, manager):
        assert manager.check_permission(Role.VIEWER, Permission.PROGRAM_READ)

    def test_admin_holds_every_permission(self, manager):
        """Stated so that narrowing admin later is a deliberate change
        rather than an accident."""
        for permission in Permission:
            assert manager.check_permission(Role.ADMIN, permission), (
                f"admin lacks {permission.value}"
            )

    def test_the_token_carries_the_role_it_was_issued_for(self, manager):
        for role in Role:
            pair = manager.issue_token_pair("someone", role)
            claims = manager.validate_jwt(pair.access_token)
            assert claims is not None and claims.role is role


class TestApiKeyBridge:
    def test_an_unknown_api_key_yields_no_token(self, manager):
        assert manager.authenticate_api_key_to_jwt("tpllm_nonexistent") is None

    def test_a_revoked_api_key_stops_bridging(self, manager):
        record, raw = manager.create_api_key(name="ci", role=Role.ENGINEER)
        assert manager.authenticate_api_key_to_jwt(raw) is not None
        manager.revoke_api_key(record.id)
        assert manager.authenticate_api_key_to_jwt(raw) is None

    def test_the_bridged_token_inherits_the_key_role_not_admin(
        self, manager,
    ):
        _, raw = manager.create_api_key(name="ci", role=Role.VIEWER)
        pair = manager.authenticate_api_key_to_jwt(raw)
        assert pair is not None
        claims = manager.validate_jwt(pair.access_token)
        assert claims is not None and claims.role is Role.VIEWER

    def test_the_raw_key_is_not_recoverable_from_storage(self, manager):
        """A stored key should be a hash. If the raw value can be read
        back, a leaked artifact store is a leaked credential."""
        record, raw = manager.create_api_key(name="ci", role=Role.ENGINEER)
        serialized = record.model_dump_json()
        assert raw not in serialized

    def test_validation_is_timing_safe(self):
        """The project convention is `compare_digest` for every secret
        comparison. Checked against the source, since a timing property
        cannot be asserted from behaviour in a unit test."""
        source = pathlib.Path(
            "src/tpllm/workflows/security_manager.py"
        ).read_text()
        validate = source[source.index("def validate_api_key"):]
        validate = validate[:validate.index("\n    def ")]
        assert "compare_digest" in validate, (
            "api key validation must not compare secrets with =="
        )


# ── The boundary nobody had checked ──────────────────────────────────


class TestTheAuthenticatedSurfaceIsEnumerated:
    """22 of 28 routes enforce nothing. That may be the right answer for
    a single-operator local tool; it is not the right answer to leave
    unwritten while advertising JWT authentication."""

    def _routes(self) -> list[tuple[str, str, bool]]:
        """Routes and whether each enforces authentication.

        Parsed with the AST rather than a regex window over the source.
        The first version read a fixed number of characters after each
        handler and truncated the longer bodies, reporting six guarded
        routes where there are three. A heuristic that undercounts an
        open surface is worse than no count.
        """
        import ast
        import inspect

        import tpllm.dashboard as dashboard

        tree = ast.parse(inspect.getsource(dashboard))
        found: list[tuple[str, str, bool]] = []
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            decorated = [
                (decorator.func.attr.upper(), decorator.args[0].value)
                for decorator in node.decorator_list
                if isinstance(decorator, ast.Call)
                and isinstance(decorator.func, ast.Attribute)
                and isinstance(decorator.func.value, ast.Name)
                and decorator.func.value.id == "app"
                and decorator.args
                and isinstance(decorator.args[0], ast.Constant)
                and decorator.func.attr != "middleware"
            ]
            if not decorated:
                continue
            calls = {
                call.func.id for call in ast.walk(node)
                if isinstance(call, ast.Call)
                and isinstance(call.func, ast.Name)
            }
            enforced = bool(calls & {"require_auth", "require_permission"})
            for verb, path in decorated:
                found.append((verb, path, enforced))
        return found

    def test_the_route_walker_finds_the_api(self):
        routes = self._routes()
        assert len(routes) >= 20, (
            f"only found {len(routes)} routes; the walker has stopped "
            f"checking anything"
        )

    def test_the_open_set_matches_what_the_module_declares(self):
        from tpllm.dashboard import UNAUTHENTICATED_READ_ROUTES

        open_now = sorted({
            path for _verb, path, enforced in self._routes()
            if not enforced
        })
        assert open_now == sorted(UNAUTHENTICATED_READ_ROUTES), (
            "the set of unauthenticated routes changed. Adding one is a "
            "decision: update UNAUTHENTICATED_READ_ROUTES and say why in "
            "docs/threat-model.md, or put the route behind require_auth."
        )

    def test_every_mutating_route_is_guarded(self):
        """Reads being open is a documented trade. Writes being open
        would be a different thing entirely."""
        # The authentication endpoints are necessarily open: you
        # cannot require a token in order to obtain one, and refresh
        # authenticates with the refresh token it is handed.
        issuing = {"/api/auth/token", "/api/auth/refresh"}
        offenders = [
            f"{verb} {path}"
            for verb, path, enforced in self._routes()
            if verb in {"POST", "PUT", "DELETE"} and not enforced
            and path not in issuing
        ]
        assert offenders == [], (
            f"{offenders} change state without a credential"
        )

    def test_the_app_warns_that_reads_are_open(self):
        """Said at startup by whatever entry point built the app,
        because the Dockerfile and deploy.sh call uvicorn directly with
        --host 0.0.0.0 and never go through the CLI."""
        pytest.importorskip("fastapi")
        import logging

        from tpllm.config import TPLLMConfig
        from tpllm.dashboard import create_app

        records: list[logging.LogRecord] = []

        class Capture(logging.Handler):
            def emit(self, record):
                records.append(record)

        logger = logging.getLogger("tpllm.dashboard")
        handler = Capture()
        logger.addHandler(handler)
        try:
            create_app(TPLLMConfig())
        finally:
            logger.removeHandler(handler)

        warnings = [
            r for r in records
            if r.levelno >= logging.WARNING
            and "without authentication" in r.getMessage()
        ]
        assert warnings, "the app does not say that its reads are open"
        assert getattr(warnings[0], "open_read_routes", 0) >= 24
