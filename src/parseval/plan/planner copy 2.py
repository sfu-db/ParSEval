from __future__ import annotations
from typing import List, Set, Tuple, Dict, Optional, TYPE_CHECKING, Any
from collections import defaultdict, deque
from functools import reduce, total_ordering
from dataclasses import dataclass, field
from sqlglot.optimizer.scope import Scope, traverse_scope
from sqlglot.planner import Plan, Scan, Aggregate, Join, Sort, SetOperation, Step
from sqlglot import exp
from .rex import *
from .context import Context, DerivedSchema
from src.parseval.constants import PBit
from contextlib import contextmanager
import logging, math
from dateutil import parser as date_parser
from src.parseval.solver.smt import OperationRegistry
from src.parseval.states import (
    non_fatal
)

logger = logging.getLogger("parseval.coverage")

if TYPE_CHECKING:
    from src.parseval.instance import Instance
    from src.parseval.uexpr.uexprs import UExprToConstraint
    
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
    
    for index, scope in  enumerate(scopes):
        node = ScopeNode(node_id=index, scope=scope)
        mappings[scope.expression] = index
        graph.add_node(node)
    
    for index, scope in enumerate(scopes):
        node = graph.get_node(index)
        if node:
            if scope.is_correlated_subquery:
                graph.add_edge(from_node_id=index, to_node_id=mappings[scope.parent.expression])
                # graph.add_edge(from_node_id=mappings[scope.parent.expression], to_node_id=index)
            elif scope.is_subquery or scope.is_cte or scope.is_union:
                print(f'adding edge from {index} to {mappings[scope.parent.expression]} for scope expression {scope.expression}')
                graph.add_edge(from_node_id=mappings[scope.parent.expression], to_node_id=index)
                # graph.add_edge(from_node_id=index, to_node_id=mappings[scope.parent.expression])
            elif scope.is_cte:
                graph.add_edge(from_node_id=mappings[scope.parent.expression], to_node_id=index)
                # graph.add_edge(from_node_id=index, to_node_id=mappings[scope.parent.expression])
            # else:
            #     raise NotImplementedError(f"Unsupported scope type: {scope.expression}")
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
            lines.append(f'  {dep_id} -> {node_id};')
    lines.append("}")
    return "\n".join(lines)
    

def get_parent(e):
    if e.parent is None:
        return None
    if isinstance(e.parent, (exp.Paren, exp.Subquery)):
        return get_parent(e.parent)
    return e.parent

class Planner:
    DATETIME_FMT = ["%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d", "%Y-%m"]
    
    TRANSFORMS = {
        "AND" : "and_",
        "OR" : "or_",
        "NOT" : "not_",
    }
    
    def __init__(self, expr: exp.Expression, instance: Instance, tracer: UExprToConstraint, dialect: str , verbose: bool = True):
        self.expression = expr
        self.instance = instance
        self.tracer = tracer
        self.verbose = verbose
        self.dialect = dialect
        self.scope_graph = build_graph_from_scopes(expr)
        
    def context(self,  tables, parent = None):
        return Context(tables=tables, parent= parent)
    
    def derived_schema(self, expressions):
        return DerivedSchema(
            expression.alias_or_name if isinstance(expression, exp.Expression) else expression for expression in expressions
        )
    
    def unwrap_subquery(self, sub_scope: Scope, contexts):
        if sub_scope.expression not in contexts:
            return self._encode(sub_scope, contexts)
        
        parent = get_parent(sub_scope.expression)
        dtype = None
        if isinstance(parent, exp.Predicate):
            for r in [parent.left, parent.right]:
                if r is not sub_scope.expression.parent:
                    dtype = r.type
            is_string = False
            if dtype is not None:
                dtype = exp.DataType.build(dtype)
                is_string = dtype.is_type(*exp.DataType.TEXT_TYPES)
            
            logger.info(f"Unwrapping subquery with expression {sub_scope.expression} used in predicate {parent} with inferred type {contexts[sub_scope]}")
            concrete = contexts[sub_scope][0][0]
            if is_string:
                new = exp.Literal.string(concrete)
            else:
                new = exp.Literal.number(concrete)
            new.type = dtype
            # new = exp.Literal(this = concrete, _type = dtype, is_string = is_string)
            logger.info(f"Unwrapping subquery with concrete value {concrete} and type {dtype} into literal {new}")
            return new
        raise NotImplementedError("Only supports unwrapping subqueries used in predicates for now.")
    
    def encode(self) -> DerivedSchema:
        self.contexts = {}
        self.current_scope = None
        contexts = {}
        for node_id in self.scope_graph.get_dependency_order():
            node = self.scope_graph.get_node(node_id)
            print(f'==== Encoding node {node_id} with expression: {node.scope.expression.sql()} ====')
            logger.info(f"Encoding node {node_id} with expression: {node.scope.expression}")
            self.tracer.reset()
            self.current_scope = node
            contexts[node.scope.expression] = self._encode(node.scope, contexts)
            
            
            from parseval.to_dot import display_uexpr
            display_uexpr(self.tracer.root).write(
                "examples/tests/dot_coverage_scalar" + str(node_id) + ".png", format="png"
            )
            
        return contexts.get(self.expression, None)
    
    def encode2(self) -> DerivedSchema:
        scope_contexts = {}
        self.current_scope = None
        contexts = {}
        for node_id in self.scope_graph.get_dependency_order():
            node = self.scope_graph.get_node(node_id)
            scope = node.scope
            print(f'==== Encoding node {node_id} with expression: {scope.expression.sql()} ====')
            logger.info(f"Encoding node {node_id} with expression: {scope.expression}")
            parent_ctx = None
            if scope.parent:
                parent_ctx = scope_contexts.get(scope.parent.expression, None)
            self.context(tables={}, parent= parent_ctx)
            
    
    def _encode(self, plan: Step, contexts: Dict[Step, Any]):
        # expr = scope.expression
        # plan = Plan(expr)
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
                raise NotImplementedError(f"Failed to encode step '{node.id}' of type {type(node)}") from e
        root = plan.root
        outputs = contexts[root].tables[root.name] 
        
        
        print(f'==== Finished encoding root node with expression: {root} ====')
        print(f'Root operator name : {root.name}')
        print(contexts[root].tables)
        for reader, _ in contexts[root]:
            print(f"  Row ID: {reader.row.rowid}, Columns: {reader.row.columns}")
        return outputs
    
    def _project_and_filter(self, node: Step, context: Context) -> Context:
        if node.condition:
            context = self.filters(node, context)
        if node.projections:
            context = self.project(node, context)
        return context
        
    def scan(self, node: Scan, context):
        logger.info(f"Processing Scan node {node.name} with source: {node.source}, {node.source.alias_or_name}")
        sql_conditions = []
        rows = []
        if isinstance(node.source, exp.Table):
            rows = self.instance.get_rows(node.source.name)
            
            scope_columns = self.current_scope.scope_columns
            visited = set()
            for column in scope_columns:
                if column.sql() in visited:
                    continue
                visited.add(column.sql())
                
                if column.table == node.source.alias_or_name:
                    dtype = self.instance.get_column_type(node.source.name, column.name, dialect= self.dialect)
                    nullable = self.instance.nullable(node.source.name, column.name)
                    is_unique = False
                    if self.instance.is_unique(node.source.name, column.name):
                        is_unique = True
                    col = exp.Column(this= exp.to_identifier(column.name, quoted=True), table = node.source.alias_or_name, _type = dtype, is_unique= is_unique, nullable = nullable)
                    col.type = dtype
                    sql_conditions.append(col)
        
        self.tracer.which_path(scope_id= self.current_scope.node_id,  step_type= node.type_name, step_name= node.name, sql_conditions= sql_conditions, takens= [PBit.TRUE] * len(sql_conditions),  branch= True)
        ### we should update coverage here based on the symbolic expressions, instead of just marking all conditions as taken
        for row in rows:
            symbolic_exprs = [row[columnref.name] for columnref in sql_conditions]
            self.tracer.which_path(scope_id=self.current_scope.node_id, step_type= node.type_name, step_name= node.name, sql_conditions= sql_conditions, smt_exprs= symbolic_exprs, takens= [PBit.TRUE] * len(symbolic_exprs), branch= True, rowids= row.rowid)
                
        derived_schema = DerivedSchema(columns = self.instance.column_names(node.source.name, dialect= self.dialect), rows = rows)
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
                ctx = self.encode_condition(project, scope = context.env["scope"])
                row[alias_name] =  ctx[project]
                smt_conditions.extend(ctx.get("smt_conditions"))
                sql_conditions.extend(ctx.get("sql_conditions"))
                
            sink.append(Row(this = reader.row.rowid, columns = row))
            takens = [16 if isinstance(sql, exp.Column) else int(smt.concrete)
                        for smt, sql in zip(smt_conditions, sql_conditions)]
            self.tracer.which_path(scope_id=self.current_scope.node_id, 
                                   step_type= "Project", step_name= node.name, sql_conditions=sql_conditions, smt_exprs = smt_conditions, takens = takens, branch=True, rowids=reader.row.rowid)
        return self.context( {node.name : sink})
    
    def filters(self, node: Step, context: Context) -> Dict:
        if node.condition is None:
            return context
        rows = []
        for reader, _ in context:
            ctx = self.encode_condition(node.condition, scope = context.env["scope"])
            result = ctx[node.condition]
            branch = result.concrete is True
            smt_conditions = ctx['smt_conditions']
            sql_conditions = ctx['sql_conditions']
            if branch:
                rows.append(reader.row)
            takens = [
                b.concrete is True for b in smt_conditions
            ]
            self.tracer.which_path(scope_id=self.current_scope.node_id, step_type= "Filter", step_name= node.name, sql_conditions=sql_conditions, smt_exprs=smt_conditions, takens=takens, branch=branch, rowids=reader.row.rowid)
        
        return self.context({
            name : DerivedSchema(table.columns, rows, column_range= table.column_range) for name, table in context.tables.items()
        })
    
    def _inner_join(self, node, join, source_context: Context, join_context: Context) -> List:
        
        logger.info(f'start to processing inner join, {node.condition}')
        rows = []
        for left_row in source_context.table:
            left_flag = False
            for right_row in join_context.table:
                combined_row = left_row.row + right_row.row
                smt_exprs, sql_conditions = [], []
                for source_key, join_key in zip(join['source_key'], join['join_key']):
                    smt_exprs.append(combined_row[source_key.name].eq(combined_row[join_key.name]))
                    sql_conditions.append(exp.EQ(this= source_key, expression= join_key))
                
                smt_expr = reduce(lambda x, y: x.and_( y), smt_exprs)
                branch = smt_expr.concrete is True
                rowids = left_row.row.rowid
                
                if branch:
                    left_flag = True
                    rows.append(combined_row)
                    rowids = combined_row.rowid
                    takens = [2] * len(smt_exprs) #[2 if b.concrete is True else 3 for b in smt_exprs]
                    self.tracer.which_path(scope_id=self.current_scope.node_id, step_type= node.type_name, step_name= node.name, sql_conditions= sql_conditions, smt_exprs= smt_exprs, takens= takens, branch= branch, rowids= rowids)
                    
            if not left_flag:
                self.tracer.which_path(scope_id=self.current_scope.node_id, step_type= node.type_name, step_name= node.name, sql_conditions= sql_conditions, smt_exprs= [], takens= [3] * len(sql_conditions), branch= False, rowids= left_row.row.rowid)     
        
        return rows
    
    def _left_join(self, node, join, source_context: Context, join_context: Context) -> List[Dict]:
        
        rows = []
        for left_row in source_context.table:
            smt_exprs = []
            for right_row in join_context.table:
                combined_row = left_row.row + right_row.row
                smt_conditions, sql_conditions = [], []
                for source_key, join_key in zip(join['source_key'], join['join_key']):
                    smt_conditions.append(combined_row[source_key.name].eq(combined_row[join_key.name]))
                    sql_conditions.append(exp.EQ(this= source_key, expression= join_key))
            
                smt_expr = reduce(lambda x, y: x.and_( y), smt_conditions)
                smt_exprs.append(smt_expr)
                branch = smt_expr.concrete is True
                if branch:
                    rows.append(combined_row)
                    takens = [2 if b else 3 for b in smt_conditions]
                    self.tracer.which_path(scope_id=self.current_scope.node_id, step_type= node.type_name, step_name= node.name, sql_conditions= sql_conditions, smt_exprs= smt_conditions, takens= takens, branch= branch, rowids= combined_row.rowid)
            
            if smt_exprs and any(smt_exprs):
                continue
            null_vlaues = {column: Const(None) for column in join_context.table.columns}
            new_row = {c: v for c, v in left_row.row.items()}
            new_row.update(null_vlaues)
            row = Row(left_row.row.rowid, new_row)
            smt_condition = reduce(lambda x, y: x.and_(y).not_(), smt_exprs)
            self.tracer.which_path(scope_id=self.current_scope.node_id, step_type= node.type_name, step_name= node.name, sql_conditions= sql_conditions, smt_exprs= [smt_condition], takens= [3], branch= True, rowids= row.rowid)
            rows.append(row)
        
        return rows
    def _right_join(self, node, join, source_context: Context, join_context: Context) -> List[Dict]:
        ...
    def _natural_join(self, node, join, source_context: Context, join_context: Context) -> List[Dict]:
        
        
        rows = []
        source_keys = []
        join_keys = []
        for column in source_context.table.columns:
            if column in join_context.table.columns:
                source_keys.append(exp.Column(this= exp.to_identifier(column), table= source_context.table.alias_or_name))
                join_keys.append(exp.Column(this= exp.to_identifier(column), table= join_context.table.alias_or_name))
                        
        for left_row in source_context.table:
            for right_row in join_context.table:
                combined_row = left_row.row + right_row.row
                smt_exprs, sql_conditions = [], []
                for source_key, join_key in zip(source_keys, join_keys):
                    smt_exprs.append(combined_row[source_key.name].eq(combined_row[join_key.name]))
                    sql_conditions.append(exp.EQ(this= source_key, expression= join_key))
                smt_expr = reduce(lambda x, y: x.and_( y), smt_exprs)
                branch = smt_expr.concrete is True
                if branch:
                    rows.append(combined_row)
                takens = [2 if b.concrete is True else 3 for b in smt_exprs]
                self.tracer.which_path(scope_id=self.current_scope.node_id, step_type= node.type_name, step_name= node.name, sql_conditions= sql_conditions, smt_exprs= smt_exprs, takens= takens, branch= branch, rowids= combined_row.rowid)              
        
        return rows
    
    def join(self, node: Join, context):
        source = node.source_name
        source_table = context.tables[source]
        source_context = self.context({source: source_table})
        column_ranges = {source: range(0, len(source_table.columns))}
        # logger.info(f"column ranges: {column_ranges}")        
        for name, join in node.joins.items():
            table = context.tables[name]
            start = max(r.stop for r in column_ranges.values())
            column_ranges[name] = range(start, len(table.columns) + start)
            join_context = self.context({name: table})
            kind = join['side']
            
            if kind == "LEFT":
                rows = self._left_join(node, join, source_context, join_context)
            elif kind == "RIGHT":
                rows = self._right_join(node, join, source_context, join_context)
            elif kind == "NATURAL":
                rows = self._natural_join(node, join, source_context, join_context)
            else:
                rows = self._inner_join(node, join, source_context, join_context)
                
            source_context = self.context(
                {
                    name: DerivedSchema(source_table.columns + table.columns, rows, column_range)
                    for name, column_range in column_ranges.items()
                }
            )
        return self._project_and_filter(node, source_context)
    
    def aggregate(self, node: Aggregate, context):
        having_condition = None
        aggregations, aggregation_alias = [], {}
        
        for agg_func in node.aggregations:
            if node.condition and agg_func.alias_or_name == node.condition.alias_or_name:
                having_condition = agg_func
            else:
                aggregation_alias[agg_func.alias_or_name] = agg_func.this
                aggregations.append(agg_func)
        
        operand_alias_names = {node.alias_or_name: node.this for node in node.operands}
        
        if node.operands:
            operand_schema = DerivedSchema(self.derived_schema(node.operands).columns)
            for reader, ctx in context:
                mapping = {}
                for operand in node.operands:
                    alias_name = operand.alias_or_name
                    if isinstance(operand, exp.Alias):
                        operand = operand.this
                    if isinstance(operand, exp.Distinct):
                        operand = operand.expressions[0]
                    if isinstance(operand, exp.Star):
                        r = Const(this = 1, dtype= DataType.build('int'))
                    else:
                        ctx = self.encode_condition(operand, scope = context.env["scope"])
                        r = ctx[operand]
                    mapping[alias_name] = r
                
                operand_schema.append(mapping)
            for i, (a, b) in enumerate(zip(context.table.rows, operand_schema.rows)):
                new_row = {k: v for k, v in a.items()}
                new_row.update({k: v for k, v in b.items()})
                context.table.rows[i] = Row(this = a.rowid, columns = new_row)
            width = len(context.columns)
            for column in operand_schema.columns:
                if column not in context.table.columns:
                    context.table.add_columns(column)
            operand_table = DerivedSchema(
                context.columns,
                context.table.rows,
                range(width, -1),
            )
            context = self.context(
                {
                    None: operand_table,
                    **context.tables,
                }
            )
        
        sink = self.derived_schema(node.projections)
        
        groups = {}
        for reader, _ in context:
            row = reader.row
            group_key = ()
            alias = []
            for gid, expression in node.group.items():
                group_key += (row[expression.alias_or_name],)
                alias.append(gid)                
            concrete_group_key = tuple(v.concrete for v in group_key)
            if concrete_group_key not in groups:
                groups[concrete_group_key] = {"group_key": group_key, "rows": [], "alias": alias}
            groups[concrete_group_key]["rows"].append(row)
        
        self.groupby(node, groups)
        result_rows = self.aggregate_functions(node, groups, aggregations, operand_alias_names)
        
        sink = self.derived_schema(list(node.group) + aggregations)
        if node.projections:
            for row in result_rows:
                mappings = {}
                for project in node.projections:
                    alias_name = project.alias_or_name
                    if isinstance(project, exp.Alias):
                        alias_name = project.this.alias_or_name
                    mappings[project.alias_or_name] = row[alias_name]
                sink.append(Row(this = row.rowid,columns =  mappings))
        else:
            sink.rows.extend(result_rows)
        context = self.context({node.name: sink, **{name: sink for name in context.tables}})
        if having_condition:
            return self.having(node, having_condition, aggregation_alias, operand_alias_names, context)
        return context
    
    def groupby(self, node: Aggregate, groups: Dict):
        if not node.group:
            return
        
        sql_conditions, takens = [], []
        for group in list(node.group.values()):
            sql_conditions.append(group)
            takens.append(PBit.GROUP_SIZE)
        
        for _, group_info in groups.items():
            group_key = group_info["group_key"]
            group_rows = group_info["rows"]
            rowids = sum((row.rowid for row in group_rows), ())
            g = AggGroup(this = rowids, group_key = group_key, group_values = group_rows)
            smt_conditions = [g] * len(sql_conditions)
            self.tracer.which_path(scope_id=self.current_scope.node_id, step_type= "Groupby", step_name= node.name, sql_conditions= sql_conditions, smt_exprs= smt_conditions, takens= takens, branch= True, rowids= rowids)
        
    
    def aggregate_functions(self, node: Aggregate, groups: Dict, aggregations: List, operand_alias_names: Dict, ) -> List[Row]:
        result_rows = []
        for _, group_info in groups.items():
            group_key = group_info["group_key"]
            group_rows = group_info["rows"]
            rowids = sum((row.rowid for row in group_rows), ())
            new_row = {g_name: k for g_name, k in zip(group_info['alias'], group_key)}
            
            for func_index, agg_func in enumerate(aggregations):
                alias = agg_func.alias_or_name
                func = agg_func.this
                operand_alias = func.this.alias_or_name
                operand = func.unnest_operands()[0]
                if operand.alias_or_name in operand_alias_names:
                    operand = operand_alias_names[operand.alias_or_name]
                
                values = []
                for row in group_rows:
                    v = row[operand_alias]
                    if isinstance(operand, exp.Star) or v.concrete is not None:
                        values.append(v)
                if isinstance(operand, exp.Distinct):
                    values = list(set(values))                    
                if isinstance(func, exp.Count):
                    value = Const(this = len(values), _type = DataType.build('int'))
                    value.type = DataType.build('int')
                elif isinstance(func, exp.Sum):
                    sum_value = sum(values) if values else Const(0, _type= DataType.build('int'))
                    value = Const(this = sum_value, _type= DataType.build('int'))
                    value.type = DataType.build('int')
                    
                elif isinstance(func, exp.Max):
                    min_value = (
                            max(values)
                            if values
                            else None
                        )
                    value = Const(min_value, _type=agg_func.type)
                elif isinstance(func, exp.Min):
                    min_value = (
                            min(values)
                            if values
                            else None
                        )
                    value = Const(min_value, _type=agg_func.type)
                
                elif isinstance(agg_func.this, exp.Avg):
                    if values:
                        avg_value = sum(values) / len(values)
                    else:
                        avg_value = None
                    value = Const(avg_value, _type= DataType.build('REAL'))
                else:
                    raise NotImplementedError(f"Aggregation function {func} not supported yet.")
                new_row[alias] = value
            result_rows.append(Row(this = rowids, columns =  new_row))
            g = AggGroup(this = rowids, group_key = group_key, group_values = group_rows)
            sql_conditions = list(aggregations)
            
            smt_conditions = [g] * len(sql_conditions)
            takens = [PBit.AGGREGATE_SIZE] * len(sql_conditions)
            if aggregations:
                self.tracer.which_path(scope_id=self.current_scope.node_id, step_type= "Aggregate", step_name= node.name, sql_conditions= sql_conditions, smt_exprs= smt_conditions, takens= takens, branch= True, rowids= rowids,  operand_alias_names = operand_alias_names)
                
        return result_rows
    def having(self, node: Aggregate, having: exp.Expression, aggregation_alias: Dict[str, exp.Expression], operand_alias_names: Dict[str, exp.Expression], context: Context) -> Dict:
        if node.condition is None:
            return context
        logger.info(f"Processing Having condition: {having}")
        
        cond = having.copy()
        def replace_func(e):
            for alias, agg_func in aggregation_alias.items():
                if e == agg_func:
                    return exp.Column(this = exp.to_identifier(alias), table = node.name)
            return e
        def recover_sql_condition(e):
            if isinstance(e, exp.Column) and e.alias_or_name in aggregation_alias:
                r = aggregation_alias[e.alias_or_name]
                logger.info(f"Recovering SQL condition: {e} to {r}, type{r}")
                return r
            return e
            
        condition = cond.transform(replace_func)
        condition = condition.this
        rows = []
        for reader, _ in context:
            ctx = self.encode_condition(condition, scope = context.env["scope"])
            result = ctx[condition]
            branch = result.concrete is True
            smt_conditions = ctx['smt_conditions']
            sql_conditions = ctx['sql_conditions']
            if branch:
                rows.append(reader.row)
            takens = [
                (PBit.HAVING_TRUE if b.concrete is True else PBit.HAVING_FALSE)
                for b in smt_conditions
            ]
            
            covered_sql_conditions = []
            
            for sql_condition in sql_conditions:
                covered_sql_conditions.append(sql_condition.transform(recover_sql_condition))
            
            logger.info(f"Having condition evaluated to {branch} with SMT conditions {smt_conditions} and SQL conditions {sql_conditions}, covered_sql_conditions: {covered_sql_conditions}")
            self.tracer.which_path(scope_id=self.current_scope.node_id, step_type= "Having", step_name= node.name, sql_conditions=covered_sql_conditions, smt_exprs=smt_conditions, takens=takens, branch=branch, rowids=reader.row.rowid,  aggregation_alias_names = aggregation_alias, operand_alias_names= operand_alias_names)
            
        return self.context({
            name : DerivedSchema(table.columns, rows, column_range= table.column_range) for name, table in context.tables.items()
        })
        
    @non_fatal(default_from_args=lambda *args, **kwargs: args[2])
    def sort(self, node: Sort, context):
        
        all_columns = list(context.columns)
        for p in [p.alias_or_name for p in node.projections]:
            if p not in all_columns:
                all_columns.append(p)
        sink = self.derived_schema(all_columns)
        index = 0
        for reader, ctx in context:
            o_row = {k : v for k, v in reader.row.items()}
            for p in node.projections:
                alias = p.alias_or_name
                p = p.this if isinstance(p, exp.Alias) else p
                try:
                    o_row[p.alias_or_name] = reader.row[alias]
                except KeyError:
                    if p.args.get('table') in ctx.tables:
                        table = ctx.tables[p.args.get('table')]
                        if p.name in table.columns:
                            o_row[p.alias_or_name] = table.rows[index][p.name]
            index += 1
            sink.append(Row(reader.row.rowid, o_row))
        
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
        def sort_key(row):
            key = []
            for expression in node.key:
                
                v = row[expression.this.alias_or_name].concrete
                desc = expression.args.get('desc')
                null_first = expression.args.get('nulls_first', False)
                
                if v is None:
                    w = 1 if null_first else -1
                    key.append((w, None))
                else:
                    key.append((0, SortValue(v, desc)))
            return tuple(key)
        
        sorted_data = sorted(sink.rows, key=sort_key)
        sql_conditions = [o.this for o in node.key]
        
        for row in sorted_data:
            smt_conditions = [row[o.this.alias_or_name]  for o in node.key]
            self.tracer.which_path(scope_id= self.current_scope.node_id, step_type= "Sort", step_name= node.name, sql_conditions= sql_conditions, smt_exprs= smt_conditions, takens= [True] * len(smt_conditions), branch= True, rowids= row.rowid)
        rows = sorted_data   
        if not math.isinf(node.limit):
            rows = sorted_data[0 : node.limit]
        new_rows = []
        for row in rows:
            new_row = {}
            for p in node.projections:
                new_row[p.alias_or_name] = row[p.alias_or_name]
            new_rows.append(Row(row.rowid, new_row))
        
        output = DerivedSchema(
            [p.alias_or_name for p in node.projections],
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
    
    
    def encode_condition(self, condition: exp.Expression, ctx: Optional[Dict] = None, **kwargs):
        
        ctx = ctx if ctx is not None else {}
        ctx.update(**kwargs)
        if condition in ctx:
            return ctx
        
        result = condition.transform(self.transform, copy = True, ctx = ctx)
        mappings = ctx.pop("mappings", {})
        
        for smt_expr in ctx.get("smt_conditions", []):
            sql_cond = smt_expr.transform(lambda node: mappings[node] if node in mappings else node, copy = True)
            ctx.setdefault('sql_conditions', []).append(sql_cond)
        if not ctx.get('sql_conditions'):
            for smt_cond, sql_cond in mappings.items():
                ctx.setdefault('sql_conditions', []).append(sql_cond)
                ctx.setdefault("smt_conditions", []).append(smt_cond)
        else:
            logger.info(f"sql conditions: ")
            for sql_cond in ctx['sql_conditions']:
                logger.info(f"{repr(sql_cond)} with type {sql_cond.key}")
            
            logger.info(f"smt conditions: ")
            for smt_cond in ctx['smt_conditions']:
                logger.info(f"{repr(smt_cond)} with type {smt_cond.key}")
        ctx[condition] = result
        return ctx
        
    def _get_sql_condition(self, smt_conditions: List[exp.Expression], ctx: Dict):
        mappings = ctx.get("mappings", {})
        sql_conditions = []
        for smt_cond in smt_conditions:
            sql_conditions.append(smt_cond.transform(lambda node: mappings[node] if node in mappings else node, copy = True))
        return sql_conditions

    def transform(self, condition: exp.Expression, ctx: Dict[str, Any]):
        if isinstance(condition, exp.Predicate):
            ctx.setdefault("smt_conditions", []).append(condition)
            
        if isinstance(condition, exp.Column):
            column = condition
            table = column.table
            column_name = column.name
            row = ctx['scope'][table].row
            smt_expr = row[column_name]            
            ctx.setdefault("mappings", {})[smt_expr] = column
            return smt_expr
        
        elif isinstance(condition, exp.Literal):
            from .helper import to_literal
            literal = to_literal(condition, datatype= condition.type)
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
                smt_expr = when.this.transform(self.transform, copy = True, ctx = ctx)
                if smt_expr.concrete:
                    return when.args.get("true").transform(self.transform, copy = True, ctx = ctx)
            return condition.args.get("default").transform(self.transform, copy = True, ctx = ctx)
        return condition
    
    def exists(self, expr: exp.Exists, ctx):
        raise NotImplementedError("EXISTS subqueries are not supported yet.")
        subquery_scope = Scope(expr.this, parent= ctx['scope'])
        subquery_context = self._encode(subquery_scope, contexts= {})
        return Const(bool(subquery_context), dtype= DataType.build('bool'))
    
    def subquery(self, expr: exp.Subquery, ctx):
        
        
        
        
        raise NotImplementedError("Subqueries are not supported yet.")