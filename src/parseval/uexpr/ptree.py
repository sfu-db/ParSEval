from __future__ import annotations
from typing import Optional, Dict, Any, Set, Union, List, TYPE_CHECKING, Tuple
import logging
from .node import Constraint, PlausibleBranch, PlausibleType, PBit

if TYPE_CHECKING:
    from src.parseval.plan.rex import Expression, ColumnRef, Datatype
    from src.parseval.plan.rex import LogicalOperator
    from src.parseval.instance import Instance
from collections import defaultdict
from ordered_set import OrderedSet
from src.parseval.symbol import Variable, Symbol, Const, Distinct
from src.parseval.helper import group_by_concrete
from .checks import resolve_check
from sqlglot import exp as sqlglot_exp
from src.parseval.plan import ExpressionEncoder, ColumnRef

logger = logging.getLogger("parseval.symbolic")


class ExprEncoder(ExpressionEncoder):
    # def __init__(self, expr, row, symbolic_registry=None):
    #     super().__init__(expr, row, symbolic_registry)

    def visit_columnref(self, expr, parent_stack, context):
        smt_expr = context[expr.qualified_name]
        return smt_expr


class UExprToConstraint:

    def __init__(self, declare, threshold=1):
        """
        positive_nodes: a mapping from operator id to all positive path constraint nodes
        prev_operator: the last SQL operator we have seen
        """
        self.constraints = []
        self.leaves: Dict[str, Constraint] = {}
        self.root_constraint = Constraint(self, None, None)
        self.positive_nodes: Dict[str, Set[Constraint]] = defaultdict(set)
        self.positive_nodes["ROOT"].add((self.root_constraint, PBit.TRUE))
        self.prev_operator: Optional[LogicalOperator] = "ROOT"
        self.declare = declare
        self.threshold = threshold
        # Index rows -> set of (Constraint, bit) for quick lookup when updating
        # mappings from runtime rows to UExpr nodes. Structure:
        # { rowid: { operator_id:  OrderedSet((Constraint, PlausibleBit), ...)} }
        self.row_index: Dict[str, Set[Tuple]] = defaultdict(
            lambda: defaultdict(OrderedSet)
        )
        self.strategy_config: Optional[Dict[object, bool]] = None

    def on_scope_enter(self, operator: LogicalOperator):
        """Optional hook for setup."""
        pass

    def on_scope_exit(self, operator: LogicalOperator):
        """
        Refined logic to finalize state after a node is successfully visited.
        """
        for pattern, leaf in self.leaves.items():
            if isinstance(leaf, PlausibleBranch):
                leaf.update_mark()
        if operator.operator_id in self.positive_nodes:
            self.prev_operator = operator.operator_id

    def _index_row(
        self,
        operator_id: str,
        rowid: Any,
        node: Constraint,
        bit: PBit,
        branch: Union[bool, str],
    ) -> None:
        """Internal helper to index a rowid -> (node, bit) mapping.

        This accelerates lookup of which UExpr nodes correspond to a runtime
        row (or tuple of rowids for composed rows like joins). The index is
        updated from Constraint.update_delta when symbolic rows are recorded.
        """
        if branch:
            try:
                self.row_index[rowid][operator_id].add((node, bit))
            except Exception:
                return

    def reset(self):
        self.prev_operator = "ROOT"
        c = [self.root_constraint]
        self.row_index.clear()
        while c:
            op = c.pop()
            op.symbolic_exprs.clear()
            op.delta.clear()
            # op.metadata.clear()
            for k, child in op.children.items():
                if isinstance(child, Constraint):
                    c.append(child)

    def which_path(
        self,
        operator: LogicalOperator,
        sql_conditions: List[Expression],
        symbolic_exprs: List[Union[List[Symbol], Symbol]],
        takens: List[bool],
        rowids: Tuple[str, ...],
        branch: Union[bool, str],
        **kwargs,
    ):
        assert isinstance(rowids, tuple), f"rowids must be a tuple, got {type(rowids)}"
        assert len(sql_conditions) == len(
            takens
        ), f"Conditions and takens length mismatch, sql_conditions: {sql_conditions}, takens: {takens}"
        operator_id = operator.operator_id
        starts: Set[tuple] = set()
        for rowid in rowids:
            nodes = self.row_index[rowid][self.prev_operator]
            if nodes:
                starts.add(nodes[-1])
        if not starts:
            starts = self.positive_nodes[self.prev_operator]

        for start, bit in starts:
            node, b = start, bit
            for index, (sql_condition, taken) in enumerate(zip(sql_conditions, takens)):
                node = node.add_child(
                    operator=operator,
                    sql_condition=sql_condition,
                    bit=b,
                    branch=branch,
                    **kwargs,
                )
                smt_expr = symbolic_exprs[index] if index < len(symbolic_exprs) else []
                b = PBit.from_int(taken)
                node.update_delta(b, smt_expr, rowids, branch)

                # node.update_metadata(**kwargs)
            if branch and (node, b) not in self.positive_nodes[operator.operator_id]:
                self.positive_nodes[operator_id].add((node, b))

    def next_path(self):
        leaves = dict(
            sorted(
                (
                    (pattern, leaf)
                    for pattern, leaf in self.leaves.items()
                    if leaf.plausible_type
                    not in {PlausibleType.INFEASIBLE, PlausibleType.COVERED}
                    and leaf.attempts <= self.threshold
                ),
                key=lambda item: len(item[0]),
                reverse=False,
            )
        )
        for pattern, leaf in leaves.items():
            if leaf.attempts > self.threshold:
                continue
            if leaf.bit() is PBit.JOIN_RIGHT:
                continue

            if leaf.plausible_type in {PlausibleType.UNEXPLORED, PlausibleType.PENDING}:
                leaf.mark_pending()
                return leaf
        return None

    def _declare_db_constraints(self, instance, var_to_columnref, columnref_to_var):

        for columnref, variable in columnref_to_var.items():
            domain = instance.column_domain.get_or_create_pool(
                variable.name,
                table_name=columnref.table,
                column_name=columnref.name,
            )
            if domain.unique:
                data = instance.get_column_data(
                    table_name=columnref.table, column_name=columnref.name
                )
                values = [Const(d.concrete, dtype=domain.datatype) for d in data]
                unique_constraint = Distinct(variable, *values, dtype="bool")
                self.declare.declare_constraint("database", unique_constraint)

    def _declare_variables(
        self,
        instance: Instance,
        path: List[Constraint],
        patterns: List[PBit],
        context: Dict,
    ):
        column_pool = instance.column_domain
        var_to_columnref, columnref_to_var = {}, {}
        for _, node in zip(patterns, path[1:]):
            sql_condition = node.sql_condition
            columnrefs = set(sql_condition.find_all(ColumnRef))
            if not columnrefs:
                continue
            if node["subquery"] != context["subquery"]:
                continue

            for columnref in columnrefs:
                var_name = f"{columnref.qualified_name}"
                if var_name not in var_to_columnref:
                    domain = column_pool.get_or_create_pool(
                        var_name,
                        table_name=columnref.table,
                        column_name=columnref.name,
                    )
                    var = Variable(var_name, dtype=domain.datatype)
                    var_to_columnref[var_name] = columnref
                    columnref_to_var[columnref] = var

                    self.declare.declare_variable(var, columnref)

                    if domain.unique:
                        data = instance.get_column_data(
                            table_name=columnref.table, column_name=columnref.name
                        )
                        values = [
                            Const(d.concrete, dtype=domain.datatype) for d in data
                        ]
                        logger.info(
                            f"Declaring unique constraint for {var_name}: {values}"
                        )
                        unique_constraint = Distinct(var, *values, dtype="bool")
                        self.declare.declare_constraint("database", unique_constraint)
        fk_variables = {}
        for var_name, columnref in var_to_columnref.items():
            if (
                columnref.table in instance.foreign_keys
                and columnref.name in instance.foreign_keys[columnref.table]
            ):
                ref_table, ref_col = instance.foreign_keys[columnref.table][
                    columnref.name
                ]
                fk_constraints = []
                flag = False
                fk_varname = f"{ref_table}.{ref_col}"

                for colref2, var2 in columnref_to_var.items():
                    if colref2.table == ref_table and colref2.name == ref_col:
                        flag = True
                        fk_constraints.append(columnref_to_var[columnref].eq(var2))
                        fk_varname = var2.name
                domain = column_pool.get_or_create_pool(
                    fk_varname, table_name=ref_table, column_name=ref_col
                )
                if not flag:
                    var = Variable(fk_varname, dtype=domain.datatype)
                    for c in instance.catalog.get_table(ref_table).columns:
                        if c.name == ref_col:
                            fk_ref_col = c
                            break
                    fk_variables[fk_varname] = (var, fk_ref_col)
                    self.declare.declare_variable(var, fk_ref_col)
                    fk_constraints.append(columnref_to_var[columnref].eq(var))
                    if domain.unique:
                        data = instance.get_column_data(
                            table_name=ref_table, column_name=ref_col
                        )
                        values = [
                            Const(d.concrete, dtype=domain.datatype) for d in data
                        ]
                        unique_constraint = Distinct(var, *values, dtype="bool")
                        self.declare.declare_constraint("database", unique_constraint)

                data = instance.get_column_data(
                    table_name=ref_table, column_name=ref_col
                )
                for d in data:
                    fk_constraints.append(
                        columnref_to_var[columnref].eq(
                            Const(d.concrete, dtype=domain.datatype)
                        )
                    )
                from functools import reduce

                fk_constraint = reduce(lambda x, y: x.or_(y), fk_constraints)
                logger.info(f"fk_constraint: {fk_constraint}")
                self.declare.declare_constraint("database", fk_constraint)
        for var_name, (var, ref_col) in fk_variables.items():
            self.declare.declare_variable(var, ref_col)
        return var_to_columnref, columnref_to_var

    def declare_coverage_constraints(
        self, plausible: PlausibleBranch, instance: Instance, skips=None
    ):
        skips = skips or set()
        path = list(reversed(plausible.get_path_to_root()[1:]))
        patterns = list(reversed(plausible.pattern()))
        context = {
            "has_having": False,
            "patterns": patterns,
            "subquery": path[1]["subquery"],
        }
        var_to_columnref, columnref_to_var = self._declare_variables(
            instance, path, patterns, context
        )

        for bit, node in zip(patterns, path[1:]):
            if context["has_having"]:
                break

            if (node.operator.operator_type, bit) in skips:
                continue
            if node["subquery"] != context["subquery"]:
                continue
            declare_strategy = resolve_check(node.operator.operator_type, bit)
            constraints = declare_strategy.declare(self, node, context)

            for constraint in constraints:
                if isinstance(constraint, Symbol):
                    logger.info(f"declaring symbol constraint: {constraint}")
                    self.declare.declare_constraint(
                        node.operator.operator_type, constraint
                    )
                columnrefs = set(constraint.find_all(ColumnRef))
                if not columnrefs:
                    continue

                if isinstance(constraint, sqlglot_exp.Predicate):
                    ### Encode Coverage to SMT constraints
                    encoder = ExprEncoder()
                    try:
                        kwgs = {
                            c.qualified_name: columnref_to_var[c] for c in columnrefs
                        }
                    except Exception as e:
                        # logger.info(columnref_to_var)
                        for c in columnrefs:
                            logger.info(f"columnref: {c}, {type(c)}")
                        for key, value in columnref_to_var.items():
                            logger.info(f"key: {key}, value: {value}, {type(key)}")
                        raise e

                    def replace(expr, ctx):
                        if isinstance(expr, ColumnRef):
                            return ctx[expr.qualified_name]

                        return expr

                    # condition = constraint.transform(replace, kwgs)

                    ctx = encoder.encode(constraint, **kwgs)
                    condition = ctx[constraint]
                    logger.info(
                        f"Declaring constraint for bit: {bit}, condition: {condition}"
                    )
                    self.declare.declare_constraint(
                        node.operator.operator_type, condition
                    )

    # def _declare_smt_constraints(self, plausible: PlausibleBranch):
    #     """
    #     declare SMT constraints for the plausible branch
    #     """
    #     path = plausible.get_path_to_root()
    #     path = list(reversed(path[1:]))
    #     patterns = list(reversed(plausible.pattern()))
    #     # ### we first process constraints in the path to root

    #     context = {"has_having": False, "patterns": patterns}

    #     for bit, node in zip(patterns, path[1:]):
    #         if context["has_having"]:
    #             break
    #         logger.info(f"Declaring constraint for bit: {bit}, node: {node}")
    #         strategy = resolve_check(node.operator.operator_type, bit)
    #         # Check per-tracer configuration whether this strategy should run
    #         enabled = True
    #         if strategy is not None:
    #             if self.strategy_config is not None:
    #                 # operator-specific key first
    #                 key = (node.operator.operator_type, bit)
    #                 if key in self.strategy_config:
    #                     enabled = bool(self.strategy_config[key])
    #                 elif bit in self.strategy_config:
    #                     enabled = bool(self.strategy_config[bit])
    #             if enabled:
    #                 strategy.declare(self, node, context)
    #                 continue

    #         # Fallback behavior when no strategy is registered
    #         if bit is PBit.FALSE:
    #             if node.operator.operator_type in {"Having"}:
    #                 continue
    #             elif isinstance(node.sql_condition, rex.sqlglot_exp.Predicate):
    #                 pos_constraint = rex.negate_predicate(node.sql_condition)
    #                 self.declare(node.operator.operator_type, pos_constraint)
    #         else:
    #             if node.operator.operator_type in {"Having"}:
    #                 self._declare_having_constraints(node, bit, context)
    #             else:
    #                 self.declare(node.operator.operator_type, node.sql_condition)

    # def _declare_duplicate_constraints(self, node, context):
    #     if isinstance(node.sql_condition, rex.ColumnRef):
    #         if not node.sql_condition.args.get("unique", False) and node.symbolic_exprs:
    #             constraint = node.sql_condition
    #             value_counts = group_by_concrete(node.symbolic_exprs[PBit.TRUE])
    #             if value_counts:
    #                 values = sorted(value_counts.items(), key=lambda x: -len(x[1]))
    #                 value = values[0][1][0]
    #                 literal = convert(value.concrete)
    #                 literal.set("datatype", node.sql_condition.datatype)
    #                 constraint = rex.sqlglot_exp.EQ(
    #                     this=node.sql_condition, expression=literal
    #                 )
    #             self.declare(node.operator.operator_type, constraint)

    # def _declare_null_constraints(self, node, context):
    #     columnrefs = list(node.sql_condition.find_all(rex.ColumnRef))
    #     for columnref in columnrefs:
    #         if columnref.datatype and columnref.datatype.nullable:
    #             null_constraint = rex.Is_Null(this=columnref)
    #             self.declare(node.operator.operator_type, null_constraint)
    #             return

    # def _declare_join_true_constraints(self, node, context):
    #     """
    #     declare SMT constraints for join true
    #     """
    #     self.declare(node.operator.operator_type, node.sql_condition)

    #     # if isinstance(node.sql_condition, rex.sqlglot_exp.Predicate):
    #     #     pos_constraint = node.sql_condition
    #     #     self.declare(node.operator.operator_type, pos_constraint)

    # def _declare_join_right_constraints(self, node, context):
    #     """
    #     declare SMT constraints for join right
    #     """
    #     column_refs = list(node.sql_condition.find_all(rex.ColumnRef))
    #     right_table = column_refs[1].table
    #     self.declare(node.operator.operator_type, column_refs[1])

    #     logger.info(
    #         f"Declaring join right constraint for table: {right_table}, {column_refs[1]}"
    #     )

    # def _declare_join_left_constraints(self, node, context):
    #     """
    #     declare SMT constraints for join left
    #     """
    #     column_refs = list(node.sql_condition.find_all(rex.ColumnRef))
    #     left_table = column_refs[0].table
    #     self.declare(node.operator.operator_type, column_refs[0])

    #     logger.info(
    #         f"Declaring join left constraint for table: {left_table}, {column_refs[0]}"
    #     )

    #     # table_columns = {
    #     #     column_ref.table: column_ref for column_ref in column_refs
    #     # }

    #     ...

    # def _declare_smt_join_constraints(self, plausible: PlausibleBranch):
    #     """
    #     declare SMT constraints for the plausible branch
    #     """
    #     path = plausible.get_path_to_root()
    #     ### we first process constraints in the path to root
    #     for bit, node in zip(plausible.pattern(), path[1:]):
    #         if bit == PBit.FALSE:
    #             if isinstance(node.sql_condition, rex.sqlglot_exp.Predicate):
    #                 pos_constraint = rex.negate_predicate(node.sql_condition)
    #                 self.declare(node.operator.operator_type, pos_constraint)
    #         else:
    #             self.declare(node.operator.operator_type, node.sql_condition)

    # def _declare_group_count_constraints(self, node, context):
    #     """declare SMT constraints for group count"""
    #     for value in node.symbolic_exprs[PBit.TRUE]:
    #         literal = convert(value.concrete)
    #         literal.set("datatype", node.sql_condition.datatype)
    #         constraint = rex.sqlglot_exp.NEQ(
    #             this=node.sql_condition, expression=literal
    #         )
    #         self.declare(node.operator.operator_type, constraint)

    # def _declare_group_size_constraints(self, node, context):
    #     """declare SMT constraints for group size"""
    #     group_key = node.symbolic_exprs[PBit.TRUE][-1]
    #     literal = convert(group_key.concrete)
    #     literal.set("datatype", node.sql_condition.datatype)
    #     constraint = rex.sqlglot_exp.EQ(this=node.sql_condition, expression=literal)
    #     self.declare(node.operator.operator_type, constraint)

    # def _declare_sortmax_constraints(self, node, context):
    #     if node.sql_condition.args.get("unique", False):
    #         return
    #     values = node.symbolic_exprs[PBit.TRUE]
    #     max_ = max([v.concrete for v in values if v.concrete is not None])
    #     min_ = min([v.concrete for v in values if v.concrete is not None])

    #     max_literal = convert(max_)
    #     max_literal.set("datatype", node.sql_condition.args.get("datatype"))
    #     if max_ == min_:
    #         klass = rex.sqlglot_exp.NEQ
    #     else:
    #         klass = rex.sqlglot_exp.EQ
    #     constraint = klass(
    #         this=node.sql_condition,
    #         expression=max_literal,
    #     )
    #     self.declare(node.operator.operator_type, constraint)

    # def _declare_sortmin_constraints(self, node, context):
    #     if node.sql_condition.args.get("unique", False):
    #         return
    #     values = node.symbolic_exprs[PBit.TRUE]
    #     max_ = max([v.concrete for v in values if v.concrete is not None])
    #     min_ = min([v.concrete for v in values if v.concrete is not None])
    #     min_literal = convert(min_)
    #     min_literal.set("datatype", node.sql_condition.datatype)
    #     if max_ == min_:
    #         klass = rex.sqlglot_exp.NEQ
    #     else:
    #         klass = rex.sqlglot_exp.EQ
    #     constraint = klass(
    #         this=node.sql_condition,
    #         expression=min_literal,
    #     )
    #     self.declare(node.operator.operator_type, constraint)

    # def _declare_having_constraints(self, node, bit, context):
    #     """declare SMT constraints for having clause"""
    #     patterns = context["patterns"]
    #     if bit is not patterns[-1]:
    #         context["has_having"] = True
    #         return

    #     # if not list(node.sql_condition.find_all(rex.ColumnRef)):
    #     #     node.children[bit].mark_infeasible()
    #     #     return

    #     b = PBit.FALSE if bit is PBit.TRUE else PBit.FALSE

    #     if not node.symbolic_exprs[b]:
    #         return

    # logger.info(f"procesing {bit}, {b} having: {node.sql_condition}")
    # node.symbolic_exprs[b][0]
    # if bit is PBit.TRUE:
    #     ...
    # else:
    #     ...

    # pattern = "".join([str(b) for b in context["patterns"]])
    # groups = []
    # for group in node.symbolic_exprs[PBit.TRUE]:
    #     group_name = group.name
    #     assigned_pattern = node.metadata.get("group", {}).get(group_name)
    #     if assigned_pattern is None:
    #         groups.append(group)
    # if groups:
    #     group = groups[-1]
    #     node.metadata.setdefault("group", {})[group.name] = pattern

    # logger.info(f"Declaring having constraint for group: for {node.sql_condition}")

    # if isinstance(node.sql_condition, rex.sqlglot_exp.Predicate):
    #     pos_constraint = rex.negate_predicate(node.sql_condition)
    #     self.declare(node.operator.operator_type, pos_constraint)
