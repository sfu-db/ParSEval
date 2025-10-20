# === AC-3 integrated implementation ===
import itertools
import random
from collections import deque
from typing import Any, Dict, List, Optional, Set, Tuple

# ---------- (re-use your project ColumnRef / ColumnDomain / ValuePool / ColumnDomainPool)
# If you already have these classes in your codebase, import them instead of redefining.
# For clarity I include minimal compatible versions here — replace them with your real ones.


class ColumnRef:
    def __init__(self, table: str, column: str):
        self.table = table
        self.column = column
        self.qualified = f"{table}.{column}"

    def __repr__(self):
        return f"ColumnRef({self.qualified})"


class ColumnDomain:
    def __init__(
        self, table, column, datatype="int", min_val=None, max_val=None, unique=False
    ):
        self.table = table
        self.column = column
        self.qualified = f"{table}.{column}"
        self.datatype = datatype
        self.min_val = min_val
        self.max_val = max_val
        self.unique = unique

    def effective_type(self):
        return self.datatype

    def __repr__(self):
        return f"ColumnDomain({self.qualified})"


# Minimal ValuePool interface expected by AC3Propagator:
class ValuePool:
    def __init__(self, domain: ColumnDomain):
        self.domain = domain
        self.values: List[Any] = []
        self.excluded: Set[Any] = set()
        self.rows: List[Dict[str, Any]] = []
        self.generated_set: Set[Any] = set() if domain.unique else set()

    def generate_new_value(self):
        """Generate a fresh numeric value observing domain bounds; add to .values and return it."""
        low = self.domain.min_val if self.domain.min_val is not None else 0
        high = self.domain.max_val if self.domain.max_val is not None else low + 10000
        attempts = 0
        while attempts < 1000:
            attempts += 1
            v = random.randint(low, high)
            if v in self.excluded:
                continue
            if self.domain.unique and v in self.generated_set:
                continue
            self.values.append(v)
            if self.domain.unique:
                self.generated_set.add(v)
            return v
        raise RuntimeError("generate_new_value exhausted")

    def sample_existing(self) -> Optional[Any]:
        cands = [v for v in self.values if v not in self.excluded]
        return random.choice(cands) if cands else None

    def exclude_value(self, v: Any):
        self.excluded.add(v)
        if v in self.values:
            try:
                self.values.remove(v)
            except ValueError:
                pass

    def register_row(self, row: Dict[str, Any]):
        self.rows.append(row)
        val = row.get(self.domain.qualified)
        if val is None:
            return
        if val not in self.excluded and val not in self.values:
            self.values.append(val)
        if self.domain.unique:
            self.generated_set.add(val)

    def current_min(self):
        if self.values:
            return min(self.values)
        return self.domain.min_val

    def current_max(self):
        if self.values:
            return max(self.values)
        return self.domain.max_val

    def prune_values_below(self, new_min: int) -> bool:
        removed = [v for v in list(self.values) if v < new_min]
        changed = False
        for v in removed:
            self.exclude_value(v)
            changed = True
        if self.domain.min_val is None or self.domain.min_val < new_min:
            self.domain.min_val = new_min
            changed = True
        return changed

    def prune_values_above(self, new_max: int) -> bool:
        removed = [v for v in list(self.values) if v > new_max]
        changed = False
        for v in removed:
            self.exclude_value(v)
            changed = True
        if self.domain.max_val is None or self.domain.max_val > new_max:
            self.domain.max_val = new_max
            changed = True
        return changed


class ColumnDomainPool:
    def __init__(self):
        self.domains: Dict[str, ColumnDomain] = {}
        self.pools: Dict[str, ValuePool] = {}
        self.joiner = {}  # not used heavily here

    def register_domain(self, d: ColumnDomain):
        self.domains[d.qualified] = d
        if d.qualified not in self.pools:
            self.pools[d.qualified] = ValuePool(d)

    def get_pool(self, qname: str) -> Optional[ValuePool]:
        return self.pools.get(qname)

    def show(self):
        for q, p in self.pools.items():
            print(
                f"{q}: values={p.values} excluded={sorted(list(p.excluded))} domain_min={p.domain.min_val} domain_max={p.domain.max_val}"
            )


# ---------- Constraints: generic + SumConstraint ----------
class Constraint:
    """
    Base n-ary constraint. Subclasses must implement evaluate_on_row(assignment).
    'vars' is a list of ColumnRef objects.
    """

    def __init__(self, vars: List[ColumnRef]):
        self.vars = vars

    def evaluate_on_row(self, assignment: Dict[str, Any]) -> Optional[bool]:
        raise NotImplementedError()

    def related_pairs(self):
        # default: all ordered pairs of variable qualified names
        names = [v.qualified for v in self.vars]
        for i in range(len(names)):
            for j in range(len(names)):
                if i == j:
                    continue
                yield (names[i], names[j])

    def __repr__(self):
        return f"{self.__class__.__name__}({[v.qualified for v in self.vars]})"


class SumConstraint(Constraint):
    """
    Sum(vars) OP rhs where OP is one of >, >=, <, <=.
    """

    def __init__(self, vars: List[ColumnRef], op: str, rhs: int):
        super().__init__(vars)
        assert op in (">", ">=", "<", "<=")
        self.op = op
        self.rhs = rhs

    def evaluate_on_row(self, assignment: Dict[str, Any]) -> Optional[bool]:
        vals = []
        for v in self.vars:
            if v.qualified not in assignment:
                return None
            vals.append(assignment[v.qualified])
        s = sum(vals)
        if self.op == ">":
            return s > self.rhs
        if self.op == ">=":
            return s >= self.rhs
        if self.op == "<":
            return s < self.rhs
        if self.op == "<=":
            return s <= self.rhs
        return None

    def __repr__(self):
        return f"SumConstraint({[v.qualified for v in self.vars]} {self.op} {self.rhs})"


# ---------- AC-3 Propagator adapted for real ColumnRef + pools ----------
class AC3Propagator:
    """
    AC-3 propagator that works with ColumnDomainPool and Constraint instances.

    - pool_mgr: ColumnDomainPool
    - constraints: list of Constraint (n-ary supported)
    - sample_threshold: if product of supporting domains > threshold -> use sampling
    - sample_budget: number of samples when sampling
    """

    def __init__(
        self,
        pool_mgr: ColumnDomainPool,
        constraints: List[Constraint],
        sample_threshold: int = 2000,
        sample_budget: int = 200,
    ):
        self.pool_mgr = pool_mgr
        self.constraints = constraints
        self.sample_threshold = sample_threshold
        self.sample_budget = sample_budget
        # build pair->constraints mapping
        self.pair_to_constraints: Dict[Tuple[str, str], List[Constraint]] = {}
        for c in self.constraints:
            for qi, qj in c.related_pairs():
                self.pair_to_constraints.setdefault((qi, qj), []).append(c)

    def _generate_initial_arcs(self) -> List[Tuple[str, str, Constraint]]:
        arcs = []
        for (qi, qj), cons_list in self.pair_to_constraints.items():
            for c in cons_list:
                arcs.append((qi, qj, c))
        return arcs

    def run(self):
        """Run AC-3 until stable. Raises RuntimeError if a domain becomes empty."""
        queue = deque(self._generate_initial_arcs())
        while queue:
            xi, xj, cons = queue.popleft()
            revised = self._revise(xi, xj, cons)
            if revised:
                pool_xi = self.pool_mgr.get_pool(xi)
                if not pool_xi or not pool_xi.values:
                    raise RuntimeError(
                        f"Domain {xi} became empty during AC-3 (constraint {cons})"
                    )
                # enqueue neighbors (xk, xi) for all xk != xj that mention xi
                for (a, b), cons_list in self.pair_to_constraints.items():
                    if b == xi and a != xj:
                        for c in cons_list:
                            queue.append((a, xi, c))

    def _revise(self, xi: str, xj: str, cons: Constraint) -> bool:
        """
        For each vi in domain(xi), check whether there exists vj in domain(xj) and
        assignments to other vars such that cons is satisfied. If none, remove vi.
        Returns True if domain(xi) reduced.
        """
        pool_i = self.pool_mgr.get_pool(xi)
        pool_j = self.pool_mgr.get_pool(xj)
        if pool_i is None or pool_j is None:
            return False

        removed_any = False
        values_i = list(pool_i.values)  # snapshot

        for vi in values_i:
            has_support = False

            # other vars in constraint excluding xi,xj
            other_vars = [v for v in cons.vars if v.qualified not in (xi, xj)]

            # Binary-only shortcut
            if not other_vars:
                # iterate all vj
                for vj in pool_j.values:
                    assignment = {xi: vi, xj: vj}
                    res = cons.evaluate_on_row(assignment)
                    if res is True:
                        has_support = True
                        break
            else:
                # compute product size of domains [pool_j] + other_vars pools
                sizes = [len(pool_j.values)]
                pools_list = [pool_j]
                missing = False
                for ov in other_vars:
                    p = self.pool_mgr.get_pool(ov.qualified)
                    if p is None or not p.values:
                        missing = True
                        break
                    pools_list.append(p)
                    sizes.append(len(p.values))
                if missing:
                    # if supporting pools empty, cannot find support => treat as no support
                    has_support = False
                else:
                    product = 1
                    for s in sizes:
                        product *= max(1, s)
                    if product <= self.sample_threshold:
                        # exhaustive search over cartesian product: iterate vj and other values
                        iterables = [p.values for p in pools_list]
                        for combo in itertools.product(*iterables):
                            vj = combo[0]
                            others = combo[1:]
                            assignment = {xi: vi, xj: vj}
                            for ov, val in zip(other_vars, others):
                                assignment[ov.qualified] = val
                            if cons.evaluate_on_row(assignment) is True:
                                has_support = True
                                break
                    else:
                        # sampling path
                        for _ in range(self.sample_budget):
                            sampled = [random.choice(p.values) for p in pools_list]
                            vj = sampled[0]
                            others = sampled[1:]
                            assignment = {xi: vi, xj: vj}
                            for ov, val in zip(other_vars, others):
                                assignment[ov.qualified] = val
                            if cons.evaluate_on_row(assignment) is True:
                                has_support = True
                                break

            if not has_support:
                # remove vi
                pool_i.exclude_value(vi)
                removed_any = True

        return removed_any


# ---------- RowGenerator (simple backtracking that checks n-ary constraints) ----------
class RowGenerator:
    """
    Backtracking generator that uses pools in ColumnDomainPool.
    """

    def __init__(self, pool_mgr: ColumnDomainPool, constraints: List[Constraint]):
        self.pool_mgr = pool_mgr
        self.constraints = constraints

    def generate_row(
        self, variables: List[ColumnDomain], max_attempts_per_var: int = 200
    ) -> Optional[Dict[str, Any]]:
        assigned: Dict[str, Any] = {}
        order = variables[:]  # could apply MRV
        return self._backtrack(order, assigned, max_attempts_per_var)

    def _backtrack(
        self,
        remaining: List[ColumnDomain],
        assigned: Dict[str, Any],
        max_attempts_per_var: int,
    ) -> Optional[Dict[str, Any]]:
        if not remaining:
            # final verify
            for c in self.constraints:
                r = c.evaluate_on_row(assigned)
                if r is False:
                    return None
            return dict(assigned)

        domain = remaining[0]
        pool = self.pool_mgr.get_pool(domain.qualified)
        if pool is None:
            return None

        # try existing values first
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

        # try new values
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

        if domain.qualified in assigned:
            assigned.pop(domain.qualified, None)
        return None

    def _partial_ok(
        self, assigned: Dict[str, Any], recent_domain: ColumnDomain
    ) -> bool:
        for c in self.constraints:
            # only check constraints that reference recent_domain
            mentions = any(
                isinstance(v, ColumnRef) and v.qualified == recent_domain.qualified
                for v in c.vars
            )
            if not mentions:
                continue
            r = c.evaluate_on_row(assigned)
            if r is False:
                return False
        return True


# ---------- HybridSolver that runs AC-3 then backtracking ----------
class HybridSolver:
    def __init__(self):
        self.pool_mgr = ColumnDomainPool()

    def register_domain(self, domain: ColumnDomain):
        self.pool_mgr.register_domain(domain)

    def solve_one_row(
        self,
        variables: List[ColumnDomain],
        constraints: List[Constraint],
        ac3_sample_threshold: int = 2000,
    ) -> Optional[Dict[str, Any]]:
        # 1) seed pools if empty (small seeding to create concrete values)
        for v in variables:
            p = self.pool_mgr.get_pool(v.qualified)
            if p and not p.values:
                # seed with a few values (avoids empty domain for AC-3)
                for _ in range(3):
                    try:
                        p.generate_new_value()
                    except RuntimeError:
                        break

        # 2) AC-3 propagation
        ac3 = AC3Propagator(
            self.pool_mgr, constraints, sample_threshold=ac3_sample_threshold
        )
        ac3.run()

        # 3) backtracking row generation
        gen = RowGenerator(self.pool_mgr, constraints)
        return gen.generate_row(variables)


# ---------- Demo usage ----------
if __name__ == "__main__":
    random.seed(1)
    solver = HybridSolver()

    A = ColumnDomain("T", "A", "int", 0, 200)
    B = ColumnDomain("T", "B", "int", 0, 200)
    C = ColumnDomain("T", "C", "int", 0, 200)
    solver.register_domain(A)
    solver.register_domain(B)
    solver.register_domain(C)

    # seed pools a bit
    for _ in range(5):
        solver.pool_mgr.get_pool("T.A").generate_new_value()
        solver.pool_mgr.get_pool("T.B").generate_new_value()
        solver.pool_mgr.get_pool("T.C").generate_new_value()

    crefA = ColumnRef("T", "A")
    crefB = ColumnRef("T", "B")
    crefC = ColumnRef("T", "C")
    sumc = SumConstraint([crefA, crefB, crefC], ">", 200)
    print("Before AC-3:")
    solver.pool_mgr.show()

    row = solver.solve_one_row([A, B, C], [sumc], ac3_sample_threshold=500)
    print("\nGenerated row:", row)
    print("\nAfter AC-3 and generation:")
    solver.pool_mgr.show()
