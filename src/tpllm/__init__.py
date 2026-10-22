"""TPLLM — AI-powered Technical Program Manager agent."""

import logging
from importlib.metadata import PackageNotFoundError, version as _package_version

try:
    # The version lives in pyproject.toml and nowhere else; a literal
    # here only ever drifts behind it.
    __version__ = _package_version("tpllm")
except PackageNotFoundError:  # running from a source tree, not installed
    __version__ = "0.0.0+unknown"

# A library attaches a null handler to its own logger and configures
# nothing else: without this, a warning from inside TPLLM would be
# printed by logging's last-resort handler into the output of whatever
# program embedded it. `tpllm.observability.configure_logging` is what
# an application calls to actually see these lines.
logging.getLogger(__name__).addHandler(logging.NullHandler())
