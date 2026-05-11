"""ParSEval symbolic module — branch-coverage-driven test-database generation.

Public API::

    from parseval.symbolic import SymbolicEngine, CoverageThresholds

    engine = SymbolicEngine(instance, sql, dialect="sqlite")
    result = engine.generate(thresholds=CoverageThresholds(atom_null=0))
    print(result.coverage, result.rows_generated)
"""

from .constraints import ConstraintGenerator, SolverConstraint
from .engine import SymbolicEngine
from .evaluator import PlanEvaluator, decompose_atoms
from .infeasibility import is_infeasible
from .types import (
    AtomObservation,
    BranchNode,
    BranchTree,
    BranchType,
    CoverageTarget,
    CoverageThresholds,
    GenerationResult,
)

__all__ = [
    "AtomObservation",
    "BranchNode",
    "BranchTree",
    "BranchType",
    "ConstraintGenerator",
    "CoverageTarget",
    "CoverageThresholds",
    "GenerationResult",
    "PlanEvaluator",
    "SolverConstraint",
    "SymbolicEngine",
    "decompose_atoms",
    "is_infeasible",
]
