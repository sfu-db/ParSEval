"""Instance-guided decision coverage for U-expressions."""

from .measure import measure_coverage, target_is_covered
from .explore import (
    CoverageExplorer,
    CoverageSnapshot,
    explore_paths,
    next_paths,
    observed_paths,
)
from .model import (
    CoverageOutcome,
    CoverageReport,
    CoverageSite,
    CoverageStatus,
    CoverageTarget,
    WitnessedObligation,
)
from .scopes import unsupported_scopes
from .tracker import CoverageTracker

__all__ = (
    "CoverageExplorer",
    "CoverageOutcome",
    "CoverageReport",
    "CoverageSite",
    "CoverageSnapshot",
    "CoverageStatus",
    "CoverageTarget",
    "CoverageTracker",
    "WitnessedObligation",
    "explore_paths",
    "measure_coverage",
    "next_paths",
    "observed_paths",
    "target_is_covered",
    "unsupported_scopes",
)
