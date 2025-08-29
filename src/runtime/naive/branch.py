from __future__ import annotations
from dataclasses import dataclass, field
from typing import List, Set, Tuple, Optional, Iterable, Dict, Any
import itertools

# =========================================================
# Core Tree with CASE/WHEN, Set-ops, Projections
# =========================================================

# self.name: t.Optional[str] = None
#         self.dependencies: t.Set[Step] = set()
#         self.dependents: t.Set[Step] = set()
#         self.projections: t.Sequence[exp.Expression] = []
#         self.limit: float = math.inf
#         self.condition: t.Optional[exp.Expression] = None

from enum import auto, Enum

class NodeKind(Enum):
    ROOT = auto()
    CONSTRAINT = auto()
    SET_OP = auto() #"set_op"          # op in {"UNION","INTERSECT","EXCEPT"}
    CASE =  auto() #"case"              # represents a CASE expression branching

# class NodeKind:
#     ROOT = "root"
#     CONSTRAINT = "constraint"
#     SET_OP = "set_op"          # op in {"UNION","INTERSECT","EXCEPT"}
#     CASE = "case"              # represents a CASE expression branching

class ConstraintNode:
    """A robust tree node for SQL execution-path tracking.

    Supports:
      - Dedicated ROOT node
      - Constraint nodes with normalized tuples, e.g. ("age", ">", 18)
      - Set-ops (UNION/INTERSECT/EXCEPT)
      - CASE WHEN branching (children are WHEN/ELSE branches)
      - Projection propagation (set of column names)
      - Optional branch labels (e.g., THEN values for CASE)
    """

    _id_counter = itertools.count()

    def __init__(self,
                 name: Optional[str] = None,
                 *,
                 node_type: str = NodeKind.CONSTRAINT,
                 op: Optional[str] = None,                 # for set_op: "UNION"|"INTERSECT"|"EXCEPT"
                 projection: Optional[Iterable[str]] = None,
                 parent: Optional['ConstraintNode'] = None,
                 constraint_tuple: Optional[Tuple[str, str, Any]] = None,  # normalized predicate
                 case_output: Optional[Any] = None,         # THEN value for CASE branch
                 is_else_branch: bool = False):             # marks CASE ELSE branch
        self.id = next(ConstraintNode._id_counter)
        self.name = name
        self.node_type = node_type
        self.op = op
        self.projection: Set[str] = set(projection or [])
        self.parent = parent
        self.children: List['ConstraintNode'] = []
        self.constraint = constraint_tuple
        self.case_output = case_output
        self.is_else_branch = is_else_branch

    # ---- Structure ----
    def add_child(self, child: 'ConstraintNode') -> None:
        self.children.append(child)
        child.parent = self

    def is_root(self) -> bool:
        return self.node_type == NodeKind.ROOT

    def is_leaf(self) -> bool:
        return len(self.children) == 0

    # ---- Path utilities ----
    def path_nodes(self) -> List['ConstraintNode']:
        node, out = self, []
        while node and not node.is_root():
            out.append(node)
            node = node.parent
        return list(reversed(out))

    def path_constraints(self) -> List[str]:
        labels: List[str] = []
        for n in self.path_nodes():
            if n.node_type == NodeKind.SET_OP and n.op:
                labels.append(n.op)
            elif n.node_type in (NodeKind.CONSTRAINT, NodeKind.CASE):
                if n.name:
                    if n.node_type == NodeKind.CASE and n.case_output is not None:
                        labels.append(f"{n.name} -> {n.case_output}")
                    else:
                        labels.append(n.name)
        return labels

    def path_constraint_tuples(self) -> List[Tuple[str, str, Any]]:
        return [n.constraint for n in self.path_nodes() if n.constraint is not None]

    def path_projections(self) -> Set[str]:
        node, projs = self, set()
        while node:
            projs |= node.projection
            node = node.parent
        return projs

    def path_setops(self) -> List[str]:
        return [n.op for n in self.path_nodes() if n.node_type == NodeKind.SET_OP and n.op]

    def __repr__(self) -> str:
        nm = self.name if self.name else "ROOT"
        tag = self.node_type.upper() + (f":{self.op}" if self.op else "")
        if self.node_type == NodeKind.CASE and self.case_output is not None:
            nm = f"{nm} -> {self.case_output}"
        return f"<{tag}#{self.id} {nm}>"


# =========================================================
# Path specs + Coverage
# =========================================================

@dataclass(frozen=True)
class PathKey:
    sequence: Tuple[str, ...]    # ordered labels (constraints, set-ops, case branches)

@dataclass
class PathSpec:
    key: PathKey
    constraints: List[str]                       # textual labels for display
    constraint_tuples: List[Tuple[str, str, Any]]
    projection: Set[str]
    setops: List[str]


class ConstraintTreeExplorer:
    def __init__(self, root: ConstraintNode):
        assert root.is_root(), "Root node must have node_type='root'."
        self.root = root
        self._covered: Set[PathKey] = set()

    # Enumerate all leaf paths (each leaf implies a full execution path)
    def enumerate_paths(self) -> List[PathSpec]:
        leaves = self._collect_leaves(self.root)
        specs: List[PathSpec] = []
        for leaf in leaves:
            constraints = leaf.path_constraints()
            ctuples = leaf.path_constraint_tuples()
            projection = leaf.path_projections()
            setops = leaf.path_setops()
            key = PathKey(tuple(constraints))
            specs.append(PathSpec(key=key,
                                  constraints=constraints,
                                  constraint_tuples=ctuples,
                                  projection=projection,
                                  setops=setops))
        return specs

    def _collect_leaves(self, node: ConstraintNode) -> List[ConstraintNode]:
        if node.is_leaf():
            return [node]
        acc: List[ConstraintNode] = []
        for ch in node.children:
            acc.extend(self._collect_leaves(ch))
        return acc

    # Coverage bookkeeping
    def mark_covered(self, path_spec: PathSpec) -> None:
        self._covered.add(path_spec.key)

    def is_covered(self, path_spec: PathSpec) -> bool:
        return path_spec.key in self._covered

    def next_uncovered(self) -> Optional[PathSpec]:
        for spec in self.enumerate_paths():
            if spec.key not in self._covered:
                return spec
        return None

    # ---- Flipping (constraints only; case branches are separate paths) ----
    @staticmethod
    def flip_constraint_text(expr: str) -> str:
        if "!=" in expr: return expr.replace("!=", "=")
        if ">=" in expr: return expr.replace(">=", "<")
        if "<=" in expr: return expr.replace("<=", ">")
        if ">"  in expr: return expr.replace(">", "<=")
        if "<"  in expr: return expr.replace("<", ">=")
        if "="  in expr: return expr.replace("=", "!=")
        return f"NOT({expr})"

    @staticmethod
    def flip_constraint_tuple(ct: Tuple[str, str, Any]) -> Tuple[str, str, Any]:
        col, op, val = ct
        flip = {">":"<=", "<=":">", "<":">=", ">=":"<", "=":"!=", "!=":"="}.get(op)
        return (col, flip, val) if flip else (col, "NOT", val)

    def propose_flipped_targets(self, covered: PathSpec) -> List[PathSpec]:
        out: List[PathSpec] = []
        for i, (ctext, ctuple) in enumerate(zip(covered.constraints, covered.constraint_tuples)):
            flipped_text = self.flip_constraint_text(ctext)
            flipped_tuple = self.flip_constraint_tuple(ctuple)
            new_constraints = covered.constraints[:i] + [flipped_text]
            new_ctuples = covered.constraint_tuples[:i] + [flipped_tuple]
            key = PathKey(tuple(new_constraints))
            out.append(PathSpec(key=key,
                                constraints=new_constraints,
                                constraint_tuples=new_ctuples,
                                projection=set(covered.projection),
                                setops=list(covered.setops)))
        return out

    def next_target_after(self, covered: PathSpec) -> Optional[PathSpec]:
        for t in self.propose_flipped_targets(covered):
            if t.key not in self._covered:
                return t
        return self.next_uncovered()


# =========================================================
# Heuristic Value Factory (no solver)
# =========================================================

class ValueFactory:
    """Deterministic, simplistic value picker that satisfies conjunctions of
    (col, op, val). Replace with solver-backed logic when needed.
    """
    def __init__(self):
        self.default_num = 0
        self.bump = 1
        self.big_bump = 1000

    def pick(self, constraints: List[Tuple[str, str, Any]]) -> Dict[str, Any]:
        per_col: Dict[str, List[Tuple[str, str, Any]]] = {}
        for c in constraints:
            per_col.setdefault(c[0], []).append(c)

        result: Dict[str, Any] = {}
        for col, cs in per_col.items():
            result[col] = self._pick_for_column(cs)
        return result

    def _pick_for_column(self, cs: List[Tuple[str, str, Any]]) -> Any:
        lb = None
        ub = None
        eq = None
        neq = set()

        for _, op, val in cs:
            if isinstance(val, (int, float)):
                if op == ">":   lb = max(lb, val + self.bump) if lb is not None else val + self.bump
                elif op == ">=": lb = max(lb, val) if lb is not None else val
                elif op == "<":   ub = min(ub, val - self.bump) if ub is not None else val - self.bump
                elif op == "<=": ub = min(ub, val) if ub is not None else val
                elif op == "=":   eq = val
                elif op == "!=":  neq.add(val)
            else:
                if op == "=":   eq = val
                elif op == "!=": neq.add(val)

        if eq is not None:
            v = eq
            if v in neq and isinstance(v, (int, float)):
                v = v + self.big_bump
            return v

        if lb is not None or ub is not None:
            if lb is None: lb = -10**6
            if ub is None: ub =  10**6
            if lb > ub:    lb, ub = ub, lb
            v = (lb + ub) / 2
            if isinstance(v, float) and v.is_integer():
                v = int(v)
            while v in neq:
                v += self.bump
            return v

        v = self.default_num
        while v in neq:
            v += self.bump
        return v


# =========================================================
# Tuple Planning with DISTINCT / multiplicity
# =========================================================

@dataclass
class TuplePlan:
    path: PathSpec
    tuples: List[Dict[str, Any]] = field(default_factory=list)
    note: str = ""


def plan_minimal_tuples_for_path(path: PathSpec,
                                 *,
                                 distinct_on: Optional[Iterable[str]] = None,
                                 min_distinct: int = 2,
                                 seed_overrides: Optional[Dict[str, Any]] = None,
                                 value_factory: Optional[ValueFactory] = None
                                 ) -> TuplePlan:
    vf = value_factory or ValueFactory()
    base = vf.pick(path.constraint_tuples)
    if seed_overrides:
        base.update(seed_overrides)

    tuples = [dict(base)]

    # DISTINCT handling: default to path.projection if not specified
    keys = list(distinct_on or path.projection)
    if keys:
        def key_of(t): return tuple(t.get(k) for k in keys)
        seen = {key_of(tuples[0])}
        while len(seen) < max(1, min_distinct):
            t2 = dict(base)
            for k in keys:
                v = t2.get(k, 0)
                if isinstance(v, (int, float)):
                    t2[k] = v + 1 + len(seen)
                else:
                    t2[k] = f"{v}_d{len(seen)}"
            key = key_of(t2)
            if key not in seen:
                tuples.append(t2)
                seen.add(key)

    return TuplePlan(path=path, tuples=tuples,
                     note=("DISTINCT on " + ", ".join(keys)) if keys else "No DISTINCT")


# =========================================================
# Set-ops coverage helpers (UNION/INTERSECT/EXCEPT)
# =========================================================

@dataclass
class SetOpCoveragePlan:
    op: str
    branches: List[TuplePlan]
    shared_key_note: str = ""


def _collect_under(node: ConstraintNode) -> List[PathSpec]:
    assert node.node_type == NodeKind.SET_OP
    tmp_root = ConstraintNode(node_type=NodeKind.ROOT)
    tmp_root.add_child(node)
    exp = ConstraintTreeExplorer(tmp_root)
    specs = exp.enumerate_paths()
    return [s for s in specs if node.op in s.setops]


def plan_union_coverage(node: ConstraintNode,
                        *,
                        distinct_on: Optional[Iterable[str]] = None,
                        min_distinct: int = 1) -> SetOpCoveragePlan:
    assert node.node_type == NodeKind.SET_OP and node.op == "UNION"
    specs = _collect_under(node)
    plans = [plan_minimal_tuples_for_path(s, distinct_on=distinct_on, min_distinct=min_distinct)
             for s in specs]
    return SetOpCoveragePlan(op="UNION", branches=plans)


def plan_intersect_coverage(node: ConstraintNode,
                            *,
                            distinct_on: Optional[Iterable[str]] = None,
                            min_distinct: int = 1) -> SetOpCoveragePlan:
    assert node.node_type == NodeKind.SET_OP and node.op == "INTERSECT"
    specs = _collect_under(node)
    if not specs:
        return SetOpCoveragePlan(op="INTERSECT", branches=[])

    vf = ValueFactory()
    base_plans = [plan_minimal_tuples_for_path(s, distinct_on=distinct_on, min_distinct=min_distinct, value_factory=vf)
                  for s in specs]

    if distinct_on:
        key_cols = list(distinct_on)
    else:
        key_cols = list(set.intersection(*[p.path.projection for p in base_plans])) if base_plans else []

    if key_cols:
        shared_values = {k: base_plans[0].tuples[0].get(k, 0) for k in key_cols}
        for plan in base_plans:
            if not plan.tuples:
                continue
            for k, v in shared_values.items():
                plan.tuples[0][k] = v
        note = f"Shared INTERSECT keys: {', '.join(key_cols)}"
    else:
        note = "No common projection keys; INTERSECT requires upstream alignment."

    return SetOpCoveragePlan(op="INTERSECT", branches=base_plans, shared_key_note=note)


def plan_except_coverage(node: ConstraintNode,
                         *,
                         left_branch_index: int = 0,
                         distinct_on: Optional[Iterable[str]] = None) -> SetOpCoveragePlan:
    """For EXCEPT: ensure a tuple exists in LEFT branch but not in RIGHT.
    left_branch_index selects which child is considered LEFT (default 0).
    """
    assert node.node_type == NodeKind.SET_OP and node.op == "EXCEPT"
    specs = _collect_under(node)
    if len(specs) < 2:
        return SetOpCoveragePlan(op="EXCEPT", branches=[])

    left = specs[left_branch_index]
    right = specs[1 - left_branch_index]

    left_plan = plan_minimal_tuples_for_path(left, distinct_on=distinct_on, min_distinct=1)
    right_plan = plan_minimal_tuples_for_path(right, distinct_on=distinct_on, min_distinct=1)

    # Ensure LEFT tuple differs on the EXCEPT key(s) from RIGHT's tuple so it doesn't get removed.
    keys = list(distinct_on or (left.projection & right.projection))
    if keys and left_plan.tuples and right_plan.tuples:
        for k in keys:
            lv = left_plan.tuples[0].get(k, 0)
            rv = right_plan.tuples[0].get(k, 0)
            if lv == rv:
                # nudge left value
                if isinstance(lv, (int, float)):
                    left_plan.tuples[0][k] = lv + 123
                else:
                    left_plan.tuples[0][k] = f"{lv}_except"
        note = f"EXCEPT keys: {', '.join(keys)}"
    else:
        note = "No shared keys; EXCEPT semantics may be ill-formed."

    return SetOpCoveragePlan(op="EXCEPT", branches=[left_plan, right_plan], shared_key_note=note)


# =========================================================
# CASE/WHEN helpers
# =========================================================

def add_case(root_parent: ConstraintNode,
             case_label: str,
             *,
             projection: Optional[Iterable[str]] = None,
             branches: List[Tuple[str, Optional[Tuple[str,str,Any]], Any, bool]]) -> ConstraintNode:
    """Add a CASE node under root_parent.

    branches: list of tuples (name, guard_constraint_tuple, then_value, is_else)
      - name: textual label for WHEN/ELSE (e.g., "age < 18" or "ELSE")
      - guard_constraint_tuple: normalized predicate or None for ELSE
      - then_value: the CASE output value for this branch
      - is_else: True if ELSE branch
    """
    case_node = ConstraintNode(case_label, node_type=NodeKind.CASE, projection=projection)
    root_parent.add_child(case_node)
    for name, guard, then_val, is_else in branches:
        branch_node = ConstraintNode(name,
                                     node_type=NodeKind.CASE,
                                     projection=projection,
                                     constraint_tuple=guard,
                                     case_output=then_val,
                                     is_else_branch=is_else)
        case_node.add_child(branch_node)
    return case_node


# =========================================================
# Demo build including CASE WHEN
# =========================================================

def build_demo_tree_with_case() -> ConstraintNode:
    root = ConstraintNode(node_type=NodeKind.ROOT)

    # SELECT DISTINCT id, name, age_group FROM users
    # WHERE age > 18 AND status='active'
    # age_group = CASE WHEN age < 30 THEN 'youth' WHEN age <= 65 THEN 'adult' ELSE 'senior' END
    users_scan = ConstraintNode("TableScan(users)", projection={"id", "name", "age_group", "age", "status"})
    root.add_child(users_scan)

    c_age = ConstraintNode("users.age > 18", projection={"id","name","age_group"}, constraint_tuple=("age", ">", 18))
    users_scan.add_child(c_age)

    c_status = ConstraintNode("users.status = 'active'", projection={"id","name","age_group"}, constraint_tuple=("status", "=", "active"))
    c_age.add_child(c_status)

    # CASE age -> age_group
    add_case(
        c_status,
        "CASE(age)",
        projection={"age_group"},
        branches=[
            ("age < 30", ("age", "<", 30), "youth", False),
            ("age <= 65", ("age", "<=", 65), "adult", False),
            ("ELSE", None, "senior", True),
        ],
    )

    # UNION branch: orders vs archived_orders
    union = ConstraintNode("UNION", node_type=NodeKind.SET_OP, op="UNION", projection={"order_id", "amount"})
    root.add_child(union)

    orders = ConstraintNode("TableScan(orders)", projection={"order_id", "amount"})
    arch_orders = ConstraintNode("TableScan(archived_orders)", projection={"order_id", "amount"})
    union.add_child(orders); union.add_child(arch_orders)

    o_amt = ConstraintNode("orders.amount > 100", projection={"order_id", "amount"}, constraint_tuple=("amount", ">", 100))
    a_amt = ConstraintNode("archived_orders.amount > 50", projection={"order_id", "amount"}, constraint_tuple=("amount", ">", 50))
    orders.add_child(o_amt); arch_orders.add_child(a_amt)

    # INTERSECT inventory example
    inter = ConstraintNode("INTERSECT", node_type=NodeKind.SET_OP, op="INTERSECT", projection={"sku", "qty"})
    root.add_child(inter)
    inv_a = ConstraintNode("Scan(inv_a)", projection={"sku", "qty"})
    inv_b = ConstraintNode("Scan(inv_b)", projection={"sku", "qty"})
    inter.add_child(inv_a); inter.add_child(inv_b)
    inv_a.add_child(ConstraintNode("qty >= 5", projection={"sku","qty"}, constraint_tuple=("qty", ">=", 5)))
    inv_b.add_child(ConstraintNode("qty <= 20", projection={"sku","qty"}, constraint_tuple=("qty", "<=", 20)))

    return root


# =========================================================
# Example usage (run directly)
# =========================================================

if __name__ == "__main__":
    root = build_demo_tree_with_case()
    explorer = ConstraintTreeExplorer(root)

    # Enumerate all paths
    all_paths = explorer.enumerate_paths()
    print(f"Total leaf paths: {len(all_paths)}")
    print("Example path labels:")
    for p in all_paths[:5]:
        print("  ", p.constraints)

    # Pick a users path (with CASE) and plan tuples with DISTINCT over projection
    users_paths = [p for p in all_paths if any("users.age > 18" in x for x in p.constraints)]
    if users_paths:
        p0 = users_paths[0]
        explorer.mark_covered(p0)
        tp = plan_minimal_tuples_for_path(p0, distinct_on=p0.projection, min_distinct=2)
        print("\nUsers path plan:")
        print("  projection:", p0.projection)
        print("  tuples:", tp.tuples)

        # Concolic-style flipping proposals
        flips = explorer.propose_flipped_targets(p0)
        print("\nFlip targets (first 3):")
        for f in flips[:3]:
            print("  ->", f.constraints)

    # Plan UNION coverage (one tuple per branch)
    union_node = next(n for n in root.children if n.node_type==NodeKind.SET_OP and n.op=="UNION")
    union_plan = plan_union_coverage(union_node, distinct_on={"order_id"}, min_distinct=1)
    print("\nUNION coverage (per branch tuples):")
    for i, br in enumerate(union_plan.branches):
        print(f"  Branch {i}:", br.tuples)

    # Plan INTERSECT coverage (align on shared keys, e.g., sku)
    inter_node = next(n for n in root.children if n.node_type==NodeKind.SET_OP and n.op=="INTERSECT")
    inter_plan = plan_intersect_coverage(inter_node, distinct_on={"sku"}, min_distinct=1)
    print("\nINTERSECT coverage:", inter_plan.shared_key_note)
    for i, br in enumerate(inter_plan.branches):
        print(f"  Branch {i}:", br.tuples)
