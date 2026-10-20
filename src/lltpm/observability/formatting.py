"""Log formatters: one for machines, one for a person at a terminal.

Both promote whatever a call site attached through ``extra={...}`` to
first-class output, and both redact credential material on the way. The
redaction happens here, at format time, rather than at the call site:
a call site that forgets is the normal case, and a formatter that
remembers is the only version that holds.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from lltpm.observability.fields import record_fields
from lltpm.observability.redaction import redact_fields

#: Fixed leading keys, in this order, so a line is readable raw and a
#: diff between two runs is about content rather than key order.
_LEADING_KEYS = ("timestamp", "level", "logger", "message")


def _render(value: object) -> str:
    """Stringify a field without letting its own ``repr`` take the
    process down with it."""
    try:
        return str(value)
    except Exception:  # noqa: BLE001
        return "[unrenderable]"


def _timestamp(created: float) -> str:
    moment = datetime.fromtimestamp(created, tz=timezone.utc)
    return moment.isoformat(timespec="milliseconds").replace("+00:00", "Z")


class JsonLinesFormatter(logging.Formatter):
    """One JSON object per line, with structured fields promoted.

    JSON Lines rather than a single JSON document: a log is appended to
    and tailed, and a document would only be parseable once the process
    that wrote it had exited.
    """

    def format(self, record: logging.LogRecord) -> str:
        document: dict[str, object] = {
            "timestamp": _timestamp(record.created),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        for name, value in sorted(redact_fields(record_fields(record)).items()):
            # A field never displaces the fixed keys; a call site that
            # passes `message=` is a bug, not a redefinition.
            if name not in _LEADING_KEYS:
                document[name] = value

        if record.exc_info:
            document["exception"] = self.formatException(record.exc_info)
        if record.stack_info:
            document["stack"] = self.formatStack(record.stack_info)

        try:
            return json.dumps(document, default=str)
        except Exception as error:  # noqa: BLE001
            # A value whose own repr raises would otherwise propagate out
            # of whatever code happened to log. Losing the fields is
            # acceptable; losing the operation is not.
            return json.dumps({
                "timestamp": document["timestamp"],
                "level": document["level"],
                "logger": document["logger"],
                "message": document["message"],
                "log_error": (
                    f"fields were not serializable: {type(error).__name__}"
                ),
            })


class HumanReadableFormatter(logging.Formatter):
    """A compact single line for someone watching a terminal.

    Same fields, same redaction; only the encoding differs. Kept as the
    default because the first consumer of these logs is a person running
    ``lltpm runtime ingest`` and wondering what it did.
    """

    def format(self, record: logging.LogRecord) -> str:
        head = (
            f"{_timestamp(record.created)} {record.levelname:<7} "
            f"{record.name} {record.getMessage()}"
        )
        fields = redact_fields(record_fields(record))
        if fields:
            rendered = " ".join(
                f"{name}={_render(value)}"
                for name, value in sorted(fields.items())
            )
            head = f"{head} [{rendered}]"
        if record.exc_info:
            head = f"{head}\n{self.formatException(record.exc_info)}"
        return head
