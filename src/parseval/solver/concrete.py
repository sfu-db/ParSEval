import random
import string
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Set, Tuple, Union


# -------------------------
# Basic types
# -------------------------
class ColumnRef:
    """Symbolic reference to a column."""

    def __init__(self, table: str, column: str):
        self.table = table
        self.column = column
        self.qualified = f"{table}.{column}"

    def __repr__(self):
        return f"ColumnRef({self.qualified})"

    def __eq__(self, other):
        return isinstance(other, ColumnRef) and self.qualified == other.qualified

    def __hash__(self):
        return hash(self.qualified)


class DataType:
    """Minimal datatype helper."""

    def __init__(self, name: str):
        self.name = (name or "int").lower()

    def is_integer(self):
        return "int" in self.name

    def is_float(self):
        return "float" in self.name or "double" in self.name

    def is_numeric(self):
        return self.is_integer() or self.is_float()

    def is_string(self):
        return self.name in ("str", "string", "varchar", "text", "char")

    def is_boolean(self):
        return self.name in ("bool", "boolean")

    def is_datetime(self):
        return self.name in ("date", "datetime", "timestamp")

    def __repr__(self):
        return f"DataType({self.name})"


# -------------------------
# ColumnDomain (static)
# -------------------------
class ColumnDomain:
    """
    Static schema description for a column.
    - table, column => qualified name
    - datatype / min/max / uniqueness / FK metadata
    """

    def __init__(
        self,
        table: str,
        column: str,
        datatype: str = "int",
        min_val: Optional[int] = None,
        max_val: Optional[int] = None,
        unique: bool = False,
        nullable: bool = False,
        fk_target: Optional[str] = None,  # e.g. "Customers.id"
        fk_cardinality: str = "1:N",
        target_cast: Optional[str] = None,
    ):
        self.table = table
        self.column = column
        self.qualified = f"{table}.{column}"
        self.datatype = DataType(datatype)
        self.min_val = min_val
        self.max_val = max_val
        self.unique = unique
        self.nullable = nullable
        self.fk_target = fk_target
        self.fk_cardinality = fk_cardinality
        self.target_cast = DataType(target_cast) if target_cast else None

    def effective_type(self) -> DataType:
        """Type that should be generated (respect CAST if present)."""
        return self.target_cast or self.datatype

    def __repr__(self):
        return f"ColumnDomain({self.qualified}, type={self.datatype.name}, unique={self.unique}, fk={self.fk_target})"


# -------------------------
# JoinLinker (union-find)
# -------------------------
class JoinLinker:
    """Union-find for equality clusters (columns that must share values)."""

    def __init__(self):
        self.parent: Dict[str, str] = {}

    def find(self, q: str) -> str:
        if q not in self.parent:
            self.parent[q] = q
            return q
        # path compression
        while self.parent[q] != q:
            self.parent[q] = self.parent[self.parent[q]]
            q = self.parent[q]
        return q

    def union(self, a: str, b: str):
        ra = self.find(a)
        rb = self.find(b)
        if ra == rb:
            return
        # deterministic tie-break
        if ra < rb:
            self.parent[rb] = ra
        else:
            self.parent[ra] = rb

    def groups(self) -> Dict[str, List[str]]:
        out: Dict[str, List[str]] = {}
        for k in list(self.parent.keys()):
            r = self.find(k)
            out.setdefault(r, []).append(k)
        return out


# -------------------------
# ValuePool (single-domain)
# -------------------------
class ValuePool:
    """
    Runtime pool for a single ColumnDomain.
    - domain: ColumnDomain (static metadata)
    - values: list of generated/available values for this column
    - excluded: set of values known invalid (row-level exclusions)
    - rows: list of rows (dict qualified->value) that include this column
    - generated_set: track generated values for uniqueness enforcement
    """

    def __init__(self, domain: ColumnDomain):
        self.domain = domain
        self.values: List[Any] = []
        self.excluded: Set[Any] = set()
        self.rows: List[Dict[str, Any]] = []
        self.generated_set: Set[Any] = set() if domain.unique else set()

    # --- low level random primitives ---
    def _rand_int(self) -> int:
        low = self.domain.min_val if self.domain.min_val is not None else 0
        high = self.domain.max_val if self.domain.max_val is not None else low + 10000
        return random.randint(low, high)

    def _rand_float(self) -> float:
        low = float(self.domain.min_val) if self.domain.min_val is not None else 0.0
        high = (
            float(self.domain.max_val) if self.domain.max_val is not None else low + 1.0
        )
        return round(random.uniform(low, high), 6)

    def _rand_str(self) -> str:
        return "".join(random.choices(string.ascii_letters + string.digits, k=8))

    def _rand_date(self):
        end = datetime.now()
        start = end - timedelta(days=365 * 10)
        days = (end - start).days
        return start + timedelta(days=random.randint(0, days))

    # --- generation / sampling API ---
    def generate_new_value(self) -> Any:
        """
        Generate a fresh value for this domain.
        Respects exclusions and uniqueness by retrying a bounded number of times.
        Appends to self.values and returns the value.
        """
        dtype = self.domain.effective_type()
        attempts = 0
        while attempts < 2000:
            attempts += 1
            if dtype.is_integer():
                v = self._rand_int()
            elif dtype.is_float():
                v = self._rand_float()
            elif dtype.is_string():
                v = self._rand_str()
            elif dtype.is_boolean():
                v = random.choice([True, False])
            elif dtype.is_datetime():
                v = self._rand_date()
            else:
                v = self._rand_int()

            if v in self.excluded:
                continue
            if self.domain.unique and v in self.generated_set:
                continue

            self.values.append(v)
            if self.domain.unique:
                self.generated_set.add(v)
            return v

        raise RuntimeError(
            f"generate_new_value: exhausted attempts for {self.domain.qualified}"
        )

    def sample_existing(self) -> Optional[Any]:
        """Choose an existing value (not excluded), or None if none available."""
        candidates = [v for v in self.values if v not in self.excluded]
        if not candidates:
            return None
        return random.choice(candidates)

    def sample_for_fk_child(self) -> Any:
        """
        For FK child generation: prefer existing parent values (1:N). If none, generate a parent value.
        Parent semantics handled by caller (ColumnDomainPool).
        """
        existing = self.sample_existing()
        if existing is not None:
            return existing
        return self.generate_new_value()

    # --- rows & exclusions ---
    def register_row(self, row: Dict[str, Any]):
        """
        Register a previously-generated row (qualified->value).
        Ensures the pool knows about used values (and respects uniqueness).
        """
        self.rows.append(row)
        val = row.get(self.domain.qualified)
        if val is None:
            return
        if val not in self.excluded and val not in self.values:
            self.values.append(val)
        if self.domain.unique:
            self.generated_set.add(val)

    def exclude_value(self, v: Any):
        """Mark v as excluded (cannot be used going forward). Remove from values if present."""
        self.excluded.add(v)
        if v in self.values:
            try:
                self.values.remove(v)
            except ValueError:
                pass

    def __repr__(self):
        return f"ValuePool({self.domain.qualified}, values={self.values}, excluded={sorted(list(self.excluded))})"


# -------------------------
# ColumnDomainPool (registry + joiner)
# -------------------------
class ColumnDomainPool:
    """
    Registry mapping qualified column name -> ColumnDomain and -> ValuePool.
    Keeps a JoinLinker to maintain equality clusters.
    """

    def __init__(self):
        self.domains: Dict[str, ColumnDomain] = {}
        self.pools: Dict[str, ValuePool] = {}
        self.joiner = JoinLinker()

    def register_domain(self, domain: ColumnDomain):
        q = domain.qualified
        self.domains[q] = domain
        if q not in self.pools:
            self.pools[q] = ValuePool(domain)
        # make sure joiner knows the key
        self.joiner.find(q)

    def get_domain(self, q: str) -> Optional[ColumnDomain]:
        return self.domains.get(q)

    def get_pool(self, q: str) -> Optional[ValuePool]:
        return self.pools.get(q)

    def link_columns_equal(self, qa: str, qb: str):
        """
        Mark qa == qb (join/equality). Note: we only update joiner.
        Actual value sharing logic is handled by RowGenerator (sampling / registration).
        """
        self.joiner.union(qa, qb)

    def clusters(self) -> Dict[str, List[str]]:
        return self.joiner.groups()

    def show(self):
        for q, p in self.pools.items():
            dom = self.domains[q]
            print(f"{q:30} -> {p} ; domain={dom}")


# -------------------------
# Constraint (binary/unary)
# -------------------------
class Constraint:
    """
    Binary constraint left op right.
    left/right can be ColumnRef or literal (int/str/...).
    evaluate_on_row returns True/False/None (None means cannot evaluate yet).
    """

    def __init__(
        self, left: Union[ColumnRef, Any], op: str, right: Union[ColumnRef, Any]
    ):
        self.left = left
        self.op = op
        self.right = right

    def __repr__(self):
        return f"Constraint({self.left} {self.op} {self.right})"

    def involves_column(self, qname: str) -> bool:
        if isinstance(self.left, ColumnRef) and self.left.qualified == qname:
            return True
        if isinstance(self.right, ColumnRef) and self.right.qualified == qname:
            return True
        return False

    def evaluate_on_row(self, row: Dict[str, Any]) -> Optional[bool]:
        # get left value
        if isinstance(self.left, ColumnRef):
            lq = self.left.qualified
            if lq not in row:
                return None
            lv = row[lq]
        else:
            lv = self.left

        # get right value
        if isinstance(self.right, ColumnRef):
            rq = self.right.qualified
            if rq not in row:
                return None
            rv = row[rq]
        else:
            rv = self.right

        try:
            if self.op == "=":
                return lv == rv
            if self.op == "!=":
                return lv != rv
            if self.op == "<":
                return lv < rv
            if self.op == ">":
                return lv > rv
            if self.op == "<=":
                return lv <= rv
            if self.op == ">=":
                return lv >= rv
        except Exception:
            return False
        return False


# -------------------------
# AC7Propagator (support-based seeding + pruning)
# -------------------------
class AC7Propagator:
    """
    Simplified support-based propagation. Works only with binary column-column constraints.
    - seeds pools if empty
    - builds small support sets and prunes unsupported values
    """

    def __init__(self, pool_mgr: ColumnDomainPool, constraints: List[Constraint]):
        # only consider binary column-column constraints for support analysis
        self.pool_mgr = pool_mgr
        self.constraints = [
            c
            for c in constraints
            if isinstance(c.left, ColumnRef) and isinstance(c.right, ColumnRef)
        ]
        # supports: (colq, val, constraint_idx) -> set of supporting counterpart values
        self.supports: Dict[Tuple[str, Any, int], Set[Any]] = {}

    def initialize(self):
        # seed small samples if pools empty
        for q, pool in self.pool_mgr.pools.items():
            if not pool.values:
                # try to generate a few sample values (respect exclusions/uniqueness)
                for _ in range(3):
                    try:
                        pool.generate_new_value()
                    except RuntimeError:
                        break

        # build supports
        for idx, cons in enumerate(self.constraints):
            left_q = cons.left.qualified
            right_q = cons.right.qualified
            left_pool = self.pool_mgr.get_pool(left_q)
            right_pool = self.pool_mgr.get_pool(right_q)
            if left_pool is None or right_pool is None:
                continue
            for lv in list(left_pool.values):
                key = (left_q, lv, idx)
                self.supports[key] = set()
                for rv in list(right_pool.values):
                    if cons.evaluate_on_row({left_q: lv, right_q: rv}):
                        self.supports[key].add(rv)
            for rv in list(right_pool.values):
                key = (right_q, rv, idx)
                self.supports[key] = set()
                for lv in list(left_pool.values):
                    if cons.evaluate_on_row({left_q: lv, right_q: rv}):
                        self.supports[key].add(lv)

    def propagate(self):
        # queue of keys to check
        queue = list(self.supports.keys())
        while queue:
            key = queue.pop(0)
            colq, val, idx = key
            # if support set empty -> prune val from pool
            sset = self.supports.get(key, set())
            if not sset:
                pool = self.pool_mgr.get_pool(colq)
                if pool is None:
                    continue
                if val in pool.values:
                    pool.values.remove(val)
                # removing val may remove supports on neighbors: scan supports and discard val
                for k, s in list(self.supports.items()):
                    if val in s:
                        s.discard(val)
                        if not s:
                            queue.append(k)  # it may cause further pruning


# -------------------------
# RowGenerator (backtracking)
# -------------------------
class RowGenerator:
    """
    Backtracking row generator operating on ValuePool objects retrieved from ColumnDomainPool.
    - variables: ordered list of ColumnDomain (structure), RowGenerator uses pool_mgr to find corresponding ValuePool
    - respects FK sampling, exclusions, uniqueness and evaluates constraints incrementally
    """

    def __init__(self, pool_mgr: ColumnDomainPool, constraints: List[Constraint]):
        self.pool_mgr = pool_mgr
        self.constraints = constraints

    def generate_row(
        self, variables: List[ColumnDomain], max_attempts_per_var: int = 200
    ) -> Optional[Dict[str, Any]]:
        """
        Try to build one complete row (qualified->value) satisfying all constraints.
        Returns None if unsatisfiable.
        """
        assigned: Dict[str, Any] = {}
        order = variables[:]  # could apply heuristics here (MRV, degree)
        return self._backtrack(order, assigned, max_attempts_per_var)

    def _backtrack(
        self,
        remaining: List[ColumnDomain],
        assigned: Dict[str, Any],
        max_attempts_per_var: int,
    ) -> Optional[Dict[str, Any]]:
        if not remaining:
            # final check for all constraints
            for c in self.constraints:
                res = c.evaluate_on_row(assigned)
                if res is False:
                    return None
            return dict(assigned)

        domain = remaining[0]
        pool = self.pool_mgr.get_pool(domain.qualified)
        if pool is None:
            return None

        # If domain has FK parent, prefer sampling parent values (1:N)
        if domain.fk_target:
            parent_pool = self.pool_mgr.get_pool(domain.fk_target)
            # try sampling existing parent values first
            for _ in range(max_attempts_per_var):
                candidate = None
                if parent_pool:
                    candidate = parent_pool.sample_for_fk_child()
                else:
                    candidate = pool.generate_new_value()
                # candidate must not be in pool.excluded
                if candidate in pool.excluded:
                    pool.exclude_value(candidate)
                    continue
                assigned[domain.qualified] = candidate
                if not self._partial_consistent(assigned, domain):
                    pool.exclude_value(candidate)
                    continue
                # recurse
                res = self._backtrack(remaining[1:], assigned, max_attempts_per_var)
                if res:
                    # register row in both pools (child and parent)
                    pool.register_row(res)
                    if parent_pool:
                        parent_pool.register_row(res)
                    return res
            return None

        # Non-FK domains: try existing then new values
        tried: Set[Any] = set()
        # existing values
        for _ in range(max_attempts_per_var):
            candidate = pool.sample_existing()
            if candidate is None:
                break
            if candidate in tried:
                break
            tried.add(candidate)
            assigned[domain.qualified] = candidate
            if not self._partial_consistent(assigned, domain):
                pool.exclude_value(candidate)
                continue
            res = self._backtrack(remaining[1:], assigned, max_attempts_per_var)
            if res:
                pool.register_row(res)
                return res

        # generate new candidates
        for _ in range(max_attempts_per_var):
            candidate = pool.generate_new_value()
            assigned[domain.qualified] = candidate
            if not self._partial_consistent(assigned, domain):
                pool.exclude_value(candidate)
                continue
            res = self._backtrack(remaining[1:], assigned, max_attempts_per_var)
            if res:
                pool.register_row(res)
                return res

        return None

    def _partial_consistent(
        self, assigned: Dict[str, Any], recent_domain: ColumnDomain
    ) -> bool:
        """
        Evaluate any constraint that mentions recent_domain against the current partial assignment.
        If any constraint is violated -> False, else True (or Unknown -> True).
        """
        for c in self.constraints:
            if (
                isinstance(c.left, ColumnRef)
                and c.left.qualified == recent_domain.qualified
                or isinstance(c.right, ColumnRef)
                and c.right.qualified == recent_domain.qualified
            ):
                val = c.evaluate_on_row(assigned)
                if val is False:
                    return False
        return True


# -------------------------
# Top-level HybridSolver
# -------------------------
class HybridSolver:
    """
    High-level orchestrator.
    - register_domain(ColumnDomain)
    - link_equals(ColumnRef, ColumnRef) to declare joins
    - solve_one_row(variables: List[ColumnDomain], constraints: List[Constraint]) -> Optional[row]
    """

    def __init__(self):
        self.pool_mgr = ColumnDomainPool()

    def register_domain(self, domain: ColumnDomain):
        self.pool_mgr.register_domain(domain)

    def link_equals(self, a: ColumnRef, b: ColumnRef):
        self.pool_mgr.link_columns_equal(a.qualified, b.qualified)

    def solve_one_row(
        self, variables: List[ColumnDomain], constraints: List[Constraint]
    ) -> Optional[Dict[str, Any]]:
        # 1) AC7 seeding + propagation
        ac7 = AC7Propagator(self.pool_mgr, constraints)
        ac7.initialize()
        ac7.propagate()

        # 2) backtracking row generation
        gen = RowGenerator(self.pool_mgr, constraints)
        return gen.generate_row(variables)

    def debug_state(self) -> str:
        return "\n".join(
            f"{q}: values={p.values} excluded={sorted(list(p.excluded))} rows={len(p.rows)}"
            for q, p in self.pool_mgr.pools.items()
        )


if __name__ == "__main__":
    # Setup
    solver = HybridSolver()
    # register domains
    customers_id = ColumnDomain(
        "Customers", "id", "int", min_val=1, max_val=100, unique=True
    )
    customers_age = ColumnDomain("Customers", "age", "int", min_val=0, max_val=120)
    solver.register_domain(customers_id)
    solver.register_domain(customers_age)

    # seed some existing rows into pools (simulate previously inserted tuples)
    p_id = solver.pool_mgr.get_pool("Customers.id")
    p_age = solver.pool_mgr.get_pool("Customers.age")

    row1 = {"Customers.id": 1, "Customers.age": 26}
    row2 = {"Customers.id": 2, "Customers.age": 20}
    p_id.register_row(row1)
    p_age.register_row(row1)
    p_id.register_row(row2)
    p_age.register_row(row2)

    print("Before solve:")
    print(solver.debug_state())

    # constraint: age < 25 (so row1 should be excluded)
    age_ref = ColumnRef("Customers", "age")
    id_ref = ColumnRef("Customers", "id")
    constraints = [Constraint(age_ref, "<", 25)]

    # variables we want values for in the same tuple
    variables = [customers_id, customers_age, age_ref]

    new_row = solver.solve_one_row(variables, constraints)
    print("\nGenerated row:", new_row)

    print("\nAfter solve state:")
    print(solver.debug_state())
