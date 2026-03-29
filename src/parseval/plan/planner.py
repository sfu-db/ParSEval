from __future__ import annotations
from typing import List, Set, Tuple, Dict, Optional, TYPE_CHECKING, Any
from functools import reduce, total_ordering
from dataclasses import dataclass, field
from sqlglot.optimizer.scope import Scope, traverse_scope
from sqlglot.planner import Plan, Scan, Aggregate, Join, Sort, SetOperation, Step
from sqlglot import exp
from parseval.plan.rex import *
from .context import Context, DerivedSchema
from parseval.constants import PBit
import logging, math
from parseval.states import non_fatal
from parseval.helper import normalize_name, convert_to_literal

logger = logging.getLogger("parseval.coverage")

if TYPE_CHECKING:
    from parseval.uexpr.uexprs import UExprToConstraint


@dataclass
class ScopeNode:

    node_id: int
    scope: Scope
    dependencies: Set[int] = field(default_factory=set)
    dependents: Set[int] = field(default_factory=set)
    outputs: List[Tuple] = field(default_factory=list)

    def add_dependency(self, node_id: int):
        self.dependencies.add(node_id)

    def add_dependent(self, node_id: int):
        self.dependents.add(node_id)

    @property
    def scope_columns(self) -> Set[exp.Column]:
        """
        Get all columns used in the scope with the given ID.

        Args:
            scope_id: ID of the scope to retrieve columns for

        Returns:
            Set of column names used in the scope
        """
        columns = set()
        column_str = set()
        for column in self.scope.columns:
            if column.sql() not in column_str:
                columns.add(column)
                column_str.add(column.sql())
        return columns


class Graph:
    def __init__(self):
        self.nodes: Dict[int, ScopeNode] = {}
        self.root_node_id: Optional[int] = None

    def add_node(self, node: ScopeNode) -> None:
        """Add a node to the graph.

        Args:
            node: GraphNode to add
        """
        self.nodes[node.node_id] = node

        # Set root if this is the first node
        if self.root_node_id is None:
            self.root_node_id = node.node_id

    def add_edge(self, from_node_id: int, to_node_id: int) -> None:
        """Add a dependency edge between nodes.

        Args:
            from_node_id: ID of dependent node
            to_node_id: ID of dependency node
        """
        if from_node_id in self.nodes and to_node_id in self.nodes:
            self.nodes[from_node_id].add_dependency(to_node_id)
            self.nodes[to_node_id].add_dependent(from_node_id)

    def get_node(self, node_id: int) -> Optional[ScopeNode]:
        """Get a node by its ID.

        Args:
            node_id: ID of the node to retrieve

        Returns:
            The ScopeNode with the given ID, or None if not found
        """
        return self.nodes.get(node_id)

    def get_root_node(self) -> Optional[ScopeNode]:
        """Get the root node (main query).

        Returns:
            Root GraphNode if exists, None otherwise
        """
        if self.root_node_id is not None:
            return self.nodes.get(self.root_node_id)
        return None

    def get_dependency_order(self) -> List[int]:
        """Get topological ordering of nodes for constraint solving.
        Returns:
            List of node IDs in dependency order (dependencies before dependents)
        """
        visited = set()
        order = []

        def visit(node_id: int):
            if node_id in visited:
                return
            visited.add(node_id)
            node = self.nodes[node_id]
            # Visit dependencies first
            for dep_id in node.dependencies:
                visit(dep_id)

            order.append(node_id)

        # Start from root
        if self.root_node_id is not None:
            visit(self.root_node_id)

        # Visit any remaining unvisited nodes
        for node_id in self.nodes:
            visit(node_id)

        return order

    def get_ancestors(self, node_id: int) -> Set[int]:
        """Get all ancestor nodes (transitive dependencies).

        Args:
            node_id: Node ID to find ancestors for

        Returns:
            Set of ancestor node IDs
        """
        ancestors = set()

        def collect_ancestors(nid: int):
            node = self.nodes.get(nid)
            if node:
                for dep_id in node.dependencies:
                    if dep_id not in ancestors:
                        ancestors.add(dep_id)
                        collect_ancestors(dep_id)

        collect_ancestors(node_id)
        return ancestors

    def get_descendants(self, node_id: int) -> Set[int]:
        """Get all descendant nodes (transitive dependents).

        Args:
            node_id: Node ID to find descendants for

        Returns:
            Set of descendant node IDs
        """
        descendants = set()

        def collect_descendants(nid: int):
            node = self.nodes.get(nid)
            if node:
                for dep_id in node.dependents:
                    if dep_id not in descendants:
                        descendants.add(dep_id)
                        collect_descendants(dep_id)

        collect_descendants(node_id)
        return descendants


def build_graph_from_scopes(expr: exp.Expression) -> Graph:
    """Build a dependency graph from a list of scopes.

    Args:
        scopes: List of Scope objects representing query components
    """
    graph = Graph()
    scopes = list(traverse_scope(expr))

    mappings = {}

    for index, scope in enumerate(scopes):
        node = ScopeNode(node_id=index, scope=scope)
        mappings[scope.expression] = index
        graph.add_node(node)

    for index, scope in enumerate(scopes):
        node = graph.get_node(index)
        if not node or scope.parent is None:
            continue
        parent_id = mappings[scope.parent.expression]
        if scope.is_correlated_subquery:
            graph.add_edge(from_node_id=index, to_node_id=parent_id)
        elif scope.is_subquery or scope.is_cte or scope.is_union:
            graph.add_edge(from_node_id=parent_id, to_node_id=index)
    return graph


def to_scope_dot(graph: Graph) -> str:
    """Convert the graph to DOT format for visualization.
    Args:
        graph: Graph to convert
    Returns:
        String in DOT format representing the graph
    """
    lines = ["digraph G {"]
    for node_id, node in graph.nodes.items():
        label = f"Node {node_id}\\n{type(node.scope.expression).__name__}"
        lines.append(f'  {node_id} [label="{label}"];')
        for dep_id in node.dependencies:
            lines.append(f"  {dep_id} -> {node_id};")
    lines.append("}")
    return "\n".join(lines)


class Planner:

    DATETIME_FMT = ["%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d", "%Y-%m"]

    def __init__(
        self,
        ctx: Context,
        scope_node: ScopeNode,
        tracer: UExprToConstraint,
        dialect: str,
        verbose: bool = True,
    ):
        self.ctx = ctx
        self._scope = scope_node
        self.tracer = tracer
        self.dialect = dialect
        self.verbose = verbose
        self.current_scope = scope_node

    @property
    def scope(self):
        return self._scope.scope

    @property
    def scope_id(self):
        return self._scope.node_id

    def context(self, tables):
        return Context(tables=tables)

    def _expression_label(self, expression: Any) -> str:
        if isinstance(expression, exp.Expression):
            if expression.alias_or_name:
                return expression.alias_or_name
            if isinstance(expression, exp.AggFunc):
                operands = []
                for operand in expression.unnest_operands():
                    if isinstance(operand, exp.Distinct):
                        operands.extend(operand.expressions)
                    else:
                        operands.append(operand)
                parts = [normalize_name(expression.key)]
                if not operands:
                    parts.append("star")
                else:
                    for operand in operands:
                        if isinstance(operand, exp.Column):
                            parts.append(normalize_name(operand.name))
                        elif isinstance(operand, exp.Star):
                            parts.append("star")
                        else:
                            parts.append(normalize_name(operand.sql()))
                return "_".join(part for part in parts if part)
            return expression.sql()
        return str(expression)

    def derived_schema(self, expressions, datatypes=None, nullables=None, uniques=None):
        datatypes = datatypes or {}
        columns = []
        for expression in expressions:
            if isinstance(expression, exp.Expression):
                columns.append(expression.alias_or_name)
                if expression.alias_or_name not in datatypes:
                    datatypes[expression.alias_or_name] = expression.type
            else:
                columns.append(expression)
        return DerivedSchema(
            columns=columns,
            datatypes=datatypes,
            nullables=nullables,
            uniqueness=uniques,
        )

    def encode(self) -> Context:
        expr = self._scope.scope.expression
        plan = Plan(expr)
        contexts = {}
        finished = set()
        queue = set(plan.leaves)

        while queue:
            node = queue.pop()
            try:
                context = self.context(
                    {
                        name: table
                        for dep in node.dependencies
                        for name, table in contexts[dep].tables.items()
                    }
                )
                if isinstance(node, Scan):
                    contexts[node] = self.scan(node, context)
                elif isinstance(node, Aggregate):
                    contexts[node] = self.aggregate(node, context)
                elif isinstance(node, Join):
                    contexts[node] = self.join(node, context)
                elif isinstance(node, Sort):
                    contexts[node] = self.sort(node, context)
                elif isinstance(node, SetOperation):
                    contexts[node] = self.set_operation(node, context)
                else:
                    raise NotImplementedError
                finished.add(node)
                for dep in node.dependents:
                    if all(d in contexts for d in dep.dependencies):
                        queue.add(dep)
                for dep in node.dependencies:
                    if all(d in finished for d in dep.dependents):
                        contexts.pop(dep)
            except Exception as e:
                raise NotImplementedError(
                    f"Failed to encode step '{node.id}' of type {type(node)}"
                ) from e
        root = plan.root
        return contexts.get(root)

    def _project_and_filter(self, node: Step, context: Context) -> Context:
        if node.condition:
            context = self.filters(node, context)
        if node.projections:
            context = self.project(node, context)
        return context

    def scan(self, node: Scan, context: Context) -> Context:
        logger.info(
            f"Processing Scan node {node.name} with source: {node.source}, {node.source.alias_or_name}"
        )
        sql_conditions, rows = [], []
        alias_or_name = node.source.alias_or_name
        table_reader = None
        if isinstance(node.source, exp.Table):
            table_reader = self.ctx.table_iter(node.source.name)
            scope_columns = self.current_scope.scope_columns
            visited = set()
            for column in scope_columns:
                if column.sql() in visited:
                    continue
                visited.add(column.sql())
                if column.table == alias_or_name:
                    resolved_schema = self.ctx.resolve_table(node.source.name)
                    dtype = resolved_schema.get_column_type(column.name)
                    nullable = resolved_schema.nullable(column.name)
                    is_unique = resolved_schema.is_unique(column.name)
                    col = exp.Column(
                        this=exp.to_identifier(column.name, quoted=True),
                        table=node.source.alias_or_name,
                        _type=dtype,
                        is_unique=is_unique,
                        nullable=nullable,
                    )
                    col.type = dtype
                    sql_conditions.append(col)
        for row in table_reader:
            symbolic_exprs = [row[columnref.name] for columnref in sql_conditions]
            self.tracer.which_path(
                scope_id=self.current_scope.node_id,
                step_type=node.type_name,
                step_name=node.name,
                sql_conditions=sql_conditions,
                smt_exprs=symbolic_exprs,
                takens=[PBit.TRUE] * len(symbolic_exprs),
                branch=True,
                rowids=row.rowid(),
            )
            rows.append(row.row)
        derived_schema = DerivedSchema(
            columns=self.ctx.resolve_table(node.source.name).columns,
            rows=rows,
        )

        source_context = self.context({node.name: derived_schema})
        return self._project_and_filter(node, source_context)

    def project(self, node: Step, context: Context) -> Context:
        if node.projections is None:
            return context
        sink = self.derived_schema(node.projections)
        for reader, _ in context:
            row = {}
            sql_conditions, smt_conditions = [], []
            for project in node.projections:
                alias_name = project.alias_or_name
                if isinstance(project, exp.Alias):
                    project = project.this
                    ctx = self.encode_condition(project, scope=reader, context=context)
                row[alias_name] = ctx[project]
                smt_conditions.extend(ctx.get("smt_conditions"))
                sql_conditions.extend(ctx.get("sql_conditions"))

            sink.append(Row(this=reader.row.rowid, columns=row))
            takens = [
                16 if isinstance(sql, exp.Column) else int(smt.concrete)
                for smt, sql in zip(smt_conditions, sql_conditions)
            ]
            self.tracer.which_path(
                scope_id=self.scope_id,
                step_type="Project",
                step_name=node.name,
                sql_conditions=sql_conditions,
                smt_exprs=smt_conditions,
                takens=takens,
                branch=True,
                rowids=reader.rowid(),
            )
        return self.context({node.name: sink})

    def filters(self, node: Step, context: Context) -> Dict:
        if node.condition is None:
            return context
        rows = []
        for reader, _ in context:
            ctx = self.encode_condition(node.condition, scope=reader, context=context)
            result = ctx[node.condition]
            branch = result.concrete is True
            smt_conditions = ctx.get("smt_conditions", [])
            sql_conditions = ctx.get("sql_conditions", [])
            if branch:
                rows.append(reader.row)
            takens = [b.concrete is True for b in smt_conditions]
            self.tracer.which_path(
                scope_id=self.current_scope.node_id,
                step_type="Filter",
                step_name=node.name,
                sql_conditions=sql_conditions,
                smt_exprs=smt_conditions,
                takens=takens,
                branch=branch,
                rowids=reader.row.rowid,
            )

        return self.context(
            {
                name: DerivedSchema(
                    table.columns, rows, column_range=table.column_range
                )
                for name, table in context.tables.items()
            }
        )

    def _inner_join(
        self, node, join, source_context: Context, join_context: Context
    ) -> List:

        logger.info(f"start to processing inner join, {node.condition}")
        rows = []
        if not join.get("source_key") or not join.get("join_key"):
            for left_row in source_context.table:
                for right_row in join_context.table:
                    rows.append(left_row.row + right_row.row)
            return rows
        for left_row in source_context.table:
            left_flag = False
            for right_row in join_context.table:
                combined_row = left_row.row + right_row.row
                smt_exprs, sql_conditions = [], []
                for source_key, join_key in zip(join["source_key"], join["join_key"]):
                    smt_exprs.append(
                        combined_row[source_key.name].eq(combined_row[join_key.name])
                    )
                    sql_conditions.append(exp.EQ(this=source_key, expression=join_key))

                smt_expr = reduce(lambda x, y: x.and_(y), smt_exprs)
                branch = smt_expr.concrete is True
                rowids = left_row.row.rowid

                if branch:
                    left_flag = True
                    rows.append(combined_row)
                    rowids = combined_row.rowid
                    takens = [2] * len(
                        smt_exprs
                    )  # [2 if b.concrete is True else 3 for b in smt_exprs]
                    self.tracer.which_path(
                        scope_id=self.current_scope.node_id,
                        step_type=node.type_name,
                        step_name=node.name,
                        sql_conditions=sql_conditions,
                        smt_exprs=smt_exprs,
                        takens=takens,
                        branch=branch,
                        rowids=rowids,
                    )

            if not left_flag:
                self.tracer.which_path(
                    scope_id=self.current_scope.node_id,
                    step_type=node.type_name,
                    step_name=node.name,
                    sql_conditions=sql_conditions,
                    smt_exprs=[],
                    takens=[3] * len(sql_conditions),
                    branch=False,
                    rowids=left_row.row.rowid,
                )

        return rows

    def _left_join(
        self, node, join, source_context: Context, join_context: Context
    ) -> List[Dict]:

        rows = []
        if not join.get("source_key") or not join.get("join_key"):
            for left_row in source_context.table:
                matched = False
                for right_row in join_context.table:
                    rows.append(left_row.row + right_row.row)
                    matched = True
                if not matched:
                    null_vlaues = {
                        column: Const(None) for column in join_context.table.columns
                    }
                    new_row = {c: v for c, v in left_row.row.items()}
                    new_row.update(null_vlaues)
                    rows.append(Row(left_row.row.rowid, new_row))
            return rows
        for left_row in source_context.table:
            smt_exprs = []
            sql_conditions = []
            for right_row in join_context.table:
                combined_row = left_row.row + right_row.row
                smt_conditions, sql_conditions = [], []
                for source_key, join_key in zip(join["source_key"], join["join_key"]):
                    smt_conditions.append(
                        combined_row[source_key.name].eq(combined_row[join_key.name])
                    )
                    sql_conditions.append(exp.EQ(this=source_key, expression=join_key))

                smt_expr = reduce(lambda x, y: x.and_(y), smt_conditions)
                smt_exprs.append(smt_expr)
                branch = smt_expr.concrete is True
                if branch:
                    rows.append(combined_row)
                    takens = [2 if b else 3 for b in smt_conditions]
                    self.tracer.which_path(
                        scope_id=self.current_scope.node_id,
                        step_type=node.type_name,
                        step_name=node.name,
                        sql_conditions=sql_conditions,
                        smt_exprs=smt_conditions,
                        takens=takens,
                        branch=branch,
                        rowids=combined_row.rowid,
                    )

            if smt_exprs and any(smt_exprs):
                continue
            null_vlaues = {column: Const(None) for column in join_context.table.columns}
            new_row = {c: v for c, v in left_row.row.items()}
            new_row.update(null_vlaues)
            row = Row(left_row.row.rowid, new_row)
            smt_condition = reduce(lambda x, y: x.and_(y).not_(), smt_exprs)
            self.tracer.which_path(
                scope_id=self.current_scope.node_id,
                step_type=node.type_name,
                step_name=node.name,
                sql_conditions=sql_conditions,
                smt_exprs=[smt_condition],
                takens=[3],
                branch=True,
                rowids=row.rowid,
            )
            rows.append(row)

        return rows

    def _right_join(
        self, node, join, source_context: Context, join_context: Context
    ) -> List[Dict]:
        rows = []
        if not join.get("source_key") or not join.get("join_key"):
            for right_row in join_context.table:
                matched = False
                for left_row in source_context.table:
                    rows.append(left_row.row + right_row.row)
                    matched = True
                if not matched:
                    null_values = {
                        column: Const(None) for column in source_context.table.columns
                    }
                    new_row = dict(null_values)
                    new_row.update({c: v for c, v in right_row.row.items()})
                    rows.append(Row(right_row.row.rowid, new_row))
            return rows
        for right_row in join_context.table:
            smt_exprs = []
            sql_conditions = []
            for left_row in source_context.table:
                combined_row = left_row.row + right_row.row
                smt_conditions, sql_conditions = [], []
                for source_key, join_key in zip(join["source_key"], join["join_key"]):
                    smt_conditions.append(
                        combined_row[source_key.name].eq(combined_row[join_key.name])
                    )
                    sql_conditions.append(exp.EQ(this=source_key, expression=join_key))

                smt_expr = reduce(lambda x, y: x.and_(y), smt_conditions)
                smt_exprs.append(smt_expr)
                branch = smt_expr.concrete is True
                if branch:
                    rows.append(combined_row)
                    takens = [2 if b else 3 for b in smt_conditions]
                    self.tracer.which_path(
                        scope_id=self.current_scope.node_id,
                        step_type=node.type_name,
                        step_name=node.name,
                        sql_conditions=sql_conditions,
                        smt_exprs=smt_conditions,
                        takens=takens,
                        branch=branch,
                        rowids=combined_row.rowid,
                    )

            if smt_exprs and any(smt_exprs):
                continue
            null_values = {
                column: Const(None) for column in source_context.table.columns
            }
            new_row = dict(null_values)
            new_row.update({c: v for c, v in right_row.row.items()})
            row = Row(right_row.row.rowid, new_row)
            smt_condition = reduce(lambda x, y: x.and_(y).not_(), smt_exprs)
            self.tracer.which_path(
                scope_id=self.current_scope.node_id,
                step_type=node.type_name,
                step_name=node.name,
                sql_conditions=sql_conditions,
                smt_exprs=[smt_condition],
                takens=[3],
                branch=True,
                rowids=row.rowid,
            )
            rows.append(row)

        return rows

    def _natural_join(
        self, node, join, source_context: Context, join_context: Context
    ) -> List[Dict]:

        rows = []
        source_keys = []
        join_keys = []
        for column in source_context.table.columns:
            if column in join_context.table.columns:
                source_keys.append(
                    exp.Column(
                        this=exp.to_identifier(column),
                        table=source_context.table.alias_or_name,
                    )
                )
                join_keys.append(
                    exp.Column(
                        this=exp.to_identifier(column),
                        table=join_context.table.alias_or_name,
                    )
                )

        for left_row in source_context.table:
            for right_row in join_context.table:
                combined_row = left_row.row + right_row.row
                smt_exprs, sql_conditions = [], []
                for source_key, join_key in zip(source_keys, join_keys):
                    smt_exprs.append(
                        combined_row[source_key.name].eq(combined_row[join_key.name])
                    )
                    sql_conditions.append(exp.EQ(this=source_key, expression=join_key))
                smt_expr = reduce(lambda x, y: x.and_(y), smt_exprs)
                branch = smt_expr.concrete is True
                if branch:
                    rows.append(combined_row)
                takens = [2 if b.concrete is True else 3 for b in smt_exprs]
                self.tracer.which_path(
                    scope_id=self.current_scope.node_id,
                    step_type=node.type_name,
                    step_name=node.name,
                    sql_conditions=sql_conditions,
                    smt_exprs=smt_exprs,
                    takens=takens,
                    branch=branch,
                    rowids=combined_row.rowid,
                )

        return rows

    def join(self, node: Join, context):
        source = node.source_name
        source_table = context.tables[source]
        source_context = self.context({source: source_table})
        column_ranges = {source: range(0, len(source_table.columns))}

        def merged_columns(left_columns, right_columns):
            columns = []
            seen = set()
            for column in list(left_columns) + list(right_columns):
                key = normalize_name(column)
                if key in seen:
                    continue
                seen.add(key)
                columns.append(column)
            return tuple(columns)

        # logger.info(f"column ranges: {column_ranges}")
        for name, join in node.joins.items():
            table = context.tables[name]
            join_context = self.context({name: table})
            kind = join["side"]

            if kind == "LEFT":
                rows = self._left_join(node, join, source_context, join_context)
            elif kind == "RIGHT":
                rows = self._right_join(node, join, source_context, join_context)
            elif kind == "NATURAL":
                rows = self._natural_join(node, join, source_context, join_context)
            else:
                rows = self._inner_join(node, join, source_context, join_context)

            combined_columns = merged_columns(source_table.columns, table.columns)
            offsets = {}
            cursor = 0
            for table_name in [source] + list(node.joins.keys()):
                table_columns = []
                for col in context.tables[table_name].columns:
                    col_key = normalize_name(col)
                    if col_key in offsets:
                        continue
                    offsets[col_key] = cursor
                    table_columns.append(col)
                    cursor += 1
                start = (
                    min(offsets[normalize_name(c)] for c in table_columns)
                    if table_columns
                    else cursor
                )
                column_ranges[table_name] = range(start, start + len(table_columns))

            source_context = self.context(
                {
                    name: DerivedSchema(combined_columns, rows, column_range)
                    for name, column_range in column_ranges.items()
                }
            )
            source_table = source_context.tables[source]
        return self._project_and_filter(node, source_context)

    def aggregate(self, node: Aggregate, context: Context):
        operand_map: dict[str, exp.Expression] = {
            operand.alias: operand.this for operand in node.operands
        }
        group_map: dict[str, exp.Expression] = dict(node.group)

        def restore(expr: exp.Expression) -> exp.Expression:
            expr = expr.copy()
            for col_ref in expr.find_all(exp.Column):
                name = col_ref.name
                if name in operand_map:
                    col_ref.replace(operand_map[name].copy())
                elif name in group_map:
                    col_ref.replace(group_map[name].copy())
            return expr

        group_by_columns: list[exp.Expression] = list(node.group.values())
        h_aliases = {a.alias for a in node.aggregations if isinstance(a, exp.Alias)}
        having_operand_name: str | None = None
        if (
            node.condition is not None
            and isinstance(node.condition, exp.Column)
            and node.condition.name in h_aliases
        ):
            having_operand_name = node.condition.name

        having_condition: exp.Expression | None = None
        having_agg_sqls: set[str] = set()
        if node.condition is not None:
            if having_operand_name:
                h_entry = next(
                    a
                    for a in node.aggregations
                    if isinstance(a, exp.Alias) and a.alias == having_operand_name
                )
                having_condition = restore(h_entry.this)
                for agg_func in having_condition.find_all(exp.AggFunc):
                    having_agg_sqls.add(agg_func.sql())
            else:
                having_condition = restore(node.condition)

        aggregations: list[exp.Expression] = []
        aggregation_alias: dict[str, exp.Expression] = {}
        covered_having_aggs: set[str] = set()

        for agg_expr in node.aggregations:
            if (
                isinstance(agg_expr, exp.Alias)
                and agg_expr.alias == having_operand_name
            ):
                continue

            restored = restore(agg_expr)
            inner = restored.this if isinstance(restored, exp.Alias) else restored

            if inner.sql() in having_agg_sqls:
                covered_having_aggs.add(inner.sql())

            aggregations.append(restored)
            aggregation_alias[restored.alias_or_name] = inner

        if having_condition is not None:
            for having_agg_sql in having_agg_sqls - covered_having_aggs:
                having_agg_node = next(
                    f
                    for f in having_condition.find_all(exp.AggFunc)
                    if f.sql() == having_agg_sql
                )
                internal_alias = f"_having_agg_{len(aggregation_alias)}"
                aggregations.append(having_agg_node.copy())
                aggregation_alias[internal_alias] = having_agg_node.copy()

        projection_labels = [
            self._expression_label(project) for project in node.projections
        ]
        group_labels = [
            (
                projection_labels[index]
                if index < len(projection_labels)
                else self._expression_label(groupby)
            )
            for index, groupby in enumerate(group_by_columns)
        ]
        aggregate_labels: list[str] = []
        derived_scm = []
        dtypes = {}
        uniques = {}
        notnulls = {}

        for index, groupby in enumerate(group_by_columns):
            label = group_labels[index]
            derived_scm.append(label)
            dtypes[label] = groupby.type
            uniques[label] = True
            notnulls[label] = groupby.args.get("nullable", True)

        for index, agg in enumerate(aggregations):
            projection_index = len(group_by_columns) + index
            label = (
                projection_labels[projection_index]
                if projection_index < len(projection_labels)
                else self._expression_label(agg)
            )
            aggregate_labels.append(label)
            derived_scm.append(label)
            dtypes[label] = agg.type
            uniques[label] = False
            notnulls[label] = agg.args.get("nullable", True)

        sink = self.derived_schema(
            derived_scm, datatypes=dtypes, nullables=notnulls, uniques=uniques
        )

        groups = {}
        for reader, _ in context:
            row = reader.row
            group_key = ()
            for expression in group_by_columns:
                group_key += (row[expression.alias_or_name],)

            concrete_group_key = tuple(v.concrete for v in group_key)
            if concrete_group_key not in groups:
                groups[concrete_group_key] = {"group_key": group_key, "rows": []}
            groups[concrete_group_key]["rows"].append(row)

        self.groupby(node, group_by_columns=group_by_columns, groups=groups)

        aggregate_results = self.aggregate_functions(
            node,
            groups,
            group_by_columns,
            aggregations,
            aggregate_labels=aggregate_labels,
            context=context,
        )

        sink.rows.extend(aggregate_results)
        context = self.context(
            {node.name: sink, **{name: sink for name in context.tables}}
        )
        if having_condition:
            return self.having(
                node,
                having_condition,
                group_labels,
                aggregate_labels,
                aggregations,
                context,
            )
        return context

    def groupby(self, node: Aggregate, group_by_columns, groups: Dict):
        if not node.group:
            return

        sql_conditions, takens = [], []
        for groupby in group_by_columns:
            sql_conditions.append(groupby)
            takens.append(PBit.GROUP_SIZE)

        for _, group_info in groups.items():
            group_key = group_info["group_key"]
            group_rows = group_info["rows"]
            rowids = sum((row.rowid for row in group_rows), ())
            g = AggGroup(this=rowids, group_key=group_key, group_values=group_rows)
            smt_conditions = [g] * len(sql_conditions)
            self.tracer.which_path(
                scope_id=self.scope_id,
                step_type="Groupby",
                step_name=node.name,
                sql_conditions=sql_conditions,
                smt_exprs=smt_conditions,
                takens=takens,
                branch=True,
                rowids=rowids,
            )

    def aggregate_functions(
        self,
        node: Aggregate,
        groups: Dict,
        group_by_columns,
        aggregations: List,
        aggregate_labels: List[str],
        context: Context,
    ):
        def compute_aggregate_symbol(
            func_expr: exp.Expression, group_rows: List[Row]
        ) -> Const:
            func = func_expr.this if isinstance(func_expr, exp.Alias) else func_expr
            operands = func.unnest_operands()
            operand = operands[0] if operands else exp.Star()
            is_distinct = isinstance(operand, exp.Distinct)
            if is_distinct:
                operand = operand.expressions[0]
            values = []
            concrete_values = []
            if isinstance(operand, exp.Star):
                values = list(group_rows)
                concrete_values = [1 for _ in group_rows]
            else:
                for row in group_rows:
                    if isinstance(operand, exp.Column):
                        v = row[operand.alias_or_name or operand.name]
                    else:
                        operand_ctx = self.encode_condition(operand, scope=row)
                        v = operand_ctx[operand]
                    if v.concrete is not None:
                        values.append(v)
                        concrete_values.append(v.concrete)
            if is_distinct:
                deduped = {}
                for value in values:
                    deduped.setdefault(value.concrete, value)
                values = list(deduped.values())
                concrete_values = list(deduped.keys())
            if isinstance(func, exp.Count):
                value = Const(this=len(values), _type=DataType.build("int"))
                value.type = DataType.build("int")
                return value
            if isinstance(func, exp.Sum):
                sum_value = sum(concrete_values) if concrete_values else 0
                value = Const(this=sum_value, _type=DataType.build("int"))
                value.type = DataType.build("int")
                return value
            if isinstance(func, exp.Max):
                max_value = max(concrete_values) if concrete_values else None
                value = Const(this=max_value, _type=func.type)
                value.type = value.type or func.type
                return value
            if isinstance(func, exp.Min):
                min_value = min(concrete_values) if concrete_values else None
                value = Const(this=min_value, _type=func.type)
                value.type = value.type or func.type
                return value
            if isinstance(func, exp.Avg):
                avg_value = (
                    (sum(concrete_values) / len(concrete_values))
                    if concrete_values
                    else None
                )
                value = Const(this=avg_value, _type=DataType.build("REAL"))
                value.type = DataType.build("REAL")
                return value
            if any(
                isinstance(child, exp.AggFunc) for child in func.find_all(exp.AggFunc)
            ):
                rewritten = func.copy()
                for agg_child in list(rewritten.find_all(exp.AggFunc)):
                    agg_symbol = compute_aggregate_symbol(agg_child, group_rows)
                    agg_child.replace(
                        convert_to_literal(agg_symbol.concrete, agg_symbol.type)
                    )
                concrete = rewritten.concrete
                value = Const(this=concrete, _type=func.type or DataType.build("REAL"))
                value.type = value.type or func.type or DataType.build("REAL")
                return value
            raise NotImplementedError(f"Aggregation function {func} not supported yet.")

        result_rows = []
        for _, group_info in groups.items():
            group_key = group_info["group_key"]
            group_rows = group_info["rows"]
            rowids = sum((row.rowid for row in group_rows), ())
            new_row = {
                self._expression_label(g_name): k
                for g_name, k in zip(group_by_columns, group_key)
            }

            for agg_func, aggregate_label in zip(aggregations, aggregate_labels):
                value = compute_aggregate_symbol(agg_func, group_rows)
                new_row[aggregate_label] = value

            result_rows.append(Row(this=rowids, columns=new_row))
            g = AggGroup(this=rowids, group_key=group_key, group_values=group_rows)
            sql_conditions = list(aggregations)
            smt_conditions = [g] * len(sql_conditions)
            takens = [PBit.AGGREGATE_SIZE] * len(sql_conditions)
            if aggregations:
                self.tracer.which_path(
                    scope_id=self.scope_id,
                    step_type="Aggregate",
                    step_name=node.name,
                    sql_conditions=sql_conditions,
                    smt_exprs=smt_conditions,
                    takens=takens,
                    branch=True,
                    rowids=rowids,
                )
        return result_rows

    def having(
        self,
        node: Aggregate,
        having_condition: exp.Expression,
        group_labels: List[str],
        aggregate_labels: List[str],
        aggregations: List,
        context: Context,
    ):
        if node.condition is None:
            return context

        rows = []

        for reader, _ in context:
            row = {}
            mappings = {}
            cond = having_condition.copy()

            for index, func in enumerate(cond.find_all(exp.AggFunc)):
                if func not in mappings:
                    column = exp.Column(
                        this=exp.to_identifier(f"agg_fun_{index}"), _type=func.type
                    )
                    mappings[func] = column
                    candidate_labels = [self._expression_label(func), *aggregate_labels]
                    for candidate in candidate_labels:
                        try:
                            row[column.name] = reader.row[candidate]
                            break
                        except KeyError:
                            normalized = normalize_name(candidate)
                            for key, value in reader.row.items():
                                if normalize_name(str(key)) == normalized:
                                    row[column.name] = value
                                    break
                            if column.name in row:
                                break
                    else:
                        raise KeyError(self._expression_label(func))

            def replace_func(e):
                if e in mappings:
                    return mappings[e]
                return e

            cond = cond.transform(replace_func)
            ctx = self.encode_condition(cond, scope=row)
            result = ctx[cond]
            branch = result.concrete is True
            smt_conditions = ctx["smt_conditions"]
            sql_conditions = []
            for sql_condition in ctx["sql_conditions"]:

                def restore(e):
                    for k, v in mappings.items():
                        if e == v:
                            return k
                    return e

                sql_conditions.append(sql_condition.transform(restore))
            if branch:
                rows.append(reader.row)
            takens = [
                (PBit.HAVING_TRUE if b.concrete is True else PBit.HAVING_FALSE)
                for b in smt_conditions
            ]
            self.tracer.which_path(
                scope_id=self.current_scope.node_id,
                step_type="Having",
                step_name=node.name,
                sql_conditions=sql_conditions,
                smt_exprs=smt_conditions,
                takens=takens,
                branch=branch,
                rowids=reader.rowid(),
            )
        return self.context(
            {
                name: DerivedSchema(
                    table.columns, rows, column_range=table.column_range
                )
                for name, table in context.tables.items()
            }
        )

    @non_fatal(default_from_args=lambda *args, **kwargs: args[2])
    def sort(self, node: Sort, context):
        projection_labels = [self._expression_label(p) for p in node.projections]
        evaluated_rows = []

        for reader, ctx in context:
            projected = {}
            sort_values = []
            sort_symbols = []

            for projection in node.projections:
                label = self._expression_label(projection)
                expr = (
                    projection.this if isinstance(projection, exp.Alias) else projection
                )
                expr_ctx = self.encode_condition(expr, scope=reader, context=ctx)
                projected[label] = expr_ctx[expr]

            for ordered in node.key:
                key_expr = ordered.this
                key_ctx = self.encode_condition(key_expr, scope=reader, context=ctx)
                symbol = key_ctx[key_expr]
                sort_symbols.append(symbol)
                sort_values.append(
                    (
                        symbol.concrete,
                        ordered.args.get("desc"),
                        ordered.args.get("nulls_first", False),
                    )
                )

            evaluated_rows.append(
                (reader.row.rowid, projected, sort_values, sort_symbols)
            )

        @total_ordering
        class SortValue:
            def __init__(self, value, descending: bool):
                self.value = value
                self.desc = descending

            def __eq__(self, other):
                return self.value == other.value

            def __lt__(self, other):
                if self.desc:
                    return self.value > other.value
                return self.value < other.value

        def sort_key(item):
            _, _, order_values, _ = item
            key = []
            for v, desc, null_first in order_values:
                if v is None:
                    w = 1 if null_first else -1
                    key.append((w, None))
                else:
                    key.append((0, SortValue(v, desc)))
            return tuple(key)

        sorted_data = sorted(evaluated_rows, key=sort_key)
        sql_conditions = [o.this for o in node.key]

        for rowid, _, _, sort_symbols in sorted_data:
            self.tracer.which_path(
                scope_id=self.current_scope.node_id,
                step_type="Sort",
                step_name=node.name,
                sql_conditions=sql_conditions,
                smt_exprs=sort_symbols,
                takens=[True] * len(sort_symbols),
                branch=True,
                rowids=rowid,
            )
        rows = sorted_data
        if not math.isinf(node.limit):
            rows = sorted_data[0 : node.limit]
        new_rows = []
        for rowid, projected, _, _ in rows:
            new_rows.append(Row(rowid, projected))

        output = DerivedSchema(
            projection_labels,
            rows=new_rows,
        )
        return self.context({node.name: output})

    def set_operation(self, node: SetOperation, context: Context) -> Dict:
        """We do not need to track set operations here"""

        left = context.tables[node.left]
        right = context.tables[node.right]

        sink = self.derived_schema(left.columns)

        if issubclass(node.op, exp.Intersect):
            sink.rows = list(set(left.rows).intersection(set(right.rows)))
        elif issubclass(node.op, exp.Except):
            sink.rows = list(set(left.rows).difference(set(right.rows)))
        elif issubclass(node.op, exp.Union) and node.distinct:
            sink.rows = list(set(left.rows).union(set(right.rows)))
        else:
            sink.rows = left.rows + right.rows
        if not math.isinf(node.limit):
            sink.rows = sink.rows[0 : node.limit]
        return self.context({node.name: sink})

    def encode_condition(
        self, condition: exp.Expression, ctx: Optional[Dict] = None, **kwargs
    ):

        ctx = ctx if ctx is not None else {}
        ctx.update(**kwargs)
        original_condition = condition
        condition = condition.transform(
            lambda node: (
                exp.Boolean(this=True) if isinstance(node, exp.Exists) else node
            ),
            copy=True,
        )
        if condition in ctx:
            return ctx

        result = condition.transform(self.transform, copy=True, ctx=ctx)
        mappings = ctx.pop("mappings", {})

        for smt_expr in ctx.get("smt_conditions", []):
            sql_cond = smt_expr.transform(
                lambda node: mappings[node] if node in mappings else node, copy=True
            )
            ctx.setdefault("sql_conditions", []).append(sql_cond)
        if not ctx.get("sql_conditions"):
            for smt_cond, sql_cond in mappings.items():
                ctx.setdefault("sql_conditions", []).append(sql_cond)
                ctx.setdefault("smt_conditions", []).append(smt_cond)
        ctx[condition] = result
        if original_condition is not condition:
            ctx[original_condition] = result
        return ctx

    def _get_sql_condition(self, smt_conditions: List[exp.Expression], ctx: Dict):
        mappings = ctx.get("mappings", {})
        sql_conditions = []
        for smt_cond in smt_conditions:
            sql_conditions.append(
                smt_cond.transform(
                    lambda node: mappings[node] if node in mappings else node, copy=True
                )
            )
        return sql_conditions

    def transform(self, condition: exp.Expression, ctx: Dict[str, Any]):
        if isinstance(condition, exp.Exists):
            return exp.Boolean(this=True)
        if isinstance(condition, exp.Predicate):
            ctx.setdefault("smt_conditions", []).append(condition)

        if isinstance(condition, exp.Column):
            column = condition
            table = column.table
            column_name = column.name
            row = ctx["scope"]
            smt_expr = None
            if hasattr(row, "get") and not isinstance(row, dict):
                try:
                    smt_expr = row.get(table, column_name)
                except Exception:
                    smt_expr = None
            if smt_expr is None and table and "context" in ctx:
                try:
                    reader_context = ctx["context"]
                    reader = reader_context.resolve_reader(table)
                    smt_expr = reader[column_name]
                except Exception:
                    smt_expr = None
            if smt_expr is None:
                try:
                    smt_expr = row[column_name]
                except Exception:
                    normalized = normalize_name(column_name)
                    if hasattr(row, "items"):
                        for key, value in row.items():
                            if normalize_name(str(key)) == normalized:
                                smt_expr = value
                                break
                    if smt_expr is None:
                        smt_expr = Const(this=None)
            ctx.setdefault("mappings", {})[smt_expr] = column
            return smt_expr

        elif isinstance(condition, exp.Literal):
            from .helper import to_literal

            literal = to_literal(condition, datatype=condition.type)
            return literal

        elif isinstance(condition, exp.Cast):
            to_type = condition.to
            inner = condition.this
            if isinstance(condition.this, exp.Column):
                ctx.setdefault("datatype", {})[condition.this] = to_type
            inner.type = to_type
            return inner
        elif isinstance(condition, exp.Case):
            for when in condition.args.get("ifs"):
                smt_expr = when.this.transform(self.transform, copy=True, ctx=ctx)
                if smt_expr.concrete:
                    return when.args.get("true").transform(
                        self.transform, copy=True, ctx=ctx
                    )
            default = condition.args.get("default")
            if default is None:
                return exp.Null()
            return default.transform(self.transform, copy=True, ctx=ctx)
        return condition
