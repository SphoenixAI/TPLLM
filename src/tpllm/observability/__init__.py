"""Operational logging for TPLLM.

The journal and the log answer different questions, and collapsing them
would damage both:

- **The journal** answers *what happened to the organization*. Durable,
  ordered, hash-chained, replayable, and deliberately not a record of
  every internal call. If replaying it would not change what the
  organization is or believes, it does not belong there.
- **The log** answers *what the process did*. Which engine ran, why a
  recompute was skipped, how long an append took, which revision was
  promoted, what a replay suppressed. Transient, cheap, and safe to lose.

So this package is not a second journal. Nothing here is authoritative,
nothing here is replayed, and losing a log line changes no conclusion.

Three rules shape the design:

**Libraries do not configure logging.** Every module inside ``tpllm``
emits through the standard library — ``logging.getLogger(__name__)`` and
``extra={...}`` — and imports nothing from this package. Only an
application entry point (the CLI) calls :func:`configure_logging`. That
is ordinary stdlib discipline, and it is also what keeps the
architecture boundaries intact: ``persistence`` imports no first-party
package at all, so it could not import a logging helper even if one were
convenient.

**Structure is carried as fields, not prose.** Call sites pass
correlation ids — ``journal_sequence``, ``revision_id``,
``causal_event_id``, ``organization_id``, ``project_id`` — as ``extra``
keys. :class:`JsonLinesFormatter` promotes every non-standard record
attribute to a top-level JSON field, so one grep answers "what else
happened in revision-7?" without parsing a sentence.

**Credentials are redacted at format time, and redaction never raises.**
The journal *refuses* secret-looking material, because a credential in
durable history is permanent. A log line must not take down the
operation it was describing, so the same material is replaced with a
marker instead.

What the log deliberately does not carry: evidence content, message
text, or anything resembling individual activity. TPLLM models
organizational mechanics, not people, and an observability layer is
exactly where that invariant would quietly erode first.
"""

from tpllm.observability.configuration import (
    DEFAULT_LOG_FORMAT,
    DEFAULT_LOG_LEVEL,
    LOG_FORMAT_CHOICES,
    LOG_LEVEL_CHOICES,
    LogFormat,
    configure_logging,
    resolve_level,
)
from tpllm.observability.fields import (
    RESERVED_RECORD_ATTRIBUTES,
    STRUCTURED_FIELD_NAMES,
    extra_fields,
)
from tpllm.observability.formatting import (
    HumanReadableFormatter,
    JsonLinesFormatter,
)
from tpllm.observability.redaction import (
    REDACTED,
    SECRET_KEY_FRAGMENTS,
    SECRET_VALUE_MARKERS,
    is_secret_field_name,
    redact_fields,
    redact_value,
)

__all__ = [
    "DEFAULT_LOG_FORMAT",
    "DEFAULT_LOG_LEVEL",
    "LOG_FORMAT_CHOICES",
    "LOG_LEVEL_CHOICES",
    "REDACTED",
    "RESERVED_RECORD_ATTRIBUTES",
    "SECRET_KEY_FRAGMENTS",
    "SECRET_VALUE_MARKERS",
    "STRUCTURED_FIELD_NAMES",
    "HumanReadableFormatter",
    "JsonLinesFormatter",
    "LogFormat",
    "configure_logging",
    "extra_fields",
    "is_secret_field_name",
    "redact_fields",
    "redact_value",
    "resolve_level",
]
