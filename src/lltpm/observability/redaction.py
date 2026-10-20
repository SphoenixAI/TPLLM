"""Format-time redaction of credential material.

Deliberately duplicated rather than shared. ``persistence`` owns the
same vocabulary for the journal, and the two layers must not import each
other — durable storage stays free of first-party dependencies, and the
logging layer stays importable from anywhere. A test asserts the two
vocabularies agree on the shared core, which is cheaper than an import
edge that would compromise both boundaries.

The behaviour differs on purpose:

- The journal **refuses** secret-looking material. A credential in
  append-only history is permanent, so the write has to fail.
- A log line **redacts** it. Raising here would take down the operation
  the line was merely describing, which trades a logging problem for a
  production one.
"""

from __future__ import annotations

import re
from typing import Any

REDACTED = "[redacted]"

#: Substrings that make a field name credential-bearing. Matched
#: case-insensitively anywhere in the name, so ``slack_bot_token`` and
#: ``X-Authorization`` are both caught.
SECRET_KEY_FRAGMENTS: tuple[str, ...] = (
    "api_key",
    "apikey",
    "authorization",
    "cookie",
    "credential",
    "passphrase",
    "password",
    "private_key",
    "secret",
    "signing_key",
    "token",
)

#: Value prefixes that identify a credential regardless of field name —
#: the case where something lands in a field called ``detail``.
SECRET_VALUE_MARKERS: tuple[str, ...] = (
    "-----begin ",
    "aws_",
    "bearer ",
    "ghp_",
    "gho_",
    "ghs_",
    "github_pat_",
    "sk-",
    "xapp-",
    "xoxb-",
    "xoxp-",
)

#: A compact JWT: three base64url segments, the first being a JSON
#: header, which in practice always begins ``eyJ``.
_JWT = re.compile(r"^eyJ[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]*$")

#: Beyond this, a nested structure is summarized rather than walked. A
#: log formatter must terminate on whatever it is handed, including
#: something unexpectedly deep or self-referential.
MAX_DEPTH = 6


def is_secret_field_name(name: str) -> bool:
    """Whether a field name says its value is a credential."""
    lowered = str(name).lower()
    return any(fragment in lowered for fragment in SECRET_KEY_FRAGMENTS)


def looks_like_secret_value(value: str) -> bool:
    """Whether a string looks like credential material on its own."""
    stripped = value.strip()
    lowered = stripped.lower()
    if any(lowered.startswith(marker) for marker in SECRET_VALUE_MARKERS):
        return True
    return bool(_JWT.match(stripped))


def redact_value(value: Any, *, depth: int = 0) -> Any:
    """Return ``value`` with any credential material replaced.

    Walks dicts, lists, tuples and sets. Non-JSON scalars are left to
    the caller's encoder; this function's only job is removing secrets,
    not serializing.
    """
    if depth >= MAX_DEPTH:
        return f"[depth>{MAX_DEPTH}]"

    if isinstance(value, dict):
        return {
            str(key): (
                REDACTED if is_secret_field_name(key)
                else redact_value(item, depth=depth + 1)
            )
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple, set)):
        return [redact_value(item, depth=depth + 1) for item in value]
    if isinstance(value, str) and looks_like_secret_value(value):
        return REDACTED
    return value


def redact_fields(fields: dict[str, Any]) -> dict[str, Any]:
    """Redact a flat field mapping by name and by value."""
    return {
        name: (
            REDACTED if is_secret_field_name(name)
            else redact_value(value)
        )
        for name, value in fields.items()
    }
