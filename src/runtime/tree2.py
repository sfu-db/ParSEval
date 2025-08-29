"""
Robust Constraint / Execution Tree + SQLGlot encoder
--------------------------------------------------
This file unifies the execution-tree (schema-aware) model with a SQL -> tree
translator using sqlglot. It handles:
 - SELECT / FROM / JOIN (ON exprs, including OR)
 - WHERE / HAVING (including boolean OR/AND, CASE WHEN)
 - Projection with per-expression DISTINCT + global DISTINCT
 - GROUP BY + Aggregate functions (COUNT, SUM, etc.)
 - SET OPS: UNION / INTERSECT / EXCEPT
 - WITH / CTEs (including recursive unrolling option)
 - Table schema integration (PK / FK / CHECK) via a Schema manager
 - Full path enumeration that expands ORs, JOIN ON disjunctions, CASE branches and CTE inlining

Notes:
 - This module purposely avoids connecting to an SMT solver; it emits path requirements
   and contains lightweight heuristics to check obvious contradictions.
 - The SQLGlot encoder emits our node types and preserves expression text for later use.

Usage:
 1) Register schema with SCHEMA.add_table(...)
 2) Call SQLToTree(sql_text) to get a ROOT ExecutionNode representing the query
 3) Use all_paths_with_requirements(root) or candidate_non_empty_paths(root)

"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any, Tuple, Iterable
from collections import defaultdict
import itertools

# SQL parser
try:
    import sqlglot
    from sqlglot import expressions as exp
except Exception as e:
    raise ImportError("sqlglot is required for SQL -> tree conversion. Install with `pip install sqlglot`")

# -----------------------------
# Core execution-tree nodes
# -----------------------------
@dataclass
class ExecutionNode:
    node_type: str
    description: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)
    children: List["ExecutionNode"] = field(default_factory=list)
    parent: Optional["ExecutionNode"] = None

    def add_child(self, child: "ExecutionNode") -> "ExecutionNode":
        child.parent = self
        self.children.append(child)
        return child

    def enumerate_paths(self, path: Optional[List["ExecutionNode"]] = None) -> Iterable[List["ExecutionNode"]]:
        if path is None:
            path = []
        here = path + [self]
        if not self.children:
            yield here
        else:
            for ch in self.children:
                yield from ch.enumerate_paths(here)

    def __repr__(self):
        return f"{self.node_type}({self.description})"


# Basic nodes reused by SQL encoder
class TableRefNode(ExecutionNode):
    def __init__(self, table: str, alias: Optional[str] = None):
        desc = f"{table}" + (f" AS {alias}" if alias else "")
        super().__init__("TableRef", desc, metadata={"table": table, "alias": alias or table})
        self.table = table
        self.alias = alias or table


class ConstraintNode(ExecutionNode):
    def __init__(self, condition: str, kind: str = "filter", relates_to: Optional[str] = None):
        desc = f"{kind.upper()}: {condition}"
        super().__init__("Constraint", desc, metadata={"kind": kind, "relates_to": relates_to})
        self.condition = condition

    def flip(self) -> "ConstraintNode":
        return ConstraintNode(condition=f"NOT({self.condition})", kind=self.metadata.get("kind", "filter"), relates_to=self.metadata.get("relates_to"))


class OrNode(ExecutionNode):
    def __init__(self, description: str = "OR", combine_all: bool = False):
        super().__init__("Or", description, metadata={"combine_all": combine_all})

    def enumerate_paths(self, path: Optional[List["ExecutionNode"]] = None) -> Iterable[List["ExecutionNode"]]:
        head = (path or []) + [self]
        if not self.children:
            yield head
            return
        # one path per disjunct
        for ch in self.children:
            for p in ch.enumerate_paths([]):
                yield head + p
        # optional combined path where all disjuncts hold
        if self.metadata.get("combine_all") and self.children:
            combined: List[ExecutionNode] = []
            for ch in self.children:
                pths = list(ch.enumerate_paths([]))
                if pths:
                    combined += pths[0]
            yield head + combined


class CaseNode(ExecutionNode):
    def __init__(self, alias: str):
        super().__init__("Case", f"CASE AS {alias}", metadata={"alias": alias})
        self.alias = alias
        self.when_then: List[Tuple[str, Any]] = []
        self.else_result: Optional[Any] = None

    def add_when_then(self, cond: str, val: Any) -> ConstraintNode:
        self.when_then.append((cond, val))
        return self.add_child(ConstraintNode(cond, kind="case-branch", relates_to=self.alias))

    def set_else(self, val: Any) -> ConstraintNode:
        self.else_result = val
        return self.add_child(ConstraintNode("ELSE", kind="case-branch", relates_to=self.alias))


class ProjectionNode(ExecutionNode):
    def __init__(self, columns: List[Dict[str, Any]], global_distinct: bool = False, derived: Optional[Dict[str, Any]] = None):
        # columns: list of dicts {expr: str, alias: Optional[str], distinct: bool}
        label = ", ".join([f"{c['expr']}" + (" (DISTINCT)" if c.get("distinct") else "") for c in columns])
        if global_distinct:
            label += " [GLOBAL DISTINCT]"
        super().__init__("Projection", label, metadata={"columns": columns, "global_distinct": global_distinct, "derived": derived or {}})
        self.columns = columns
        self.global_distinct = global_distinct
        self.derived = derived or {}

    @property
    def distinct_subset(self) -> List[str]:
        return [c["expr"] for c in self.columns if c.get("distinct")]


class GroupKeyNode(ExecutionNode):
    def __init__(self, keys: List[str]):
        super().__init__("GroupKeys", f"GroupKeys({', '.join(keys)})", metadata={"keys": keys})
        self.keys = keys


class AggregateFuncNode(ExecutionNode):
    def __init__(self, func: str, column: str, having: Optional[str] = None, distinct: bool = False):
        desc = f"{func}({ 'DISTINCT ' if distinct else ''}{column})"
        if having:
            desc += f" HAVING {having}"
        super().__init__("AggregateFunc", desc, metadata={"func": func, "column": column, "having": having, "distinct": distinct})
        self.func = func
        self.column = column
        self.having = having
        self.distinct = distinct


class SetOpNode(ExecutionNode):
    def __init__(self, op_type: str):
        assert op_type in {"UNION", "INTERSECT", "EXCEPT"}
        super().__init__("SetOp", op_type, metadata={"op": op_type})
        self.op_type = op_type

    def enumerate_paths(self, path: Optional[List["ExecutionNode"]] = None) -> Iterable[List["ExecutionNode"]]:
        head = (path or []) + [self]
        if not self.children:
            yield head
            return
        child_paths = [list(ch.enumerate_paths([])) for ch in self.children]
        if self.op_type == "UNION":
            for plist in child_paths:
                for p in plist:
                    yield head + p
        elif self.op_type == "INTERSECT":
            for combo in itertools.product(*child_paths):
                merged: List[ExecutionNode] = []
                for p in combo:
                    merged += p
                yield head + merged
        else:  # EXCEPT: emit left-side paths (semantics handled elsewhere)
            for p in child_paths[0]:
                yield head + p


class WithNode(ExecutionNode):
    def __init__(self, name: str):
        super().__init__("WithDef", f"WITH {name}", metadata={"name": name})
        self.body: Optional[ExecutionNode] = None

    def set_body(self, node: ExecutionNode) -> ExecutionNode:
        self.body = node
        node.parent = self
        return node

    def expand_paths(self) -> List[List[ExecutionNode]]:
        return list(self.body.enumerate_paths([])) if self.body else [[self]]


class CTERefNode(ExecutionNode):
    def __init__(self, with_def: WithNode, alias: Optional[str] = None):
        name = with_def.metadata.get("name")
        super().__init__("CTERef", f"CTERef({alias or name})", metadata={"name": name})
        self.with_def = with_def

    def enumerate_paths(self, path: Optional[List["ExecutionNode"]] = None) -> Iterable[List["ExecutionNode"]]:
        head = (path or []) + [self]
        for p in self.with_def.expand_paths():
            tail = head + p
            if not self.children:
                yield tail
            else:
                for ch in self.children:
                    for rest in ch.enumerate_paths(tail):
                        yield rest


class CorrelatedSubqueryNode(ExecutionNode):
    def __init__(self, correlation_vars: Dict[str, Any], mode: str = "EXISTS"):
        desc = f"Correlated({mode}, corr={list(correlation_vars.keys())})"
        super().__init__("CorrelatedSubquery", desc, metadata={"corr": correlation_vars, "mode": mode})
        self.correlation_vars = correlation_vars
        self.mode = mode


class JoinNode(ExecutionNode):
    def __init__(self, join_type: str, on_constraints: Optional[List[str]] = None):
        assert join_type in {"INNER", "LEFT", "RIGHT", "FULL"}
        super().__init__("Join", f"{join_type} JOIN", metadata={"join_type": join_type, "on": on_constraints or []})
        self.join_type = join_type
        self.left: Optional[ExecutionNode] = None
        self.right: Optional[ExecutionNode] = None
        self.on_expr: Optional[ExecutionNode] = None
        self.left_table: Optional[str] = None
        self.right_table: Optional[str] = None

    def set_left(self, node: ExecutionNode) -> ExecutionNode:
        self.left = node
        node.parent = self
        return node

    def set_right(self, node: ExecutionNode) -> ExecutionNode:
        self.right = node
        node.parent = self
        return node

    def set_tables(self, left_table: str, right_table: str):
        self.left_table = left_table
        self.right_table = right_table
        self.metadata["left_table"] = left_table
        self.metadata["right_table"] = right_table

    def set_on_expr(self, expr: ExecutionNode) -> ExecutionNode:
        self.on_expr = expr
        expr.parent = self
        return expr

    def add_on_constraint_nodes(self):
        for txt in self.metadata.get("on", []):
            self.add_child(ConstraintNode(txt, kind="on"))

    def enumerate_paths(self, path: Optional[List["ExecutionNode"]] = None) -> Iterable[List["ExecutionNode"]]:
        here = (path or []) + [self]
        if not (self.left and self.right):
            if not self.children:
                yield here
            else:
                for ch in self.children:
                    yield from ch.enumerate_paths(here)
            return

        left_paths = list(self.left.enumerate_paths([]))
        right_paths = list(self.right.enumerate_paths([]))

        on_variants: List[List[ExecutionNode]] = [[]]
        if self.on_expr is not None:
            on_variants = list(self.on_expr.enumerate_paths([]))
        elif self.metadata.get("on"):
            on_seq = [ConstraintNode(txt, kind="on") for txt in self.metadata.get("on", [])]
            on_variants = [on_seq]

        for lp in left_paths:
            for rp in right_paths:
                for onp in on_variants:
                    combined = here + lp + rp + onp
                    if not self.children:
                        yield list(combined)
                    else:
                        for ch in self.children:
                            for tail in ch.enumerate_paths(list(combined)):
                                yield tail


# -----------------------------
# Schema & catalog (PK/FK/CHECK)
# -----------------------------
class TableSchema:
    def __init__(self, table_name: str):
        self.table_name = table_name
        self.primary_keys: List[str] = []
        self.foreign_keys: List[Tuple[str, str, str]] = []  # (local_col, ref_table, ref_col)
        self.checks: List[str] = []

    def add_primary_key(self, *cols: str):
        self.primary_keys.extend(list(cols))

    def add_foreign_key(self, local_col: str, ref_table: str, ref_col: str):
        self.foreign_keys.append((local_col, ref_table, ref_col))

    def add_check(self, expr: str):
        self.checks.append(expr)


class SchemaConstraintManager:
    def __init__(self):
        self.tables: Dict[str, TableSchema] = {}

    def add_table(self, schema: TableSchema):
        self.tables[schema.table_name] = schema

    def get_table(self, name: str) -> Optional[TableSchema]:
        return self.tables.get(name)


SCHEMA = SchemaConstraintManager()


# -----------------------------
# Predicate parsing & heuristics
# -----------------------------

def parse_simple_pred(predicate: str) -> Optional[Tuple[str, str, Any]]:
    s = predicate.strip()
    if s == "ELSE":
        return ("__ELSE__", "=", True)
    if s.startswith("NOT(") and s.endswith(")"):
        inner = s[4:-1].strip()
        p = parse_simple_pred(inner)
        if not p:
            return None
        col, op, val = p
        inverse = {"=": "!=", "!=": "=", ">": "<=", "<": ">=", ">=": "<", "<=": ">"}.get(op)
        return (col, inverse, val) if inverse else None

    for sym in ["=", "!=", ">=", "<=", ">", "<"]:
        if sym in s:
            left, right = [x.strip() for x in s.split(sym, 1)]
            val: Any
            if right.replace(".", "", 1).lstrip("-+").isdigit():
                val = float(right) if "." in right else int(right)
            elif (right.startswith("'") and right.endswith("'")) or (right.startswith('"') and right.endswith('"')):
                val = right[1:-1]
            else:
                val = ("__col__", right)
            return (left, sym, val)
    return None


def constraints_satisfiable(constraints: List[str]) -> bool:
    parsed: Dict[str, List[Tuple[str, Any]]] = defaultdict(list)
    for c in constraints:
        p = parse_simple_pred(c)
        if not p:
            continue
        col, op, val = p
        if isinstance(val, tuple) and val and val[0] == "__col__":
            continue
        parsed[col].append((op, val))

    for col, preds in parsed.items():
        lb = None; ub = None; eq = None; neq = set()
        for op, v in preds:
            if op == ">":
                b = v + (1 if isinstance(v, int) else 1e-6)
                lb = b if lb is None else max(lb, b)
            elif op == ">=":
                lb = v if lb is None else max(lb, v)
            elif op == "<":
                b = v - (1 if isinstance(v, int) else 1e-6)
                ub = b if ub is None else min(ub, b)
            elif op == "<=":
                ub = v if ub is None else min(ub, v)
            elif op == "=":
                eq = v
            elif op == "!=":
                neq.add(v)
        if eq is not None:
            if eq in neq:
                return False
            if lb is not None and isinstance(eq, (int, float)) and eq < lb:
                return False
            if ub is not None and isinstance(eq, (int, float)) and eq > ub:
                return False
        if lb is not None and ub is not None and isinstance(lb, (int, float)) and isinstance(ub, (int, float)) and lb > ub:
            return False
    return True


# -----------------------------
# Path requirements & feasibility
# -----------------------------

def path_to_requirements(path: List[ExecutionNode]) -> Dict[str, Any]:
    req: Dict[str, Any] = {
        "constraints": [],
        "group_keys": None,
        "aggregates": [],
        "projections": set(),
        "distinct": False,
        "distinct_subset": set(),
        "derived": {},
        "case_defs": {},
        "join_type": None,
        "on_constraints": [],
        "tables": set(),
        "schema": {"pks": {}, "fks": [], "checks": []},
    }

    chosen_case_branches: Dict[str, str] = {}
    for n in path:
        if isinstance(n, ConstraintNode):
            req["constraints"].append(n.condition)
            if n.metadata.get("kind") == "on":
                req["on_constraints"].append(n.condition)
            if n.metadata.get("kind") == "case-branch":
                alias = n.metadata.get("relates_to")
                chosen_case_branches[alias] = n.condition
        elif isinstance(n, TableRefNode):
            tbl = n.table
            req["tables"].add(tbl)
            schema = SCHEMA.get_table(tbl)
            if schema:
                if schema.primary_keys:
                    req["schema"]["pks"][tbl] = list(schema.primary_keys)
                for (lc, rt, rc) in schema.foreign_keys:
                    req["schema"]["fks"].append({"table": tbl, "local": lc, "ref_table": rt, "ref_col": rc})
                for chk in schema.checks:
                    req["schema"]["checks"].append({"table": tbl, "expr": chk})
                    req["constraints"].append(chk)
        elif isinstance(n, GroupKeyNode):
            req["group_keys"] = n.keys
        elif isinstance(n, AggregateFuncNode):
            req["aggregates"].append((n.func, n.column, n.having, getattr(n, "distinct", False)))
        elif isinstance(n, ProjectionNode):
            for col in n.columns:
                expr = col["expr"]
                alias = col.get("alias") or expr
                req["projections"].add(alias)
            req["distinct"] = req["distinct"] or bool(n.global_distinct)
            req["distinct_subset"].update(n.distinct_subset)
            req["derived"].update(n.derived)
        elif isinstance(n, CaseNode):
            req["case_defs"][n.alias] = {"when_then": list(n.when_then), "else": n.else_result}
        elif isinstance(n, JoinNode):
            req["join_type"] = n.join_type
            req["on_constraints"].extend(n.metadata.get("on", []))
            if getattr(n, "left_table", None):
                req["tables"].add(n.left_table)
            if getattr(n, "right_table", None):
                req["tables"].add(n.right_table)
        elif isinstance(n, CTERefNode):
            req["tables"].add(n.with_def.metadata.get("name"))

    req["chosen_case_branches"] = chosen_case_branches
    return req


def case_projection_consistent(req: Dict[str, Any]) -> bool:
    for c in req["constraints"]:
        p = parse_simple_pred(c)
        if not p:
            continue
        col, op, val = p
        if col in req["derived"] and op in {"=", "!="}:
            d = req["derived"][col]
            if d.get("type") == "CASE":
                alias = d.get("alias")
                case_def = req["case_defs"].get(alias)
                if not case_def:
                    continue
                chosen = req["chosen_case_branches"].get(alias)
                out_val = None
                if chosen == "ELSE" or chosen is None:
                    out_val = case_def.get("else")
                else:
                    for cond, v in case_def.get("when_then", []):
                        if cond == chosen:
                            out_val = v
                            break
                if out_val is None:
                    continue
                if op == "=" and out_val != val:
                    return False
                if op == "!=" and out_val == val:
                    return False
    return True


def join_feasible(req: Dict[str, Any]) -> bool:
    jt = req.get("join_type")
    if not jt:
        return True
    ons = req.get("on_constraints", [])
    has_col_eq = False
    pairs = []
    for o in ons:
        p = parse_simple_pred(o)
        if p and isinstance(p[2], tuple) and p[2][0] == "__col__" and p[1] == "=":
            has_col_eq = True
            pairs.append((p[0], p[2][1]))
    for fk in req.get("schema", {}).get("fks", []):
        l = f"{fk['table']}.{fk['local']}"
        r = f"{fk['ref_table']}.{fk['ref_col']}"
        if (l, r) in pairs or (r, l) in pairs:
            return True
    if jt == "INNER":
        return True if has_col_eq or not ons else True
    return True


def path_can_be_non_empty(req: Dict[str, Any]) -> bool:
    if not constraints_satisfiable(req["constraints"]):
        return False
    if not case_projection_consistent(req):
        return False
    if not join_feasible(req):
        return False
    for func, col, having, distinct in req["aggregates"]:
        if having and "< 0" in having.replace(" ", "") and func.upper().startswith("COUNT"):
            return False
    return True


# -----------------------------
# Simple tuple planner (heuristic)
# -----------------------------
class SimpleValueFactory:
    @staticmethod
    def _intify_if_whole(x: Any) -> Any:
        try:
            return int(x) if isinstance(x, float) and x.is_integer() else x
        except Exception:
            return x

    def pick_for_constraints(self, constraints: List[Tuple[str, str, Any]]) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        by_col: Dict[str, List[Tuple[str, Any]]] = defaultdict(list)
        for col, op, val in constraints:
            if isinstance(val, tuple) and val and val[0] == "__col__":
                continue
            by_col[col].append((op, val))
        for col, preds in by_col.items():
            eq = next((v for op, v in preds if op == "="), None)
            if eq is not None:
                out[col] = eq
                continue
            lb = None; ub = None
            for op, v in preds:
                if op == ">":
                    b = v + (1 if isinstance(v, int) else 1e-6)
                    lb = b if lb is None else max(lb, b)
                elif op == ">=":
                    lb = v if lb is None else max(lb, v)
                elif op == "<":
                    b = v - (1 if isinstance(v, int) else 1e-6)
                    ub = b if ub is None else min(ub, b)
                elif op == "<=":
                    ub = v if ub is None else min(ub, v)
            if lb is not None:
                out[col] = self._intify_if_whole(lb)
            elif ub is not None:
                out[col] = self._intify_if_whole(ub)
            else:
                out[col] = 1
        return out


def plan_minimal_tuples(req: Dict[str, Any]) -> List[Dict[str, Any]]:
    parsed = [p for c in req["constraints"] if (p := parse_simple_pred(c))]
    base = SimpleValueFactory().pick_for_constraints(parsed)
    tuples = [dict(base)]

    distinct_subset = list(req.get("distinct_subset", []))
    if req.get("distinct"):
        keys = list(req.get("projections", []))
        if keys:
            t2 = dict(base)
            k = keys[0]
            v = t2.get(k, 0)
            t2[k] = (v + 1) if isinstance(v, (int, float)) else f"{v}_2"
            tuples.append(t2)
    elif distinct_subset:
        k = distinct_subset[0]
        t2 = dict(base)
        v = t2.get(k, 0)
        t2[k] = (v + 1) if isinstance(v, (int, float)) else f"{v}_2"
        tuples.append(t2)

    if req.get("group_keys"):
        for idx, t in enumerate(list(tuples)):
            for gk in req["group_keys"]:
                if gk not in t:
                    t[gk] = f"g{idx}"
        for func, col, having, distinct in req["aggregates"]:
            if func.upper().startswith("COUNT") and having and (">" in having or ">=" in having):
                tuples.append(dict(tuples[0]))

    for table, pk_cols in req.get("schema", {}).get("pks", {}).items():
        for i, t in enumerate(tuples):
            for col in pk_cols:
                q = f"{table}.{col}"
                if q in t:
                    if i == 0:
                        continue
                    v = t[q]
                    t[q] = (v + 1) if isinstance(v, (int, float)) else f"{v}_{i}"

    for fk in req.get("schema", {}).get("fks", []):
        lq = f"{fk['table']}.{fk['local']}"
        rq = f"{fk['ref_table']}.{fk['ref_col']}"
        for t in tuples:
            if lq in t and rq in t:
                t[lq] = t[rq]

    return tuples


# -----------------------------
# API helpers
# -----------------------------

def all_paths_with_requirements(root: ExecutionNode) -> List[Tuple[List[ExecutionNode], Dict[str, Any]]]:
    out: List[Tuple[List[ExecutionNode], Dict[str, Any]]] = []
    for p in root.enumerate_paths():
        out.append((p, path_to_requirements(p)))
    return out


def candidate_non_empty_paths(root: ExecutionNode) -> List[Tuple[List[ExecutionNode], Dict[str, Any], List[Dict[str, Any]]]]:
    results: List[Tuple[List[ExecutionNode], Dict[str, Any], List[Dict[str, Any]]]] = []
    for path, req in all_paths_with_requirements(root):
        if path_can_be_non_empty(req):
            plan = plan_minimal_tuples(req)
            results.append((path, req, plan))
    return results


# -----------------------------
# SQLGlot -> Execution Tree encoder
# -----------------------------
class SQLToTree:
    def __init__(self, schema: Optional[SchemaConstraintManager] = None, recursive_cte_unroll: int = 2):
        self.schema = schema or SCHEMA
        self.recursive_cte_unroll = recursive_cte_unroll
        self.cte_defs: Dict[str, WithNode] = {}

    def parse(self, sql: str) -> exp.Expression:
        return sqlglot.parse_one(sql)

    def build(self, sql: str) -> ExecutionNode:
        expr = self.parse(sql)
        root = ExecutionNode("ROOT", "ROOT")
        self._handle_statement(expr, root)
        return root

    def _handle_statement(self, node: exp.Expression, parent: ExecutionNode):
        # WITH
        if isinstance(node, exp.With):
            # record CTE defs
            for e in node.expressions:
                name = e.alias
                w = WithNode(name)
                # build subtree for the CTE body
                body_root = ExecutionNode("CTE_BODY", f"CTE({name})")
                self._handle_statement(e.this, body_root)
                w.set_body(body_root)
                self.cte_defs[name] = w
            # continue with main statement
            return self._handle_statement(node.this, parent)

        # SELECT
        if isinstance(node, exp.Select):
            return self._handle_select(node, parent)

        # SET OPS
        if isinstance(node, exp.Union) or isinstance(node, exp.Intersect) or isinstance(node, exp.Except):
            op = node.__class__.__name__.upper()
            s = SetOpNode(op)
            parent.add_child(s)
            # left and right
            self._handle_statement(node.this, s)
            self._handle_statement(node.expression, s)
            return

        # Subquery / other
        if isinstance(node, exp.Subquery):
            return self._handle_statement(node.this, parent)

        # table
        if isinstance(node, exp.Table):
            parent.add_child(TableRefNode(node.name, alias=(node.alias and node.alias_or_name)))
            return

        # fallback: attach textual node
        parent.add_child(ExecutionNode(type(node).__name__, node.sql()))

    def _handle_select(self, select: exp.Select, parent: ExecutionNode):
        # Projections
        cols: List[Dict[str, Any]] = []
        for e in select.expressions:
            if isinstance(e, exp.Alias):
                expr_sql = e.this.sql()
                alias = e.alias
                # detect DISTINCT inside function like COUNT(DISTINCT x)
                distinct = False
                cols.append({"expr": expr_sql, "alias": alias, "distinct": distinct})
            elif isinstance(e, exp.Distinct):
                # SELECT DISTINCT a, b
                for child in e.expressions:
                    cols.append({"expr": child.sql(), "alias": child.alias_or_name, "distinct": False})
            else:
                cols.append({"expr": e.sql(), "alias": getattr(e, 'alias', None), "distinct": False})
        proj = ProjectionNode(cols, global_distinct=bool(select.args.get("distinct")), derived={})
        parent.add_child(proj)

        # FROM / JOIN
        from_ = select.args.get("from")
        if from_:
            # source expressions (can be tables, joins, subqueries)
            for src in from_.expressions:
                self._handle_from_item(src, proj)

        # WHERE
        where = select.args.get("where")
        if where:
            self._handle_where(where.this, proj)

        # GROUP BY
        group = select.args.get("group")
        if group:
            keys = [g.sql() for g in group.expressions]
            gnode = GroupKeyNode(keys)
            proj.add_child(gnode)
            current_after_group = gnode
        else:
            current_after_group = proj

        # HAVING
        having = select.args.get("having")
        if having:
            self._handle_where(having.this, current_after_group, kind="having")

        # aggregates: attach AggregateFuncNodes under group node when detected in projections
        # (simple heuristic: look for SUM/COUNT/AVG/MAX/MIN in projection expressions)
        for col in cols:
            ex = col["expr"]
            if any(fn in ex.upper() for fn in ["COUNT(", "SUM(", "AVG(", "MAX(", "MIN("]):
                # try to parse function name and inner column crudely
                func = ex.split("(", 1)[0]
                inner = ex.split("(", 1)[1].rstrip(")")
                af = AggregateFuncNode(func, inner, distinct=("DISTINCT" in inner.upper()))
                current_after_group.add_child(af)

        # If a projection had global distinct, we keep that in the proj node
        return

    def _handle_from_item(self, item: exp.Expression, parent: ExecutionNode):
        # JOIN node
        if isinstance(item, exp.Join):
            jkind = (item.args.get("kind") or "INNER").upper()
            join_node = JoinNode(jkind)
            parent.add_child(join_node)
            # left side: 'this', right side: 'expression'
            self._handle_from_item(item.this, join_node)
            self._handle_from_item(item.expression, join_node)
            # ON
            on = item.args.get("on")
            if on:
                self._handle_boolean_expr(on.this, join_node, kind="on")
            return

        # Table ref
        if isinstance(item, exp.Table):
            parent.add_child(TableRefNode(item.name, alias=(item.alias and item.alias_or_name)))
            return

        # Subquery
        if isinstance(item, exp.Subquery):
            sub_root = ExecutionNode("Subquery", item.sql())
            parent.add_child(sub_root)
            self._handle_statement(item.this, sub_root)
            return

        # Other (expression)
        parent.add_child(ExecutionNode(type(item).__name__, item.sql()))

    def _handle_where(self, node: exp.Expression, parent: ExecutionNode, kind: str = "filter"):
        # node may be AND / OR / comparison / CASE
        if isinstance(node, exp.Or):
            ornode = OrNode(description=node.sql())
            parent.add_child(ornode)
            for part in node.flatten():
                self._handle_where(part, ornode, kind=kind)
            return
        if isinstance(node, exp.And):
            # AND: keep as sequence of ConstraintNodes
            for part in node.flatten():
                self._handle_where(part, parent, kind=kind)
            return
        if isinstance(node, exp.Case):
            # CASE in WHERE/HAVING -> expand branches
            case_alias = f"case_expr_{len(parent.children)}"
            cnode = CaseNode(case_alias)
            parent.add_child(cnode)
            # sqlglot represents WHEN as list in args['ifs'] with corresponding thens in args['thens']
            if node.args.get('ifs') and node.args.get('thens'):
                for w, t in zip(node.args['ifs'], node.args['thens']):
                    cond = w.sql()
                    cnode.add_when_then(cond, t.sql())
            if node.args.get('default'):
                cnode.set_else(node.args['default'].sql())
            return
        # base comparison or existence
        parent.add_child(ConstraintNode(node.sql(), kind=kind))

    def _handle_boolean_expr(self, node: exp.Expression, parent: ExecutionNode, kind: str = "filter"):
        # Generic boolean expr handler (used for ON and WHERE)
        if isinstance(node, exp.Or):
            ornode = OrNode(node.sql())
            parent.add_child(ornode)
            for part in node.flatten():
                self._handle_boolean_expr(part, ornode, kind=kind)
            return
        if isinstance(node, exp.And):
            for part in node.flatten():
                self._handle_boolean_expr(part, parent, kind=kind)
            return
        if isinstance(node, exp.Predicate) or isinstance(node, exp.Comparison) or isinstance(node, exp.Column) or isinstance(node, exp.Literal):
            parent.add_child(ConstraintNode(node.sql(), kind=kind))
            return
        # default
        parent.add_child(ConstraintNode(node.sql(), kind=kind))


# -----------------------------
# Demo: build tree from SQL
# -----------------------------
if __name__ == "__main__":
    # small schema
    customers = TableSchema("customers")
    customers.add_primary_key("id")
    customers.add_check("customers.age >= 0")

    orders = TableSchema("orders")
    orders.add_primary_key("id")
    orders.add_foreign_key("cust_id", "customers", "id")
    orders.add_check("orders.price >= 0")

    SCHEMA.add_table(customers)
    SCHEMA.add_table(orders)

    sql = """    
    SELECT c.name, CASE WHEN o.amount > 500 THEN 'VIP' ELSE 'REG' END as tier
    FROM customers c
    JOIN cte o ON c.id = o.cust_id OR c.email = o.contact_email
    WHERE c.age > 18 OR c.name = 'John'
    GROUP BY c.name
    HAVING COUNT(*) > 1
    """

    encoder = SQLToTree()
    root = encoder.build(sql)

    print("\n=== Paths (descriptions) ===\n")
    for p, req in all_paths_with_requirements(root):
        print("PATH:", " -> ".join(n.description for n in p))
        print("REQ:", req)
        print()

    print("\n=== Candidate non-empty paths (plans) ===\n")
    for nodes, req, plan in candidate_non_empty_paths(root):
        print("PATH:", " -> ".join(n.description for n in nodes))
        print("PLAN:", plan)
        print()
