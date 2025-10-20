from __future__ import annotations
import random
from collections import defaultdict
from typing import Dict, Set, Optional, List, Callable, Any, Tuple, Union
import logging
import random, time
import string
from datetime import datetime, timedelta
from ..dtype import DATATYPE, DataType
import src.parseval.plan.expression as sql_exp
from collections import deque


logger = logging.getLogger(__name__)


# -------------------------
# JoinLinker (union-find)
# -------------------------


class JoinLinker:
    """
    Tracks columns linked by equality using union-find.
    Uses ColumnRef.quality_name as variables.
    """

    def __init__(self):
        self.parent: Dict[str, str] = {}
        self.clusters: Dict[str, Set[str]] = {}

    def find(self, column: str) -> str:
        if column not in self.parent:
            self.parent[column] = column
            self.clusters[column] = {column}
        if self.parent[column] != column:
            self.parent[column] = self.find(self.parent[column])
        return self.parent[column]

    def union(self, a: str, b: str):
        ra = self.find(a)
        rb = self.find(b)
        if ra == rb:
            return
        # keep deterministic: smaller string as root
        cluster1 = self.clusters[ra]
        cluster2 = self.clusters[rb]
        if len(cluster1) < len(cluster2):
            ra, rb = rb, ra
            cluster1, cluster2 = cluster2, cluster1

        cluster1 |= cluster2
        for c in cluster2:
            self.parent[c] = ra
        self.clusters[ra] = cluster1
        del self.clusters[rb]

    def groups(self) -> List[Set[str]]:
        roots: Dict[str, Set[str]] = {}
        for v in list(self.parent.keys()):
            r = self.find(v)
            roots.setdefault(r, set()).add(v)
        return list(roots.values())


class DomainSpec:
    """
    This is a static specification of a logical column domain from database schema.
    """

    def __init__(
        self,
        table_name,
        column_name,
        datatype,
        min_val=None,
        max_val=None,
        choices=None,
        unique=False,
        nullable=False,
        default=None,
        checks: Optional[List[Callable[[Any], bool]]] = None,
        generated: List[Any] = None,
    ):
        self.table_name = table_name
        self.column_name = column_name
        self.datatype = DataType.build(datatype)
        self.min_val = min_val
        self.max_val = max_val
        self.choices = choices or []
        self.unique = unique
        self.nullable = nullable
        self.default = default
        self.checks = checks
        self.generated = generated or []

    def __repr__(self):
        return (
            f"DomainSpec({self.table_name}.{self.column_name}, {self.datatype.dtype})"
        )

    @property
    def qualified_name(self):
        return f"{self.table_name}.{self.column_name}"


class ValuePool:
    """
    Holds a set of produced values for a logical domain.
    If 'unique' True, values are intended to be unique (PK).
    'locked' indicates pool is referenced by dependents (FK linking)
    so expansions are more controlled.
    """

    def __init__(self, domain: DomainSpec, datatype=None):
        self.domain = domain
        self.unique = domain.unique
        self.values: Set[Any] = set()
        self._locked = False
        self.choices: List[Any] = []
        self.excluded: Set[Any] = set()
        self.min_val = domain.min_val
        self.max_val = domain.max_val
        self.datatype = DataType.build(datatype) if datatype else domain.datatype

    def add_value(self, v: Any):
        """Add a generated value to the pool."""
        if v not in self.excluded:
            self.values.add(v)

    def apply_constraints(self, constraint):
        raise NotImplementedError

    def add_excluded(self, v: Any):
        self.excluded.add(v)

    def propagate_bounds(self, min_val=None, max_val=None):
        if min_val is not None:
            self.min_val = max(self.min_val or min_val, min_val)
        if max_val is not None:
            self.max_val = min(self.max_val or max_val, max_val)

    def mark_locked(self):
        """Lock the pool when another column depends on it (e.g., FK reference)."""
        if not self._locked:
            self._locked = True
            logger.info(f"🔒 Pool locked with {len(self.values)} values")

    def generate_new_value(self) -> Any:
        """Generate a new value for the pool based on domain spec."""
        if self.domain.default is not None:
            return self.domain.default
        datatype = self.datatype
        attempts = 0
        while attempts < 2000:
            attempts += 1

            if datatype.is_numeric():
                value = (
                    self._sample_int()
                    if datatype.is_integer()
                    else self._sample_float()
                )
            elif datatype.is_string():
                value = self._sample_str()
            elif datatype.is_boolean():
                value = self._sample_bool()
            elif datatype.is_datetime():
                value = self._sample_date()
            else:
                value = self._sample_int()
            if value in self.excluded:
                continue
            if self.unique and value in self.values:
                continue
            self.add_value(value)
            return value

        raise RuntimeError(
            f"generate_new_value: exhausted attempts for {self.domain.qualified}"
        )

    def _sample_int(self):
        low = self.min_val if self.min_val is not None else 0
        high = self.max_val if self.max_val is not None else 10000
        value = random.randint(low, high)

        return value

    def _sample_float(self):
        low = self.min_val if self.min_val is not None else 0.0
        high = self.max_val if self.max_val is not None else 1.0
        value = round(random.uniform(low, high), 4)

        return value

    def _sample_str(self):
        if self.choices:
            return random.choice(self.choices)
        chars = string.ascii_letters + string.digits
        value = "".join(random.choices(chars, 10))

        return value

    def _sample_bool(self):
        return random.choice([True, False])

    def _sample_date(self):
        end_date = datetime.now()
        start_date = end_date - timedelta(days=365 * 10)
        days_between = (end_date - start_date).days
        random_days = random.randint(0, days_between)
        value = start_date + timedelta(days=random_days)
        return value


class ColumnDomainPool:
    def __init__(self):
        self.domains: Dict[str, DomainSpec] = {}
        self.value_pools: Dict[str, ValuePool] = {}
        self.fk_links: Dict[str, str] = {}

    def register_domain(self, domain: DomainSpec):
        """Register a column domain."""
        key = domain.qualified_name
        self.domains[key] = domain

    def get_value_pool(
        self, table_name: str, column_name: str, constraints: List["Constraint"]
    ) -> ValuePool:
        key = f"{table_name}.{column_name}"
        if key not in self.domains:
            raise ValueError(f"Domain for column {key} not registered")

        domain = self.domains[key]
        pool = ValuePool(domain)

        # Apply constraints to the pool
        for constraint in constraints:
            pool.apply_constraints(constraint)
        return pool

    def link_foreign_key(self, fk_col: str, referenced_col: str):
        """
        Link single-column FK to reference another column's value pool.

        Args:
            fk_col: Foreign key column name (e.g., "A.b_id")
            referenced_col: Referenced column name (e.g., "B.id")
                           Must have unique=True constraint
        """
        if referenced_col not in self.value_pools:
            raise ValueError(
                f"Cannot link: referenced column {referenced_col} not found"
            )

        if fk_col not in self.domains:
            raise ValueError(f"Foreign key column {fk_col} not registered")

        # Validate that referenced column is unique
        ref_domain = self.domains[referenced_col]
        if not ref_domain.unique:
            raise ValueError(
                f"Cannot create foreign key: referenced column {referenced_col} "
                f"must be UNIQUE or PRIMARY KEY. Current: unique={ref_domain.unique}"
            )

        # Store the link
        self.fk_links[fk_col] = referenced_col
        # Lock the referenced pool
        self.value_pools[referenced_col].mark_locked()

    def generate_value(self, column_name):
        """Generate a new value for the specified column domain."""
        if column_name not in self.value_pools:
            raise ValueError(f"Column {column_name} not registered in solver")

        if column_name in self.fk_links:
            return self._generate_fk_values(column_name)

        pool = self.value_pools[column_name]
        value = pool.generate_new_value()
        return value


# class Variable:
#     pass

Variable = sql_exp.ColumnRef


class CSPConstraint:
    def __init__(self, variables: List[Any]):
        self.variables = variables

    def is_satisfied(self, assignment: Dict[Any, Any]) -> bool:
        raise NotImplementedError("Must override is_satisfied")

    def propagate(self) -> bool:
        raise NotImplementedError("Must override propagate")

    def evaluate_on_row(self, row: Dict[str, Any]) -> Optional[bool]:
        raise NotImplementedError("Must override evaluate_on_row")


class CSPSolver:
    """
    A simple CSP solver for column domains with constraints.
    """

    def __init__(self, pool_mgr: Optional[ColumnDomainPool] = None):
        self.pool_mgr: ColumnDomainPool = pool_mgr or ColumnDomainPool()
        self.variables: List[Variable] = []
        self.constraints: List[CSPConstraint] = []

        self.var_to_constraints = defaultdict(list)
        self.var_to_columnref: Dict[Variable, sql_exp.ColumnRef] = {}

        self.vars_to_constraints: Dict[Tuple[str, str], List[Any]] = {}

    def register_domain(self, domain: DomainSpec):
        self.pool_mgr.register_domain(domain)

    def add_constraint(self, constraint: CSPConstraint):
        """Add a constraint to the solver."""
        self.constraints.append(constraint)
        columns = constraint.find_all(sql_exp.ColumnRef)
        for col in columns:
            self.add_variable(col.qualified_name)
            self.var_to_columnref[col.qualified_name] = col

        for qi, qj in constraint.related_pairs():
            self.pair_to_constraints.setdefault((qi, qj), []).append(constraint)

    def _generate_initial_arcs(self) -> List[Tuple[str, str, CSPConstraint]]:
        arcs = []
        for (qi, qj), cons_list in self.pair_to_constraints.items():
            for c in cons_list:
                arcs.append((qi, qj, c))
        return arcs

        # for c in constraints:
        #     for v in c.variables:
        #         self.var_to_constraints[v.name].append(c)

        # self._update_valuepool(constraint)

    def _update_valuepool(self, constraint):

        casts = constraint.find_all(sql_exp.Cast)
        for cast in casts:
            if isinstance(cast.operand, sql_exp.ColumnRef):
                col = cast.operand
                value_pool = self.column_pool.value_pools[col.quality_name]
                value_pool.datatype = DataType.build(cast.to)

        if isinstance(constraint, sql_exp.BinaryOp):
            left, right = constraint.left, constraint.right
            if isinstance(right, sql_exp.Literal):
                value_pool = self.column_pool.value_pools[left.quality_name]
                if constraint.op in ("=", "=="):
                    value_pool.add_value(right.value)
                elif constraint.op in ("!=", "<>"):
                    value_pool.add_excluded(right.value)
                elif constraint.op in (">", ">="):
                    value_pool.propagate_bounds(min_val=right.value)
                elif constraint.op in ("<", "<="):
                    value_pool.propagate_bounds(max_val=right.value)
            # elif isinstance(right, sql_exp.ColumnRef) and isinstance(
            #     left, sql_exp.Literal
            # ):
            #     value_pool = self.column_pool.value_pools[right.quality_name]
            #     if constraint.op in ("=", "=="):
            #         value_pool.add_excluded(left.value)
            #     elif constraint.op in (">", ">="):
            #         value_pool.propagate_bounds(min_val=left.value)
            #     elif constraint.op in ("<", "<="):
            #         value_pool.propagate_bounds(max_val=left.value)

    def add_variable(self, variable: Variable):
        """Add a variable to the problem."""
        self.variables.append(variable)

    def propagate(self) -> bool:
        """
        Apply constraint propagation until fixpoint.
        Returns True if successful, False if inconsistency detected.
        """
        queue = deque(self._generate_initial_arcs())
        while queue:
            xi, xj, cons = queue.popleft()
            revised = self._revise(xi, xj, cons)
            if revised:
                columnref = self.var_to_columnref[xi]
                pool_xi = self.pool_mgr.get_value_pool(
                    columnref.metadata["table"], columnref.name
                )
                if not pool_xi or not pool_xi.values:
                    raise RuntimeError(
                        f"Domain {xi} became empty during AC-3 (constraint {cons})"
                    )
                # enqueue neighbors (xk, xi) for all xk != xj that mention xi
                for (a, b), cons_list in self.pair_to_constraints.items():
                    if b == xi and a != xj:
                        for c in cons_list:
                            queue.append((a, xi, c))

    def _revise(self, xi: str, xj: str, cons: CSPConstraint) -> bool:
        """
        For each vi in domain(xi), check whether there exists vj in domain(xj) and
        assignments to other vars such that cons is satisfied. If none, remove vi.
        Returns True if domain(xi) reduced.
        """
        col_xi = self.var_to_columnref[xi]
        col_xj = self.var_to_columnref[xj]

        pool_i = self.pool_mgr.get_pool(col_xi.metadata["table"], col_xi.name)
        pool_j = self.pool_mgr.get_pool(col_xj.matadata["table"], col_xj.name)
        if pool_i is None or pool_j is None:
            return False

        removed_any = False
        values_i = list(pool_i.values)  # snapshot

        for vi in values_i:
            has_support = False

            # other vars in constraint excluding xi,xj
            other_vars = [
                v.qualified_name
                for v in cons.find_all(sql_exp.ColumnRef)
                if v.qualified_name not in (xi, xj)
            ]

            if not other_vars:
                # iterate all vj
                for vj in pool_j.values:
                    assignment = {xi: vi, xj: vj}
                    res = cons.evaluate_on_row(assignment)
                    if res is True:
                        has_support = True
                        break

        if not has_support:
            pool_i.add_excluded(vi)
            removed_any = True

        return removed_any

    def _backtrack(
        self, assignment: Dict[Variable, Any]
    ) -> Optional[Dict[Variable, Any]]:
        """Backtracking search with constraint propagation."""
        if len(assignment) == len(self.variables):
            return assignment

        # Select unassigned variable with smallest domain (MRV heuristic)
        unassigned = [v for v in self.variables if v not in assignment]
        var = min(unassigned, key=lambda v: len(v.domain))

        for value in list(var.domain):
            if self._is_consistent(var, value, assignment):
                assignment[var] = value
                # Save current state
                saved_domains = {v: v.domain.copy() for v in self.variables}

                # Forward checking: reduce domains
                var.domain = {value}
                if self.propagate():
                    result = self._backtrack(assignment)
                    if result is not None:
                        return result

                # Restore domains
                for v in self.variables:
                    v.domain = saved_domains[v]

                del assignment[var]

        return None

    def _is_consistent(
        self, var: Variable, value: Any, assignment: Dict[Variable, Any]
    ) -> bool:
        """Check if assigning value to var is consistent with constraints."""
        assignment[var] = value
        consistent = all(c.is_satisfied(assignment) for c in self.constraints)
        del assignment[var]
        return consistent
