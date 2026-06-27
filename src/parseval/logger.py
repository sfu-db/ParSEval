"""ParSEval logging — configurable execution tracking.

Usage::

    from parseval.logger import configure, log

    # Optional: configure once at startup
    configure(level="INFO", log_file="parseval.log")

    # Use the module-level logger
    log.info("Generation started")

    # Or get one directly
    import logging
    logging.getLogger("parseval").info("hello")
"""

import logging
import sys
from pathlib import Path
from typing import Optional


_FORMAT = "[%(asctime)s] %(levelname)s [%(name)s]: %(message)s"
_FORMAT_VERBOSE = "[%(asctime)s] %(levelname)s [%(name)s] [%(filename)s:%(lineno)d]: %(message)s"

ROOT = "parseval"

# Default: WARNING to stderr so users see problems but not noise.
_root = logging.getLogger(ROOT)
_root.setLevel(logging.WARNING)
_root.propagate = False
if not _root.handlers:
    _root.addHandler(logging.StreamHandler(sys.stderr))


def configure(
    *,
    level: str = "INFO",
    log_file: Optional[str] = None,
    verbose: bool = False,
    quiet: bool = False,
) -> None:
    """Configure ParSEval logging.

    Parameters
    ----------
    level : str
        Logging level: "DEBUG", "INFO", "WARNING", "ERROR".
    log_file : str, optional
        Path to write logs. If None, logs go to stderr.
    verbose : bool
        If True, include filename and line number in output.
    quiet : bool
        If True, suppress all output below WARNING.
    """
    _root.handlers.clear()

    if quiet:
        log_level = logging.WARNING
    else:
        log_level = getattr(logging, level.upper(), logging.INFO)

    _root.setLevel(log_level)

    fmt = _FORMAT_VERBOSE if verbose else _FORMAT
    formatter = logging.Formatter(fmt)

    console = logging.StreamHandler(sys.stderr)
    console.setLevel(log_level)
    console.setFormatter(formatter)
    _root.addHandler(console)

    if log_file:
        Path(log_file).parent.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(log_file)
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(logging.Formatter(_FORMAT_VERBOSE))
        _root.addHandler(fh)


log = logging.getLogger(ROOT)
