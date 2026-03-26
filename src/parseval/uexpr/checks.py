from __future__ import annotations

from abc import ABC, abstractmethod
from ..constants import PBit, PlausibleType, VALID_PATH_BITS
from typing import Optional, TYPE_CHECKING, Dict, Tuple, List, Any
from sqlglot import expressions as sqlglot_exp
from parseval.plan.rex import Variable, ITE
from parseval.helper import group_by_concrete
from parseval.constants import BranchType
from parseval.plan.helper import to_literal
from parseval.plan.rex import negate_predicate
from collections import OrderedDict

if TYPE_CHECKING:
    from parseval.uexpr.uexprs import PlausibleBit, PlausibleBranch, PBit, Constraint

from functools import reduce
import logging

logger = logging.getLogger("parseval.coverage")

DUPLICATE_THRESHOLD = 1
NULL_THRESHOLD = 1
POSITIVE_THRESHOLD = 1
NEGATIVE_THRESHOLD = 1
GROUP_SIZE_THRESHOLD = 2
GROUP_COUNT_THRESHOLD = 3


class Check:
    """Base checker that can be used to check plausible branches."""

    def __init__(self, **kwargs):
        self.duplicate_threshold = kwargs.get(
            "duplicate_threshold", DUPLICATE_THRESHOLD
        )
        self.null_threshold = kwargs.get("null_threshold", NULL_THRESHOLD)
        self.group_count_threshold = kwargs.get(
            "group_count_threshold", GROUP_COUNT_THRESHOLD
        )
        self.group_size_threshold = kwargs.get(
            "group_size_threshold", GROUP_SIZE_THRESHOLD
        )
        self.positive_threshold = kwargs.get("positive_threshold", POSITIVE_THRESHOLD)
        self.negative_threshold = kwargs.get("negative_threshold", NEGATIVE_THRESHOLD)

    CHECKS = {
        PBit.DUPLICATE: lambda self, plausible: self.check_duplicate(plausible),
        PBit.NULL: lambda self, plausible: self.check_null(plausible),
        PBit.TRUE: lambda self, plausible: self.check_predicate(plausible),
        PBit.PROJECT: lambda self, plausible: self.check_project(plausible),
        PBit.FALSE: lambda self, plausible: self.check_predicate(plausible),
        PBit.GROUP_COUNT: lambda self, plausible: self.check_group_count(plausible),
        # PBit.GROUP_COUNT: GroupCountStrategy(),
        PBit.GROUP_SIZE: lambda self, plausible: self.check_group_size(plausible),
        PBit.AGGREGATE_SIZE: lambda self, plausible: self.check_group_size(plausible),
        PBit.GROUP_NULL: lambda self, plausible: self.check_group_null(plausible),
        PBit.GROUP_DUPLICATE: lambda self, plausible: self.check_group_duplicate(
            plausible
        ),
        # PBit.MAX: SortMaxStrategy(),
        # PBit.MIN: SortMinStrategy(),
        PBit.JOIN_TRUE: lambda self, plausible: self.check_predicate(plausible),
        PBit.HAVING_TRUE: lambda self, plausible: self.check_predicate(plausible),
        PBit.HAVING_FALSE: lambda self, plausible: self.check_predicate(plausible),
        PBit.JOIN_TRUE: lambda self, plausible: self.check_predicate(plausible),
        PBit.JOIN_LEFT: lambda self, plausible: self.check_predicate(plausible),
        PBit.JOIN_RIGHT: lambda self, plausible: self.check_predicate(plausible),
        # PBit.TRUE: PredicateStrategy(bit=PBit.TRUE),
        # PBit.FALSE: PredicateStrategy(bit=PBit.FALSE),
        # PBit.HAVING_TRUE: HavingStrategy(bit=PBit.HAVING_TRUE),
        # PBit.HAVING_FALSE: HavingStrategy(bit=PBit.HAVING_FALSE),
    }

    def _get_positive_bit(self, plausible: "PlausibleBranch") -> Optional[PlausibleBit]:
        from parseval.uexpr.uexprs import PlausibleBranch

        if plausible.branch and plausible.bit() in VALID_PATH_BITS:
            return plausible.bit()
        for bit, child in plausible.parent.children.items():
            if isinstance(child, PlausibleBranch) and child.branch:
                return bit
        for bit in plausible.parent.coverage:
            if bit in VALID_PATH_BITS:
                return bit
        return plausible.bit()

    def check_group_count(self, plausible: "PlausibleBranch") -> PlausibleType:
        bit = plausible.bit()
        constraint: Constraint = plausible.parent

        constraint.coverage[bit].clear()
        hit = 0
        for g in constraint.coverage.get(PBit.GROUP_SIZE, []):
            constraint.coverage[bit].append(g)
            if any(v.concrete is None for v in g.group_key):
                continue
            hit += 1
        constraint.hits[bit] = hit
        logger.info(
            f"Checking group count constraint for {constraint.sql_condition}, hit: {hit}, threshold: {self.group_count_threshold}"
        )
        if hit > self.group_count_threshold:
            plausible.mark_covered()

    def check_group_duplicate(self, plausible: "PlausibleBranch") -> PlausibleType:
        bit = plausible.bit()
        positive_bit = PBit.AGGREGATE_SIZE
        constraint: Constraint = plausible.parent
        columnrefs = list(constraint.sql_condition.find_all(sqlglot_exp.Column))
        if not columnrefs:
            logger.info(
                f"set group duplicate to infeasible since no column reference is found in {constraint.sql_condition}"
            )
            plausible.mark_infeasible()

        elif all(column.args.get("is_unique", False) for column in columnrefs):
            plausible.mark_infeasible()
        else:
            logger.info(
                f"checking group duplicate constraint for {constraint.sql_condition}, columnrefs: {columnrefs}, positive_bit: {positive_bit}, coverage size: {len(constraint.coverage.get(positive_bit, []))}"
            )
            constraint.coverage[bit].clear()
            hit = 0
            for g in constraint.coverage[positive_bit]:
                if g.group_key and any(key.concrete is None for key in g.group_key):
                    continue
                values = {}
                for row in g.group_values:
                    for columnref in columnrefs:
                        v = row[columnref.name]
                        if v.concrete is not None:
                            values.setdefault(columnref.name, []).append(v.concrete)
                for items in values.values():
                    if len(items) != len(set(items)):
                        hit += 1
                        constraint.coverage[bit].append(g)
                        break

            constraint.hits[bit] = hit
            if hit > 0:
                plausible.mark_covered()

        # bit = plausible.bit()
        # constraint: Constraint = plausible.parent
        # operand_alias_names = constraint.metadata['operand_alias_names']

        # agg_func = constraint.sql_condition.this if isinstance(constraint.sql_condition, sqlglot_exp.Alias) else constraint.sql_condition
        # assert isinstance(agg_func, sqlglot_exp.AggFunc), f"the aggregation function is expected, but got {constraint.sql_condition.key}"

        # operand = agg_func.unnest_operands()[0]

        # if operand.alias_or_name in operand_alias_names:
        #     operand = operand_alias_names[operand.alias_or_name]

        # if isinstance(operand, sqlglot_exp.Star):
        #     plausible.mark_infeasible()
        # else:
        #     if all(column.args.get("is_unique", False) for column in operand.unnest_operands()):
        #         plausible.mark_infeasible()

    def check_group_null(self, plausible: "PlausibleBranch") -> PlausibleType:
        bit = plausible.bit()
        positive_bit = PBit.AGGREGATE_SIZE
        constraint: Constraint = plausible.parent
        columnrefs = list(constraint.sql_condition.find_all(sqlglot_exp.Column))
        if not columnrefs:
            logger.info(
                f"set group null to infeasible since no column reference is found in {constraint.sql_condition}"
            )
            plausible.mark_infeasible()
        elif all([columnref.args.get("nullable") is False for columnref in columnrefs]):
            logger.info(
                f"set group null to infeasible since all column references are non-nullable in {constraint.sql_condition}, columnrefs: {columnrefs}"
            )
            plausible.mark_infeasible()
        else:
            constraint.coverage[bit].clear()
            hit = 0
            for g in constraint.coverage[positive_bit]:
                if g.group_key and any(key.concrete is None for key in g.group_key):
                    continue
                for row in g.group_values:
                    for columnref in columnrefs:
                        v = row[columnref.name]
                        if v.concrete is None:
                            constraint.coverage[bit].append(v)
                            hit += 1
                            break
            constraint.hits[bit] = hit
            if hit > 0:
                plausible.mark_covered()

        # operand_alias_names = constraint.metadata['operand_alias_names']
        # agg_func = constraint.sql_condition.this if isinstance(constraint.sql_condition, sqlglot_exp.Alias) else constraint.sql_condition
        # assert isinstance(agg_func, sqlglot_exp.AggFunc), f"the aggregation function is expected, but got {constraint.sql_condition.key}"
        # operand = agg_func.unnest_operands()[0]

        # if operand.alias_or_name in operand_alias_names:
        #     operand = operand_alias_names[operand.alias_or_name]

        # if isinstance(operand, sqlglot_exp.Star):
        #     plausible.mark_infeasible()
        # else:
        #     if all(column.args.get("nullable", False) for column in operand.unnest_operands()):
        #         plausible.mark_infeasible()

    def check_duplicate(self, plausible: "PlausibleBranch") -> PlausibleType:
        bit = plausible.bit()

        constraint: Constraint = plausible.parent
        columnrefs = list(constraint.sql_condition.find_all(sqlglot_exp.Column))
        if not columnrefs or all(
            [columnref.args.get("is_unique", False) for columnref in columnrefs]
        ):
            plausible.mark_infeasible()
            plausible.branch = BranchType.NEGATIVE
        else:
            positive_bit = self._get_positive_bit(plausible)
            variables = []
            for smt_expr in constraint.coverage[positive_bit]:
                variables.extend(smt_expr.find_all(Variable))
            constraint.coverage[bit].clear()
            constraint.coverage[bit].extend(constraint.coverage[positive_bit])
            logger.info(
                f"Checking duplicate constraint for {constraint.sql_condition}. Found variables: {len(variables)}, {plausible.plausible_type}"
            )
            groups = group_by_concrete(variables)
            hit = 0
            for key, items in groups.items():
                hit = max(hit, len(items))
                if len(items) >= self.duplicate_threshold:
                    plausible.mark_covered()
            plausible.parent.hits[bit] = hit

    def check_null(self, plausible: "PlausibleBranch") -> PlausibleType:
        bit = plausible.bit()
        constraint: Constraint = plausible.parent
        columnrefs = list(constraint.sql_condition.find_all(sqlglot_exp.Column))
        if not columnrefs or all(
            [columnref.args.get("nullable") is False for columnref in columnrefs]
        ):
            plausible.mark_infeasible()
        else:
            positive_bit = self._get_positive_bit(plausible)
            constraint.coverage[bit].clear()
            constraint.coverage[bit].extend(constraint.coverage[positive_bit])
            null_count = 0
            for smt in constraint.coverage[bit]:
                for var in smt.find_all(Variable):
                    if var.concrete is None:
                        null_count += 1
                        break
            plausible.parent.hits[bit] = null_count
            if plausible.parent.hits[bit] >= self.null_threshold:

                plausible.mark_covered()

    def check_predicate(self, plausible: "PlausibleBranch") -> PlausibleType:
        bit = plausible.bit()
        constraint: Constraint = plausible.parent
        threshold = (
            self.positive_threshold if plausible.branch else self.negative_threshold
        )
        logger.info(
            "checking predicate constraint for %s, coverage size: %s, threshold: %s",
            constraint.sql_condition,
            len(constraint.coverage[bit]),
            threshold,
        )
        constraint.hits[bit] = len(constraint.coverage[bit])
        if len(constraint.coverage[bit]) >= threshold:
            plausible.mark_covered()

    def check_project(self, plausible: "PlausibleBranch") -> PlausibleType:
        bit = plausible.bit()
        constraint: Constraint = plausible.parent
        threshold = self.positive_threshold
        if len(constraint.coverage[bit]) >= threshold:
            plausible.mark_covered()

    def visit_plausible_branch(self, plausible: "PlausibleBranch") -> PlausibleType:
        if plausible.bit() in self.CHECKS:
            return self.CHECKS[plausible.bit()](self, plausible)

    def check_group_size(self, plausible: "PlausibleBranch"):
        bit = plausible.bit()
        constraint: Constraint = plausible.parent
        hit = 0
        for g in constraint.coverage[bit]:
            if len(g.group_values) >= self.group_size_threshold:
                hit += 1
                plausible.mark_covered()
        constraint.hits[bit] = hit


class Declare:
    def __init__(self, add):
        self.add = add

    DECLARES = OrderedDict(
        {
            PBit.HAVING_TRUE: lambda self, bit, node, context: self.declare_having(
                bit, node, context
            ),
            PBit.HAVING_FALSE: lambda self, bit, node, context: self.declare_having(
                bit, node, context
            ),
            PBit.AGGREGATE_SIZE: lambda self, bit, node, context: self.declare_aggregate_size(
                bit, node, context
            ),
            PBit.JOIN_LEFT: lambda self, bit, node, context: self.declare_join_left(
                bit, node, context
            ),
            PBit.JOIN_RIGHT: lambda self, bit, node, context: self.declare_join_right(
                bit, node, context
            ),
            PBit.JOIN_TRUE: lambda self, bit, node, context: self.declare_join_true(
                bit, node, context
            ),
            PBit.GROUP_COUNT: lambda self, bit, node, context: self.declare_group_count(
                bit, node, context
            ),
            PBit.GROUP_SIZE: lambda self, bit, node, context: self.declare_group_size(
                bit, node, context
            ),
            PBit.GROUP_NULL: lambda self, bit, node, context: self.declare_group_null(
                bit, node, context
            ),
            PBit.GROUP_DUPLICATE: lambda self, bit, node, context: self.declare_group_duplicate(
                bit, node, context
            ),
            PBit.MAX: lambda self, bit, node, context: self.declare_sort_max(
                bit, node, context
            ),
            PBit.MIN: lambda self, bit, node, context: self.declare_sort_min(
                bit, node, context
            ),
            PBit.DUPLICATE: lambda self, bit, node, context: self.declare_duplicate(
                bit, node, context
            ),
            PBit.NULL: lambda self, bit, node, context: self.declare_null(
                bit, node, context
            ),
            PBit.TRUE: lambda self, bit, node, context: self.declare_predicate(
                bit, node, context
            ),
            PBit.FALSE: lambda self, bit, node, context: self.declare_predicate(
                bit, node, context
            ),
        }
    )

    def _get_positive_bit(self, plausible: "PlausibleBranch") -> Optional[PlausibleBit]:
        from parseval.uexpr.uexprs import PlausibleBranch

        if plausible.branch:
            return plausible.bit()
        for bit, child in plausible.parent.children.items():
            if isinstance(child, PlausibleBranch) and child.branch:
                return bit
        for bit in plausible.parent.coverage:
            if bit in VALID_PATH_BITS:
                return bit
        return plausible.bit()

    def declare(self, bit: PBit, node: "Constraint", context: Dict) -> bool:
        if bit in self.DECLARES:
            return self.DECLARES[bit](self, bit, node, context)
        return True

    def declare_predicate(self, bit, node: "Constraint", context: Dict) -> bool:
        constraint = node.sql_condition
        if bit == PBit.FALSE:
            constraint = negate_predicate(node.sql_condition)
        self.add.declare_constraint(bit, constraint)
        return True

    def declare_duplicate(self, bit, node: "Constraint", context: Dict) -> bool:
        sql_condition = node.sql_condition
        if isinstance(sql_condition, sqlglot_exp.Column):
            constraints = []
            if (
                not node.sql_condition.args.get("is_unique", False)
                and node.coverage[bit]
            ):
                value_counts = group_by_concrete(node.coverage[bit])
                if value_counts:
                    values = sorted(value_counts.items(), key=lambda x: len(x[1]))
                    value = values[0][1][0]
                    literal = to_literal(value.concrete, sql_condition.datatype)
                    constraint = sql_condition.eq(literal)
                    constraints.append(constraint)

            self.add.declare_constraint(bit, constraints)
        return True

    def declare_null(
        self, bit, node: "Constraint", context: Dict
    ) -> List[sqlglot_exp.Expression]:

        constraints = []
        sql_condition = node.sql_condition
        columnrefs = list(sql_condition.find_all(sqlglot_exp.Column))
        logger.info(
            f"Declaring null constraints for {node.sql_condition}, {columnrefs}"
        )
        for columnref in columnrefs:
            logger.info(
                f"Processing columnref {columnref}, nullable: {columnref.args.get('nullable', False)}"
            )
            # if columnref.args.get('nullable', False):
            null_constraint = sqlglot_exp.Is(
                this=columnref,
                expression=sqlglot_exp.Null(
                    _type=columnref.type, datatype=columnref.datatype
                ),
            )
            logger.info(f"Declaring null constraint: {null_constraint}")
            logger.info(f"Declaring null constraint: {null_constraint}")
            constraints.append(null_constraint)
        self.add.declare_constraint(bit, constraints)
        return True

    def declare_join_true(self, bit, node: "Constraint", context: Dict) -> bool:
        sql_condition = node.sql_condition
        self.add.declare_constraint(bit, sql_condition)
        return True

    def declare_join_left(self, bit, node: "Constraint", context: Dict) -> bool:
        sql_condition = node.sql_condition
        left_column, right_column = sql_condition.this, sql_condition.expression
        self.add.declare_constraint(bit, left_column)
        return True

    def declare_join_right(self, bit, node: "Constraint", context: Dict) -> bool:
        sql_condition = node.sql_condition
        left_column, right_column = sql_condition.this, sql_condition.expression
        self.add.declare_constraint(bit, right_column)
        return True

    def declare_group_count(self, bit, node: "Constraint", context: Dict) -> bool:
        sql_condition = node.sql_condition
        values = set()
        dtype = None
        for group in node.coverage[PBit.GROUP_SIZE]:
            group_keys = group.group_key
            if group_keys and any(key.concrete is None for key in group_keys):
                continue
            for row in group.group_values:
                v = row.get(node.sql_condition.table, node.sql_condition.name)
                # [node.sql_condition.name]
                values.add(v.concrete)
                dtype = v.datatype
                break

        constraints = []
        for v in values:
            literal = to_literal(v, dtype)
            constraints.append(sqlglot_exp.NEQ(this=sql_condition, expression=literal))
        logger.info(
            f"Declaring group count constraint for {node.sql_condition}, values: {values}, constraints: {constraints}"
        )
        if constraints:
            self.add.declare_constraint(
                bit,
                reduce(lambda x, y: sqlglot_exp.And(this=x, expression=y), constraints),
            )

        return True

    def declare_group_size(self, bit, node: "Constraint", context: Dict) -> bool:

        agg_group = context.get("agg_group", None)
        if agg_group is None:
            agg_group = self._select_group(node)
            context["agg_group"] = agg_group

        if not agg_group:

            null_constraint = sqlglot_exp.Is(
                this=node.sql_condition,
                expression=sqlglot_exp.Null(
                    _type=node.sql_condition.type, datatype=node.sql_condition.datatype
                ),
            )
            logger.info(
                f"No suitable group found for {node.sql_condition}, declaring non-null constraint: {null_constraint}"
            )
            self.add.declare_constraint(bit, null_constraint.not_())
            return True
        v = None
        for row in agg_group.group_values:
            v = row.get(node.sql_condition.table, node.sql_condition.name)
            break
        logger.info(
            f"Declaring group size constraint for {node.sql_condition}, group picked value: {repr(v)}"
        )
        if v is not None:
            literal = to_literal(v.concrete, node.sql_condition.datatype)
            constraint = sqlglot_exp.EQ(this=node.sql_condition, expression=literal)
            self.add.declare_constraint(bit, constraint)
            return True
        return True

    def _select_group(self, node: "Constraint"):
        positive_bit = (
            PBit.AGGREGATE_SIZE
            if PBit.AGGREGATE_SIZE in node.coverage
            else PBit.GROUP_SIZE
        )
        for group in node.coverage[positive_bit]:
            group_keys = group.group_key
            if group_keys and any(key.concrete is None for key in group_keys):
                continue
            return group
        return None

    def declare_aggregate_size(self, bit, node: "Constraint", context: Dict) -> bool:
        agg_group = context.get("agg_group", None)
        if agg_group is None:
            agg_group = self._select_group(node)
            context["agg_group"] = agg_group
        return True

    def declare_group_null(self, bit, node: "Constraint", context: Dict) -> bool:
        agg_group = context.get("agg_group", None)
        if agg_group is None:
            agg_group = self._select_group(node)
            context["agg_group"] = agg_group

        if agg_group is None or len(agg_group.group_values) < 1:
            return True

        sql_condition = node.sql_condition
        columnrefs = sql_condition.find_all(sqlglot_exp.Column)

        constraints = []

        for columnref in columnrefs:
            null_constraint = sqlglot_exp.Is(
                this=columnref,
                expression=sqlglot_exp.Null(
                    _type=columnref.type, datatype=columnref.datatype
                ),
            )
            constraints.append(null_constraint)
        logger.info(
            f"Declaring group null constraint for {node.sql_condition}, constraints: {constraints}"
        )
        if constraints:
            self.add.declare_constraint(
                bit,
                reduce(lambda x, y: sqlglot_exp.Or(this=x, expression=y), constraints),
            )
        return True

    def declare_group_duplicate(self, bit, node: "Constraint", context: Dict) -> bool:

        agg_group = context.get("agg_group", None)
        if agg_group is None:
            agg_group = self._select_group(node)
            context["agg_group"] = agg_group

        if agg_group is None or len(agg_group.group_values) < 1:
            return True

        sql_condition = node.sql_condition
        columnrefs = sql_condition.find_all(sqlglot_exp.Column)

        variables = {}
        for row in agg_group.group_values:
            for columnref in columnrefs:
                v = row[columnref.name]
                if v.concrete is not None:
                    variables.setdefault(columnref, []).append(v)

        constraints = []
        for columnref in columnrefs:
            if columnref.args.get("is_unique", False):
                continue
            column_constraints = []
            for v in variables.get(columnref.name, []):
                literal = to_literal(v.concrete, columnref.datatype)
                constraint = sqlglot_exp.EQ(this=columnref, expression=literal)
                column_constraints.append(constraint)
            constraints.append(
                reduce(
                    lambda x, y: sqlglot_exp.Or(this=x, expression=y),
                    column_constraints,
                )
            )
        if constraints:
            self.add.declare_constraint(
                bit,
                reduce(lambda x, y: sqlglot_exp.Or(this=x, expression=y), constraints),
            )
        return True

    def declare_having(self, bit, node: "Constraint", context: Dict) -> bool:
        sql_condition = node.sql_condition
        agg_group = context.get("agg_group", None)
        if agg_group is None:
            agg_group = self._select_group(node)
            context["agg_group"] = agg_group
        if agg_group is None or len(agg_group.group_values) < 3:
            return True

        agg_funcs = list(sql_condition.find_all(sqlglot_exp.AggFunc))
        has_count = any(
            isinstance(agg_func, sqlglot_exp.Count) for agg_func in agg_funcs
        )
        group_size = len(agg_group.group_values)

        constraints = {
            "count": [],
            "max": [],
            "min": [],
            "sum": [],
            "avg": [],
            "nullable": [],
        }

        concretes = {}

        for agg_func in agg_funcs:
            for column in agg_func.find_all(sqlglot_exp.Column):
                if column.name in concretes:
                    continue
                for row in agg_group.group_values:
                    v = row[column.name]
                    concretes.setdefault(column, []).append(v)

        texprs = {}

        for agg_func in agg_funcs:
            distinct = list(agg_func.find(sqlglot_exp.Distinct)) > 0
            columns = agg_func.find_all(sqlglot_exp.Column)
            if isinstance(agg_func, sqlglot_exp.Count):
                if not columns:
                    texprs[agg_func] = to_literal(
                        group_size + 1, sqlglot_exp.DataType.build("INT")
                    )
                    continue
                for column in columns:
                    distincted_values = set()
                    distincted_constraints = []
                    for v in concretes.get(column, []):
                        if v.concrete is not None:
                            col = column.copy()
                            distincted_constraints.append(
                                col.eq_(to_literal(v.concrete, column.datatype))
                            )
                            distincted_values.add(v.concrete)
                    null_constraint = sqlglot_exp.Is(
                        this=column,
                        expression=sqlglot_exp.Null(
                            _type=column.type, datatype=column.datatype
                        ),
                    )
                    cnt = ITE(
                        this=null_constraint,
                        true_branch=to_literal(1, sqlglot_exp.DataType.build("INT")),
                        false_branch=to_literal(0, sqlglot_exp.DataType.build("INT")),
                    )

                    if distinct:
                        texprs[agg_func] = ITE(
                            this=reduce(
                                lambda x, y: sqlglot_exp.Or(this=x, expression=y),
                                distincted_constraints,
                            ),
                            true_branch=to_literal(
                                len(distincted_values) + cnt,
                                sqlglot_exp.DataType.build("INT"),
                            ),
                            false_branch=to_literal(
                                concretes.get(column, []) + cnt,
                                sqlglot_exp.DataType.build("INT"),
                            ),
                        )
                    else:
                        texprs[agg_func] = to_literal(
                            len(concretes.get(column, [])) + cnt,
                            sqlglot_exp.DataType.build("INT"),
                        )

            elif isinstance(agg_func, sqlglot_exp.Sum):
                for column in columns:
                    null_constraint = sqlglot_exp.Is(
                        this=column,
                        expression=sqlglot_exp.Null(
                            _type=column.type, datatype=column.datatype
                        ),
                    )
                    tval = ITE(
                        this=null_constraint,
                        true_branch=to_literal(0, column.datatype),
                        false_branch=column,
                    )
                    existing_values = [
                        v.concrete
                        for v in concretes.get(column, [])
                        if v.concrete is not None
                    ]
                    if distinct:
                        total = sum(set(existing_values))
                    else:
                        total = sum(existing_values)
                    texprs[agg_func] = to_literal(total, column.datatype) + tval

        sql_condition = sql_condition.copy()

        tconstraint = sql_condition.transform(
            lambda node: texprs[node] if node in texprs else node
        )

        logger.info(
            f"Declaring having constraint for {node.sql_condition}, transformed constraint: {tconstraint}"
        )

        self.add.declare_constraint(bit, tconstraint)
        return True
