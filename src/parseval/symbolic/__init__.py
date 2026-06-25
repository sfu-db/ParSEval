"""ParSEval symbolic module — branch-coverage-driven test-database generation.

Public API::

    from parseval.symbolic import SymbolicEngine, CoverageThresholds

    engine = SymbolicEngine(instance, sql, dialect="sqlite")
    result = engine.generate(thresholds=CoverageThresholds(atom_null=0))
    print(result.coverage, result.rows_generated)
"""

from parseval.solver.unified import SolverConstraint
from .constraints import (
    ConstraintGenerator,
    PlausibleBranch,
    ConstraintGenerator,
    PlausiblePath,
)
from .engine import SymbolicEngine
from .evaluator import PlanEvaluator, decompose_atoms
from .types import (
    AtomObservation,
    BranchNode,
    BranchTree,
    BranchType,
    CoverageTarget,
    CoverageThresholds,
    GenerationResult,
    OperatorObligation,
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
    "OperatorObligation",
    "PlanEvaluator",
    "PlausibleBranch",
    "ConstraintGenerator",
    "PlausiblePath",
    "SolverConstraint",
    "SymbolicEngine",
    "decompose_atoms",
]
