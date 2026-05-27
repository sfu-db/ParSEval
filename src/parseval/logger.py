"""ParSEval logging — configurable, structured execution tracking.

Usage::

    from parseval.logger import get_logger, configure

    # Configure once at startup
    configure(level="INFO", log_file="parseval.log")

    # Get loggers by component
    log = get_logger("engine")
    log.info("Generation started", extra={"sql": sql, "dialect": "sqlite"})

    # Or use the module-level convenience
    from parseval.logger import log
    log.info("ParSEval initialized")
"""

import logging
import sys
from pathlib import Path
from typing import Optional


_FORMAT = "[%(asctime)s] %(levelname)s [%(name)s]: %(message)s"
_FORMAT_VERBOSE = "[%(asctime)s] %(levelname)s [%(name)s] [%(filename)s:%(lineno)d]: %(message)s"

# Root logger name for all ParSEval loggers
ROOT = "parseval"

# Sub-logger names
COMPONENTS = {
    "engine": "Symbolic engine execution",
    "solver": "SMT/CSP solver invocations",
    "speculate": "Speculative data generation",
    "plan": "Query plan construction",
    "instance": "Instance row management",
    "db": "Database operations (dump/execute)",
    "coverage": "Branch coverage tracking",
}

_configured = False


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
    global _configured

    root = logging.getLogger(ROOT)
    root.handlers.clear()
    root.propagate = False

    if quiet:
        log_level = logging.WARNING
    else:
        log_level = getattr(logging, level.upper(), logging.INFO)

    root.setLevel(log_level)

    fmt = _FORMAT_VERBOSE if verbose else _FORMAT
    formatter = logging.Formatter(fmt)

    # Console handler
    console = logging.StreamHandler(sys.stderr)
    console.setLevel(log_level)
    console.setFormatter(formatter)
    root.addHandler(console)

    # File handler (if specified)
    if log_file:
        Path(log_file).parent.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(log_file)
        fh.setLevel(logging.DEBUG)  # File always gets everything
        fh.setFormatter(logging.Formatter(_FORMAT_VERBOSE))
        root.addHandler(fh)

    _configured = True


def get_logger(component: str = "") -> logging.Logger:
    """Get a ParSEval logger for a specific component.

    Parameters
    ----------
    component : str
        Component name (e.g. "engine", "solver", "db").
        Empty string returns the root parseval logger.
    """
    if not _configured:
        # Auto-configure with defaults on first use
        configure(level="WARNING")

    if component:
        return logging.getLogger(f"{ROOT}.{component}")
    return logging.getLogger(ROOT)


# Module-level convenience logger
log = logging.getLogger(ROOT)
