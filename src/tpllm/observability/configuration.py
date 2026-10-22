"""Turning logging on, which only an application may do.

One function, and it is called from exactly one place: the CLI. Every
module inside ``tpllm`` emits through ``logging.getLogger(__name__)``
and configures nothing, so embedding TPLLM in a larger program leaves
that program's logging setup alone.

The configuration is scoped to the ``tpllm`` logger rather than the root
logger for the same reason. A library that reconfigures root decides how
its host's unrelated modules log, which is not its decision to make.
"""

from __future__ import annotations

import logging
import sys
from enum import Enum
from typing import IO

from tpllm.observability.formatting import (
    HumanReadableFormatter,
    JsonLinesFormatter,
)

#: The logger tree this package owns.
ROOT_LOGGER_NAME = "tpllm"

#: Marks the handler this function installed, so a second call replaces
#: it instead of stacking a duplicate — which would double every line.
_HANDLER_TAG = "tpllm-observability"


class LogFormat(str, Enum):
    TEXT = "text"
    JSON = "json"


LOG_FORMAT_CHOICES = tuple(f.value for f in LogFormat)

#: Levels a caller may name, plus ``off``. ``off`` exists because the
#: CLI's own reporting is the primary interface; logs are for when
#: something needs explaining, and silence is a legitimate default.
_LEVELS = {
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "warning": logging.WARNING,
    "warn": logging.WARNING,
    "error": logging.ERROR,
    "critical": logging.CRITICAL,
    "off": logging.CRITICAL + 10,
}

LOG_LEVEL_CHOICES = ("debug", "info", "warning", "error", "critical", "off")

#: Quiet by default: the CLI already says what it did, and a default
#: that buries that in process chatter would make the tool worse.
DEFAULT_LOG_LEVEL = "warning"
DEFAULT_LOG_FORMAT = LogFormat.TEXT.value


def resolve_level(level: str | int) -> int:
    """Translate a level name to its numeric value."""
    if isinstance(level, int):
        return level
    try:
        return _LEVELS[str(level).strip().lower()]
    except KeyError:
        raise ValueError(
            f"Unknown log level '{level}'; expected one of "
            f"{', '.join(LOG_LEVEL_CHOICES)}."
        ) from None


def configure_logging(
    *,
    level: str | int = DEFAULT_LOG_LEVEL,
    log_format: str | LogFormat = DEFAULT_LOG_FORMAT,
    stream: IO[str] | None = None,
) -> logging.Handler:
    """Install TPLLM's log handler and return it.

    Logs go to stderr, so ``tpllm runtime export > history.json``
    produces clean JSON on stdout while the operator still sees what the
    process was doing.

    Safe to call more than once: the previous handler installed by this
    function is removed first, which matters for tests and for any host
    that initializes twice.
    """
    chosen = LogFormat(
        log_format.value if isinstance(log_format, LogFormat) else log_format
    )
    numeric = resolve_level(level)

    logger = logging.getLogger(ROOT_LOGGER_NAME)
    for existing in list(logger.handlers):
        if getattr(existing, "_tpllm_tag", None) == _HANDLER_TAG:
            logger.removeHandler(existing)
            existing.close()

    handler = logging.StreamHandler(stream or sys.stderr)
    handler.setFormatter(
        JsonLinesFormatter() if chosen is LogFormat.JSON
        else HumanReadableFormatter()
    )
    handler.setLevel(numeric)
    handler._tpllm_tag = _HANDLER_TAG  # type: ignore[attr-defined]

    logger.addHandler(handler)
    logger.setLevel(numeric)
    # The host's root configuration is not ours to feed; our own handler
    # is the one place these lines come out.
    logger.propagate = False
    return handler
