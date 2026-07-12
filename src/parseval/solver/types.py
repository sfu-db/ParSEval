"""Shared types for the solver module."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Mapping, Optional, Protocol, Set, Tuple

from sqlglot import exp
from sqlglot.generator import Generator

from parseval.dtype import DataType, TypeFamily, type_family
from parseval.domain.value_space import ValueSpace

Status = Literal["sat", "unsat", "unknown"]


class SolverVar(exp.Expression):
    """Opaque solver variable as a sqlglot AST leaf.

    Construct with ``SolverVar(key=..., dtype=..., meta=...)``. Identity is the
    ``key`` string stored in ``this`` (hash/eq), so ``copy()`` nodes with the
    same key compare equal. sqlglot's node-kind ``Expression.key`` remains
    ``"solvervar"``; use :attr:`var_key` (or the constructor kwarg ``key``) for
    identity.
    """

    arg_types = {
        "this": True,  # identity key string
        "type": False,  # DataType
        "meta": False,
    }

    def __init__(
        self,
        *args: Any,
        key: str | None = None,
        dtype: DataType | None = None,
        meta: Mapping[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        if key is not None:
            kwargs.setdefault("this", key)
        if dtype is not None:
            kwargs.setdefault("type", dtype)
        if meta is not None:
            kwargs["meta"] = dict(meta)
        else:
            kwargs.setdefault("meta", {})
        if kwargs.get("type") is None:
            kwargs["type"] = DataType.build("TEXT")
        super().__init__(*args, **kwargs)

    @property
    def var_key(self) -> str:
        """Opaque solver identity (constructor ``key=`` / ``this``)."""
        this = self.this
        return this if isinstance(this, str) else str(this)

    @property
    def dtype(self) -> DataType:
        value = self.args.get("type")
        if isinstance(value, DataType):
            return value
        if value is None:
            return DataType.build("TEXT")
        return DataType.build(str(value))

    @property
    def meta(self) -> Mapping[str, Any]:
        value = self.args.get("meta")
        return value if isinstance(value, Mapping) else {}

    @property
    def display(self) -> str:
        return self.var_key

    def __hash__(self) -> int:
        return hash(self.var_key)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, SolverVar):
            return self.var_key == other.var_key
        return NotImplemented

    def sql(self, dialect: Any = None, **opts: Any) -> str:
        return f"SolverVar({self.var_key})"


def _generate_solver_var(self: Generator, expression: SolverVar) -> str:
    return f"SolverVar({expression.var_key})"


# Ensure pretty-print / copy paths that go through the generator work.
Generator.TRANSFORMS[SolverVar] = _generate_solver_var


@dataclass
class Problem:
    """Constraint problem for CSP/SMT backends."""

    constraints: List[exp.Expression] = field(default_factory=list)
    equalities: List[Tuple[SolverVar, SolverVar]] = field(default_factory=list)
    variables: Set[SolverVar] = field(default_factory=set)


@dataclass
class Result:
    """Outcome of a backend or orchestrator solve."""

    status: Status
    assignments: Dict[SolverVar, Any] = field(default_factory=dict)
    reason: str = ""

    @property
    def sat(self) -> bool:
        return self.status == "sat"


class Backend(Protocol):
    def solve(self, problem: Problem) -> Result: ...


def node_dtype(node: exp.Expression) -> Optional[DataType]:
    """Read dtype from a SolverVar leaf, else Expression.type if present."""
    if isinstance(node, SolverVar):
        return node.dtype
    dtype = getattr(node, "type", None)
    if dtype is None:
        return None
    if isinstance(dtype, DataType):
        return dtype
    try:
        return DataType.build(str(dtype))
    except Exception:
        return None


def collect_problem_variables(problem: Problem) -> Tuple[SolverVar, ...]:
    """Return every SolverVar referenced by a Problem in deterministic order."""
    variables: Set[SolverVar] = set(problem.variables)
    for expr in problem.constraints:
        variables.update(expr.find_all(SolverVar))
    for left, right in problem.equalities:
        variables.add(left)
        variables.add(right)
    return tuple(sorted(variables, key=lambda variable: variable.var_key))


__all__ = [
    "Backend",
    "Problem",
    "Result",
    "SolverVar",
    "Status",
    "TypeFamily",
    "ValueSpace",
    "collect_problem_variables",
    "node_dtype",
    "type_family",
]
