import random
import string
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Set, Tuple, Union


# -------------------------
# Lightweight types (ColumnRef, DataType)
# -------------------------
class ColumnRef:
    def __init__(self, table: str, column: str):
        self.table = table
        self.column = column
        self.qualified = f"{table}.{column}"

    def __repr__(self):
        return f"ColumnRef({self.qualified})"


class DataType:
    def __init__(self, name: str = "int"):
        self.name = (name or "int").lower()

    def is_integer(self):
        return "int" in self.name

    def is_float(self):
        return "float" in self.name or "double" in self.name

    def is_numeric(self):
        return self.is_integer() or self.is_float()

    def is_string(self):
        return self.name in ("str", "string", "varchar", "text", "char")

    def is_datetime(self):
        return self.name in ("date", "datetime", "timestamp")

    def __repr__(self):
        return f"DataType({self.name})"


# -------------------------
# ColumnDomain (schema-level)
# -------------------------
class ColumnDomain:
    def __init__(
        self,
        table: str,
        column: str,
        datatype: str = "int",
        min_val: Optional[int] = None,
        max_val: Optional[int] = None,
        unique: bool = False,
        fk_target: Optional[str] = None,
        target_cast: Optional[str] = None,
    ):
        self.table = table
        self.column = column
        self.qualified = f"{table}.{column}"
        self.datatype = DataType(datatype)
        self.min_val = min_val
        self.max_val = max_val
        self.unique = unique
        self.fk_target = fk_target
        self.target_cast = DataType(target_cast) if target_cast else None

    def effective_type(self) -> DataType:
        return self.target_cast or self.datatype

    def __repr__(self):
        return f"ColumnDomain({self.qualified}, {self.datatype}, min={self.min_val}, max={self.max_val}, unique={self.unique})"


# -------------------------
# JoinLinker (union-find)
# -------------------------
class JoinLinker:
    def __init__(self):
        self.parent: Dict[str, str] = {}

    def find(self, q: str) -> str:
        if q not in self.parent:
            self.parent[q] = q
            return q
        while self.parent[q] != q:
            self.parent[q] = self.parent[self.parent[q]]
            q = self.parent[q]
        return q

    def union(self, a: str, b: str):
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return
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
    Single-domain runtime pool (per column).
    Holds:
      - domain (ColumnDomain)
      - values: usable generated values
      - excluded: values deemed invalid by propagation/backtracking
      - rows: registered rows that include this column
    """

    def __init__(self, domain: ColumnDomain):
        self.domain = domain
        self.values: List[Any] = []
        self.excluded: Set[Any] = set()
        self.rows: List[Dict[str, Any]] = []
        self.generated_set: Set[Any] = set() if domain.unique else set()

    def _rand_int(self):
        low = self.domain.min_val if self.domain.min_val is not None else 0
        high = self.domain.max_val if self.domain.max_val is not None else low + 10000
        return random.randint(low, high)

    def generate_new_value(self) -> Any:
        dtype = self.domain.effective_type()
        attempts = 0
        while attempts < 2000:
            attempts += 1
            if dtype.is_integer():
                v = self._rand_int()
            elif dtype.is_float():
                v = float(self._rand_int())
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
        raise RuntimeError(f"failed generate for {self.domain.qualified}")

    def sample_existing(self) -> Optional[Any]:
        cands = [v for v in self.values if v not in self.excluded]
        if not cands:
            return None
        return random.choice(cands)

    def register_row(self, row: Dict[str, Any]):
        self.rows.append(row)
        val = row.get(self.domain.qualified)
        if val is None:
            return
        if val not in self.excluded and val not in self.values:
            self.values.append(val)
        if self.domain.unique:
            self.generated_set.add(val)

    def exclude_value(self, v: Any):
        self.excluded.add(v)
        if v in self.values:
            try:
                self.values.remove(v)
            except ValueError:
                pass

    def current_min(self) -> Optional[int]:
        """Return best-available min estimate for the pool."""
        if self.values:
            return min(self.values)
        if self.domain.min_val is not None:
            return self.domain.min_val
        return None

    def current_max(self) -> Optional[int]:
        if self.values:
            return max(self.values)
        if self.domain.max_val is not None:
            return self.domain.max_val
        return None

    def prune_values_below(self, new_min: int) -> bool:
        """Remove values < new_min; return True if changed."""
        changed = False
        remove = [v for v in self.values if v < new_min]
        for v in remove:
            self.exclude_value(v)
            changed = True
        # also update domain min if possible
        if self.domain.min_val is None or self.domain.min_val < new_min:
            self.domain.min_val = new_min
            changed = True
        return changed

    def prune_values_above(self, new_max: int) -> bool:
        changed = False
        remove = [v for v in self.values if v > new_max]
        for v in remove:
            self.exclude_value(v)
            changed = True
        if self.domain.max_val is None or self.domain.max_val > new_max:
            self.domain.max_val = new_max
            changed = True
        return changed

    def __repr__(self):
        return f"ValuePool({self.domain.qualified}, values={self.values}, excl={sorted(list(self.excluded))}, domain_min={self.domain.min_val}, domain_max={self.domain.max_val})"


# -------------------------
# ColumnDomainPool
# -------------------------
class ColumnDomainPool:
    def __init__(self):
        self.domains: Dict[str, ColumnDomain] = {}
        self.pools: Dict[str, ValuePool] = {}
        self.joiner = JoinLinker()

    def register_domain(self, d: ColumnDomain):
        self.domains[d.qualified] = d
        if d.qualified not in self.pools:
            self.pools[d.qualified] = ValuePool(d)
        self.joiner.find(d.qualified)

    def get_pool(self, q: str) -> Optional[ValuePool]:
        return self.pools.get(q)

    def link_equals(self, a: ColumnRef, b: ColumnRef):
        self.joiner.union(a.qualified, b.qualified)

    def clusters(self):
        return self.joiner.groups()

    def show(self):
        for q, p in self.pools.items():
            print(p)


# -------------------------
# Generic Constraint base (supports n-ary)
# -------------------------
class Constraint:
    """
    Base class: left/right can be ColumnRef or literal.
    For n-ary constraints we subclass and override propagate/evaluate.
    """

    def __init__(self, vars_involved: List[ColumnRef]):
        self.vars = vars_involved  # list of ColumnRef

    def evaluate_on_row(self, row: Dict[str, Any]) -> Optional[bool]:
        """Default: unknown. Subclasses implement."""
        return None

    def propagate(self, pool_mgr: ColumnDomainPool) -> bool:
        """
        Try to tighten domains / pools; return True if any change happened.
        Default: no-op.
        """
        return False


# -------------------------
# SumConstraint: A + B + C > K (n-ary arithmetic)
# -------------------------
class SumConstraint(Constraint):
    def __init__(self, vars_involved: List[ColumnRef], operator: str, rhs: int):
        """
        operator: one of '>', '>=', '<', '<='
        rhs: numeric threshold
        """
        super().__init__(vars_involved)
        assert operator in (">", ">=", "<", "<=")
        self.op = operator
        self.rhs = rhs

    def evaluate_on_row(self, row: Dict[str, Any]) -> Optional[bool]:
        vals = []
        for v in self.vars:
            if v.qualified not in row:
                return None
            vals.append(row[v.qualified])
        total = sum(vals)
        if self.op == ">":
            return total > self.rhs
        if self.op == ">=":
            return total >= self.rhs
        if self.op == "<":
            return total < self.rhs
        if self.op == "<=":
            return total <= self.rhs
        return None

    def propagate(self, pool_mgr: ColumnDomainPool) -> bool:
        """
        Tighten per-variable min/max values using simple interval arithmetic:
          - For operator '>', ensure for each Xi: min(Xi) >= rhs+1 - sum_{j!=i} max(Xj)
          - and similar for '<' with max bounds.
        Returns True if any pool was tightened or values pruned.
        """
        pools: List[ValuePool] = []
        for cref in self.vars:
            pool = pool_mgr.get_pool(cref.qualified)
            if pool is None:
                return False
            pools.append(pool)

        changed = False

        # compute current min/max for each variable
        mins: List[int] = []
        maxs: List[int] = []
        for p in pools:
            m = p.current_min()
            M = p.current_max()
            # fallback sensible defaults
            if m is None:
                m = p.domain.min_val if p.domain.min_val is not None else 0
            if M is None:
                M = p.domain.max_val if p.domain.max_val is not None else (m + 1000)
            mins.append(m)
            maxs.append(M)

        total_min = sum(mins)
        total_max = sum(maxs)

        # Unsatisfiable detection
        if self.op == ">" and total_max <= self.rhs:
            # no assignment can satisfy (max sum too small)
            raise RuntimeError(
                f"Unsatisfiable SumConstraint: max sum {total_max} <= {self.rhs}"
            )
        if self.op == "<" and total_min >= self.rhs:
            raise RuntimeError(
                f"Unsatisfiable SumConstraint: min sum {total_min} >= {self.rhs}"
            )

        # For '>' operator, we can raise minima
        if self.op in (">", ">="):
            # need sum > rhs (or >=)
            # for each var i, required min_i = (rhs + 1) - sum_{j!=i} max_j  (for '>')
            # for '>=' use rhs - sum_others + 0
            need_offset = 1 if self.op == ">" else 0
            for i, p in enumerate(pools):
                sum_other_max = total_max - maxs[i]
                required = (self.rhs + need_offset) - sum_other_max
                # required is lower bound candidate
                if required is None:
                    continue
                required_int = int(required)
                # If required_int is greater than current min, raise it
                cur_min = (
                    p.current_min()
                    if p.current_min() is not None
                    else (p.domain.min_val or 0)
                )
                if required_int > cur_min:
                    # prune values below required_int
                    did = p.prune_values_below(required_int)
                    if did:
                        changed = True

        # For '<' operator, we can lower maxima
        if self.op in ("<", "<="):
            need_offset = -1 if self.op == "<" else 0  # for strict '<' reduce by 1
            for i, p in enumerate(pools):
                sum_other_min = total_min - mins[i]
                # we need Xi < rhs - sum_other_min  (strict)
                bound = (self.rhs + need_offset) - sum_other_min
                bound_int = int(bound)
                cur_max = (
                    p.current_max()
                    if p.current_max() is not None
                    else (p.domain.max_val or (cur_max := bound_int))
                )
                if bound_int < cur_max:
                    did = p.prune_values_above(bound_int)
                    if did:
                        changed = True

        return changed

    def __repr__(self):
        vars_q = ", ".join([v.qualified for v in self.vars])
        return f"SumConstraint({vars_q} {self.op} {self.rhs})"


# -------------------------
# ConstraintPropagator (iterate n-ary constraint propagation)
# -------------------------
class ConstraintPropagator:
    def __init__(self, pool_mgr: ColumnDomainPool, constraints: List[Constraint]):
        self.pool_mgr = pool_mgr
        self.constraints = constraints

    def propagate_all(self, max_rounds: int = 20) -> None:
        """
        Iteratively call propagate() on constraints until stable or max_rounds reached.
        propagate() may raise RuntimeError on unsatisfiable detection.
        """
        for r in range(max_rounds):
            any_changed = False
            for c in self.constraints:
                changed = c.propagate(self.pool_mgr)
                if changed:
                    any_changed = True
            if not any_changed:
                return
        # if still changing after max_rounds, just return (avoid infinite loops)
        return


# -------------------------
# Simple backtracking RowGenerator (checks n-ary constraints via evaluate_on_row)
# -------------------------
class RowGenerator:
    def __init__(self, pool_mgr: ColumnDomainPool, constraints: List[Constraint]):
        self.pool_mgr = pool_mgr
        self.constraints = constraints

    def generate_row(
        self, variables: List[ColumnDomain], max_attempts_per_var: int = 200
    ) -> Optional[Dict[str, Any]]:
        assigned: Dict[str, Any] = {}
        order = variables[:]  # could use MRV; keep simple for demo
        return self._backtrack(order, assigned, max_attempts_per_var)

    def _backtrack(
        self,
        remaining: List[ColumnDomain],
        assigned: Dict[str, Any],
        max_attempts_per_var: int,
    ) -> Optional[Dict[str, Any]]:
        if not remaining:
            # full assignment check
            for c in self.constraints:
                res = c.evaluate_on_row(assigned)
                if res is False:
                    return None
            return dict(assigned)

        domain = remaining[0]
        pool = self.pool_mgr.get_pool(domain.qualified)
        if pool is None:
            return None

        # try reuse existing
        for _ in range(max_attempts_per_var):
            cand = pool.sample_existing()
            if cand is None:
                break
            assigned[domain.qualified] = cand
            if not self._partial_ok(assigned, domain):
                pool.exclude_value(cand)
                continue
            res = self._backtrack(remaining[1:], assigned, max_attempts_per_var)
            if res:
                pool.register_row(res)
                return res

        # try generate new
        for _ in range(max_attempts_per_var):
            cand = pool.generate_new_value()
            assigned[domain.qualified] = cand
            if not self._partial_ok(assigned, domain):
                pool.exclude_value(cand)
                continue
            res = self._backtrack(remaining[1:], assigned, max_attempts_per_var)
            if res:
                pool.register_row(res)
                return res

        # failure
        if domain.qualified in assigned:
            assigned.pop(domain.qualified, None)
        return None

    def _partial_ok(
        self, assigned: Dict[str, Any], recent_domain: ColumnDomain
    ) -> bool:
        # evaluate any constraint that references the recent domain
        for c in self.constraints:
            # check if c mentions recent domain
            mentions = any(
                (isinstance(v, ColumnRef) and v.qualified == recent_domain.qualified)
                for v in getattr(c, "vars", [])
            )
            if not mentions:
                # for binary constraints other forms might be stored differently; assume c.vars if present
                continue
            r = c.evaluate_on_row(assigned)
            if r is False:
                return False
        return True


# -------------------------
# Demo: A + B + C > 200
# -------------------------
if __name__ == "__main__":
    random.seed(1)

    # create pool mgr and domains
    pool_mgr = ColumnDomainPool()
    A = ColumnDomain("T", "A", "int", min_val=0, max_val=200)
    B = ColumnDomain("T", "B", "int", min_val=0, max_val=200)
    C = ColumnDomain("T", "C", "int", min_val=0, max_val=200)
    pool_mgr.register_domain(A)
    pool_mgr.register_domain(B)
    pool_mgr.register_domain(C)

    # optionally seed some values to pools (not necessary; propagator will seed)
    # add an example existing row that should be excluded if inconsistent
    pA = pool_mgr.get_pool("T.A")
    pB = pool_mgr.get_pool("T.B")
    pC = pool_mgr.get_pool("T.C")
    # seed a few values
    for _ in range(3):
        pA.generate_new_value()
        pB.generate_new_value()
        pC.generate_new_value()

    # define sum constraint A+B+C > 200
    crefA = ColumnRef("T", "A")
    crefB = ColumnRef("T", "B")
    crefC = ColumnRef("T", "C")
    sum_cons = SumConstraint([crefA, crefB, crefC], ">", 200)
    print("Initial pools:")
    pool_mgr.show()

    # run propagation
    propagator = ConstraintPropagator(pool_mgr, [sum_cons])
    try:
        propagator.propagate_all()
        print("\nAfter propagation (domain tightening / pruning):")
        pool_mgr.show()
    except RuntimeError as e:
        print("Propagation found UNSAT:", e)

    # now try to generate a concrete row
    gen = RowGenerator(pool_mgr, [sum_cons])
    row = gen.generate_row([A, B, C])
    print("\nGenerated row:", row)
    print("\nFinal pools state:")
    pool_mgr.show()
