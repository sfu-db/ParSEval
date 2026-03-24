from __future__ import annotations
from abc import abstractmethod
from collections.abc import Iterable
from functools import reduce
from datetime import date, datetime
from sqlglot import exp
from sqlglot import generator, MappingSchema
from sqlglot.executor.env import ENV
from parseval.dtype import DataType
from parseval.helper import normalize_name
from typing import TYPE_CHECKING, List, Optional, Dict, Any, Tuple, Type
from sqlglot.optimizer.simplify import simplify
from dateutil import parser as date_parser


def _coerce_temporal_pair(left, right):
    if left is None or right is None:
        return left, right
    left_is_temporal = isinstance(left, (date, datetime))
    right_is_temporal = isinstance(right, (date, datetime))
    if left_is_temporal and isinstance(right, str):
        try:
            return left, date_parser.parse(right)
        except Exception:
            return left, right
    if right_is_temporal and isinstance(left, str):
        try:
            return date_parser.parse(left), right
        except Exception:
            return left, right
    return left, right


def _coerce_numeric_pair(left, right):
    def coerce_str_number(value, other):
        if not isinstance(value, str) or isinstance(other, bool):
            return value
        text = value.strip()
        try:
            if isinstance(other, int) and text.isdigit():
                return int(text)
            if isinstance(other, int):
                digits = "".join(ch for ch in text if ch.isdigit())
                if digits:
                    return int(digits[:4] if len(digits) >= 4 else digits)
            if isinstance(other, float):
                return float(text)
        except Exception:
            return value
        return value

    left = coerce_str_number(left, right)
    right = coerce_str_number(right, left)
    return left, right


def _coerce_comparable_pair(left, right):
    left, right = _coerce_temporal_pair(left, right)
    left, right = _coerce_numeric_pair(left, right)
    return left, right


def _null_if_any_compare(func):
    def _wrapped(left, right):
        if left is None or right is None:
            return None
        left, right = _coerce_comparable_pair(left, right)
        return func(left, right)

    return _wrapped


def _ensure_iterable(values):
    if isinstance(values, (str, bytes)):
        return [values]
    if isinstance(values, Iterable):
        return list(values)
    return [values]


def _aggregate_values(values):
    return [value for value in _ensure_iterable(values) if value is not None]


def _agg_sum(values):
    items = _aggregate_values(values)
    return sum(items) if items else None


def _agg_avg(values):
    items = _aggregate_values(values)
    return (sum(items) / len(items)) if items else None


def _agg_min(values):
    items = _aggregate_values(values)
    return min(items) if items else None


def _agg_max(values):
    items = _aggregate_values(values)
    return max(items) if items else None


def _agg_count(values):
    return len(_aggregate_values(values))


def _case_op(*args):
    if not args:
        return None
    if len(args) == 1:
        return args[0]
    if len(args) == 2:
        cond, true_value = args
        return true_value if cond else None
    cond, true_value, false_value = args[:3]
    return true_value if cond else false_value


def _safe_div(left, right):
    if left is None or right in (None, 0):
        return None
    return left / right


def _nullif(left, right):
    if left == right:
        return None
    return left


def _between(value, low, high):
    if value is None or low is None or high is None:
        return None
    value, low = _coerce_comparable_pair(value, low)
    value, high = _coerce_comparable_pair(value, high)
    try:
        return low <= value and value <= high
    except TypeError:
        return False


OPS = {
    **ENV,
    "VARIABLE": lambda x: x.concrete,
    "CONST": lambda x: x.args.get("concrete"),
    "AND": lambda x, y: x and y,
    "OR": lambda x, y: x or y,
    "NOT": lambda x: not x,
    "NULL": lambda: None,
    "IS": lambda x, y: x is y,
    "GT": _null_if_any_compare(lambda x, y: x > y),
    "GTE": _null_if_any_compare(lambda x, y: x >= y),
    "LT": _null_if_any_compare(lambda x, y: x < y),
    "LTE": _null_if_any_compare(lambda x, y: x <= y),
    "SUM": _agg_sum,
    "AVG": _agg_avg,
    "MIN": _agg_min,
    "MAX": _agg_max,
    "COUNT": _agg_count,
    "CASE": _case_op,
    "IF": _case_op,
    "CAST": lambda value: value,
    "DIV": _safe_div,
    "NULLIF": _nullif,
    "BETWEEN": _between,
}


def ref(self) -> int:
    return self.args.get("ref", 0)


def datatype(self) -> DataType:
    if self.type is not None:
        return self.type
    dtype = self.args.get("_type")
    return DataType.build(dtype)


def concrete(self) -> Any:
    if isinstance(self, exp.Column):
        return self.args.get("concrete")
    if isinstance(self, exp.Literal):
        from .helper import to_const

        return to_const(self)
    concretes = [
        a.concrete for a in self.iter_expressions() if not isinstance(a, exp.DataType)
    ]
    if self.key.upper() not in OPS:
        return None
    return OPS[self.key.upper()](*concretes)


def __bool__(self) -> bool:
    if self.concrete is None:
        return False
    return bool(self.concrete)


setattr(exp.Column, "ref", property(ref))
setattr(exp.Expression, "datatype", property(datatype))
setattr(exp.Expression, "concrete", property(concrete))

ColumnRef = exp.Column


class Symbol(exp.Expression):

    arg_types = {"this": True, "concrete": True}

    def is_number(self):
        return self.type.is_type(DataType.NUMERIC_TYPES)

    def is_datetime(self):
        return self.type.is_type(*DataType.TEMPORAL_TYPES)

    def sql(self, dialect=None, **opts):
        return f"{self.key}({self.this})"


class Variable(Symbol):
    arg_types = {"this": True, "concrete": False}
    """
        Represents a symbolic variable with additional attributes.
    """

    @property
    def name(self) -> str:
        return self.text("this")

    @property
    def concrete(self) -> Any:
        return self.args.get("concrete", None)


class Const(Symbol):
    arg_types = {"this": True}

    @property
    def value(self) -> Any:
        return self.concrete

    @property
    def concrete(self):
        return self.this


class ITE(Symbol):
    arg_types = {"this": True, "true_branch": True, "false_branch": True}

    @property
    def condition(self) -> Symbol:
        return self.this

    @property
    def true_branch(self) -> Symbol:
        return self.args.get("true_branch")

    @property
    def false_branch(self) -> Symbol:
        return self.args.get("false_branch")


class Row(Symbol):
    arg_types = {"this": True, "columns": True}
    """
    rowid, {column_name: value, ...}
        rowid: Tuple
        column_name: str
        value: Symbol
    """

    @property
    def columns(self):
        return tuple(self.args.get("columns", {}).keys())

    @property
    def rowid(self) -> Tuple[Any, ...]:
        if isinstance(self.this, tuple):
            return self.this
        return (self.this,)

    def _key_name(self, key):
        if isinstance(key, exp.Expression):
            if isinstance(key, exp.Column):
                return key.name
            return key.alias_or_name or key.sql()
        return str(key)

    def items(self):
        return self.args.get("columns", {}).items()

    def __iter__(self):
        return iter(self.args.get("columns"))

    def __getitem__(self, key):
        columns = self.args.get("columns", {})
        if key in columns:
            return columns[key]
        if hasattr(key, "name"):
            key = key.name
            if key in columns:
                return columns[key]
        normalized = normalize_name(self._key_name(key))
        for column_name, value in columns.items():
            if normalize_name(self._key_name(column_name)) == normalized:
                return value
        raise KeyError(key)

    def get(self, table, column):
        del table
        return self[column]

    def __len__(self):
        return len(self.args.get("columns", {}))

    def __add__(self, other):
        assert isinstance(other, Row), f"Cannot add Row with {type(other)}"
        new_columns = {**self.args.get("columns"), **other.args.get("columns", {})}
        rid = self.rowid + other.rowid
        return Row(this=rid, columns=new_columns)


class AggGroup(Symbol):
    """
    this: rowids of the group,
    group_key: group by key values,
    group_values: list of group by key values
    """

    arg_types = {"this": True, "group_key": True, "group_values": True}

    @property
    def group_key(self):
        return self.args.get("group_key", ())

    @property
    def group_values(self):
        return self.args.get("group_values", [])

    @property
    def name(self) -> Any:
        return self.text("this")

    @property
    def rowids(self) -> Tuple[Any, ...]:
        return self.this

    # def extend(self, items: Iterable[Symbol]):
    #     object.__setattr__(self, "args", self.args + tuple(items))

    # def append(self, item: Symbol):
    #     object.__setattr__(self, "args", self.args + (item,))

    # def __iter__(self):
    #     return iter(self.args[2:])

    # def __getitem__(self, index):
    #     return self.args[2:][index]


from sqlglot import generator

for klass in [Symbol, Variable, Const, ITE, Row, AggGroup]:
    generator.Generator.TRANSFORMS[klass] = lambda self, expression: expression.sql(
        dialect=self.dialect
    )


class Is_Null(exp.Unary, exp.Predicate):

    def sql(self, dialect=None, **opts):
        return f"{self.this.sql(dialect=dialect, **opts)} IS NULL"


class Is_Not_Null(exp.Unary, exp.Predicate):
    def sql(self, dialect=None, **opts):
        return f"{self.this.sql(dialect=dialect, **opts)} IS NOT NULL"


class FunctionCall(exp.Func):
    arg_types = {"this": True, "expressions": False}

    def params(self) -> List[exp.Expression]:
        return self.expressions

    def sql(self, dialect=None, **opts):
        args_sql = ", ".join(
            [expr.sql(dialect=dialect, **opts) for expr in self.expressions]
        )
        return f"{self.this}({args_sql})"


class Strftime(FunctionCall):
    arg_types = {"this": True, "expressions": True, "datatype": True}

    @property
    def fmt(self) -> exp.Expression:
        return self.expressions[1]

    @property
    def operand(self) -> exp.Expression:
        return self.this


class ABS(FunctionCall):
    arg_types = {"this": True, "datatype": True}

    @property
    def operand(self) -> exp.Expression:
        return self.this


class ScalarQuery(exp.Expression):
    arg_types = {
        "this": True,
        "datatype": False,
        "correlated": False,
    }

    @property
    def query(self) -> "exp.Expression":
        return self.this

    @property
    def selects(self) -> List["exp.Expression"]:
        return (
            self.query.schema.columns if isinstance(self.query, LogicalOperator) else []
        )

    def sql(self, dialect=None, **opts):
        if self.query:
            return f"{self.key}( {self.query.sql(dialect=dialect, **opts)})"
        if self.args.get("expressions"):
            return f"{self.key}( {', '.join([expr.sql(dialect=dialect, **opts) for expr in self.expressions])})"

        # return f"{self.key}( {self.query.sql(dialect=dialect, **opts)})"


class FieldAccess(exp.Expression):
    arg_types = {
        "this": True,
        "column": True,
        "datatype": False,
        "correlated": False,
    }

    @property
    def name(self) -> str:
        return self.text("this")

    @property
    def column(self) -> int:
        return self.args.get("column", 0)

    def sql(self, dialect=None, **opts):
        return f"{self.key}({self.this}, column={self.column})"


# class DerivedSchema(exp.Expression):
#     arg_types = {"this": False, "expressions": True}
#     @property
#     def columns(self) -> List[exp.Expression]:
#         return self.expressions

#     def column_names(self) -> List[str]:
#         """Get list of column names"""
#         return [col.name for col in self.columns]


class Schema(exp.Expression):
    arg_types = {"this": False, "expressions": True}

    @property
    def columns(self) -> List[exp.Expression]:
        return self.expressions

    def column_names(self) -> List[str]:
        """Get list of column names"""
        return [col.name for col in self.columns]


class Table(exp.Expression):
    arg_types = {
        "this": True,
        "schema": False,
        "constraints": False,
        "primary_key": False,
        "foreign_key": False,
    }

    @property
    def schema(self) -> Schema:
        return self.args.get("schema")

    @property
    def constraints(self):
        return self.args.get("constraints", {})

    @property
    def columns(self) -> List[ColumnRef]:
        if "_columns" in self.args:
            return self.args.get("_columns")

        columns = []
        for column in self.schema.columns:
            nullable = self.nullable(column.name)
            unique = self.is_unique(column.name)
            column.set("table", self.name)
            column.set("unique", unique)
            column.set("nullable", nullable)
            columns.append(column)
        self.set("_columns", columns)
        return columns

    def nullable(self, column_name: str):
        primary_key = self.args.get("primary_key", None)
        if primary_key:
            for column_name in primary_key.find_all(exp.Identifier):
                if str(column_name) == column_name:
                    return False
        for constraint in self.constraints.get(column_name, []):

            if isinstance(constraint.kind, exp.NotNullColumnConstraint):
                return constraint.kind.args.get("allow_null", False)
        return True

    def is_unique(self, column_name):
        for constraint in self.constraints.get(column_name, []):
            if isinstance(
                constraint.kind,
                (
                    exp.UniqueColumnConstraint,
                    exp.PrimaryKeyColumnConstraint,
                ),
            ):
                return True
        primary_key = self.args.get("primary_key", None)
        if primary_key:
            for column_name in primary_key.find_all(exp.Identifier):
                if str(column_name) == column_name:
                    return True
        return False


class Catalog(exp.Expression):
    arg_types = {"tables": False}

    @property
    def tables(self) -> Dict[str, Table]:
        return self.args.get("tables", {})

    def add_table(self, table_info: Table):
        """Register a table in the catalog"""
        self.tables[table_info.name] = table_info

    def get_table(self, name: str) -> Optional[Table]:
        """Get table information by name"""
        return self.tables.get(name)


from sqlglot.schema import (
    AbstractMappingSchema,
    MappingSchema,
    flatten_schema,
    dict_depth,
    nested_get,
    nested_set,
    SchemaError,
)
from collections import OrderedDict

# class Catalog2(AbstractMappingSchema, Schema):
#     def __init__(self, schema = None, constraints = None, primary_keys = None, foreign_keys = None, visible = None, dialect = None, normalize = True):
#         self.dialect = dialect
#         self.visible = {} if visible is None else visible
#         self.normalize = normalize
#         self._type_mapping_cache: Dict[str, DataType] = {}
#         self._depth = 0
#         self.constraints = {}
#         self.primary_keys = {}
#         self.foreign_keys = {}
#         schema = OrderedDict() if schema is None else schema
#         super().__init__(schema if self.normalize else schema)


class Catalog2(MappingSchema):
    def __init__(
        self,
        schema=None,
        constraints=None,
        primary_keys=None,
        foreign_keys=None,
        visible=None,
        dialect=None,
        normalize=True,
    ):
        self.constraints = {}
        self.primary_keys = {}
        self.foreign_keys = {}
        schema = OrderedDict() if schema is None else schema
        super().__init__(schema, visible, dialect, normalize)
        constraints = {} if constraints is None else constraints
        primary_keys = {} if primary_keys is None else primary_keys
        foreign_keys = {} if foreign_keys is None else foreign_keys

        for table_name, table_constraints in constraints.items():
            for column_name, column_constraints in table_constraints.items():
                for constraint in column_constraints:
                    self.add_constraint(table_name, column_name, constraint)
        for table_name, pks in primary_keys.items():
            self.add_primary_key(table_name, pks)
        for table_name, fks in foreign_keys.items():
            self.add_foreign_key(table_name, fks)

    def _normalize(self, schema):
        normalized_mapping: Dict = OrderedDict()
        flattened_schema = flatten_schema(schema, depth=dict_depth(schema) - 1)
        for keys in flattened_schema:
            columns = nested_get(schema, *zip(keys, keys))
            if not isinstance(columns, dict):
                raise SchemaError(
                    f"Table {'.'.join(keys[:-1])} must match the schema's nesting level: {len(flattened_schema[0])}."
                )
            normalized_keys = [
                self._normalize_name(
                    key, is_table=True, dialect=self.dialect, normalize=self.normalize
                )
                for key in keys
            ]
            for column_name, column_type in columns.items():
                nested_set(
                    normalized_mapping,
                    normalized_keys
                    + [
                        self._normalize_name(
                            column_name, dialect=self.dialect, normalize=self.normalize
                        )
                    ],
                    column_type,
                )
        return normalized_mapping

    @property
    def tables(self):
        return self.mapping

    def add_primary_key(
        self, table: exp.Table | str, columns: List[exp.Identifier] | exp.Identifier
    ):
        table = self._normalize_name(
            table if isinstance(table, str) else table.this,
            self.dialect,
            self.normalize,
        )
        pk_set = self.primary_keys.setdefault(table, set())
        columns = [columns] if isinstance(columns, exp.Identifier) else columns
        pk_set.update(columns)

    def get_primary_key(self, table: exp.Table | str):
        table = self._normalize_name(
            table if isinstance(table, str) else table.this,
            self.dialect,
            self.normalize,
        )
        return self.primary_keys.get(table, set())

    def add_foreign_key(
        self, table: exp.Table | str, foreign_key: List[exp.ForeignKey] | exp.ForeignKey
    ):
        table = self._normalize_name(
            table if isinstance(table, str) else table.this,
            self.dialect,
            self.normalize,
        )
        fk_list = self.foreign_keys.setdefault(table, [])
        fks = [foreign_key] if isinstance(foreign_key, exp.ForeignKey) else foreign_key
        fk_list.extend(fks)

    def get_foreign_key(self, table: exp.Table | str):
        table = self._normalize_name(
            table if isinstance(table, str) else table.this,
            self.dialect,
            self.normalize,
        )
        return self.foreign_keys.get(table, [])

    def add_constraint(
        self, table: exp.Table | str, column: exp.Column | str, constraint
    ):
        table = self._normalize_name(
            table if isinstance(table, str) else table.this,
            self.dialect,
            self.normalize,
        )
        column = self._normalize_name(
            column if isinstance(column, str) else column.this, normalize=self.normalize
        )
        table_constraints = self.constraints.setdefault(table, {})
        column_constraints = table_constraints.setdefault(column, set())
        column_constraints.add(constraint)

    def get_column_constraints(self, table: exp.Table | str, column: exp.Column | str):
        table = self._normalize_name(
            table if isinstance(table, str) else table.this,
            self.dialect,
            self.normalize,
        )
        column = self._normalize_name(
            column if isinstance(column, str) else column.this, normalize=self.normalize
        )
        table_constraints = self.constraints.get(table, {})
        column_constraints = table_constraints.get(column, set())
        return column_constraints

    def nullable(
        self,
        table: exp.Table | str,
        column: exp.Column | str,
        normalize: Optional[bool] = None,
    ):
        for constraint in self.get_column_constraints(table, column):
            if isinstance(constraint.kind, exp.NotNullColumnConstraint):
                return constraint.kind.args.get("allow_null", False)
        for pk in self.get_primary_key(table):
            if pk.name == (column if isinstance(column, str) else column.this):
                return False
        return True

    def is_unique(
        self,
        table: exp.Table | str,
        column: exp.Column | str,
        normalize: Optional[bool] = None,
    ):
        for constraint in self.get_column_constraints(table, column):
            if isinstance(
                constraint.kind,
                (
                    exp.UniqueColumnConstraint,
                    exp.PrimaryKeyColumnConstraint,
                ),
            ):
                return True
        for pk in self.get_primary_key(table):
            if pk.name == (column if isinstance(column, str) else column.this):
                return True

        return False


class LogicalOperator(exp.Expression):
    """
    Represents a single step in a REX (Relational EXpression) plan.
    """

    @property
    def operator_id(self) -> str:
        return self.args.get("operator_id", "")

    @property
    def operator_type(self) -> str:
        return self.key[7:].capitalize()

    @property
    def children(self) -> List[LogicalOperator]:
        return []

    @abstractmethod
    def schema(self):
        """
        Returns the schema of the output produced by this operator.
        """
        pass

    def sql(self, dialect=None, **opts):
        indent = opts.get("indent", 0)
        pad = "  " * indent
        lines = [f"{pad}{self._sql(dialect=dialect, **opts)}"]
        opts.setdefault("skips", set()).add(self.operator_id)
        for child in self.children:
            if child.operator_id in opts["skips"]:
                continue
            opts["indent"] = indent + 1
            lines.append(child.sql(dialect=dialect, **opts))
        return "\n".join(lines)


class LeafOperator(LogicalOperator):
    """Base class for operators with no children (leaf nodes)"""


class UnaryOperator(LogicalOperator):
    """Base class for operators with exactly one child"""

    arg_types = {"this": True}

    @property
    def children(self) -> List[LogicalOperator]:
        return [self.this]

    def schema(self):
        if "_schema" in self.args:
            return self.args.get("_schema")
        scm = self.this.schema()
        self.set("_schema", scm)
        return scm


class BinaryOperator(LogicalOperator):
    """Base class for operators with exactly two children"""

    @property
    def left(self):
        return self.this

    @property
    def right(self):
        return self.expression

    @property
    def children(self) -> List[LogicalOperator]:
        return [self.left, self.right]


class LogicalScan(LeafOperator):
    """
    Represents a logical scan operation in a REX plan.
    """

    arg_types = {
        "this": True,
        "operator_id": True,
        "expressions": False,
    }

    @property
    def table_name(self) -> str:
        return self.text("this")

    @property
    def columns(self):
        return self.expressions

    def _sql(self, dialect=None, **opts):
        return f"{self.operator_type}(table={self.table_name}, id = {self.operator_id})"

    def __repr__(self):
        return f"{self.operator_type}(table={self.table_name}, id = {self.operator_id})"

    def schema(self):
        if "_schema" in self.args:
            return self.args.get("_schema")
        scm = Schema(expressions=self.expressions)
        self.set("_schema", scm)
        return scm


class LogicalProject(UnaryOperator):
    """
    Represents a logical projection operation in a REX plan.
    """

    arg_types = {
        "this": True,
        "expressions": True,
        "operator_id": True,
    }

    def _sql(self, dialect=None, **opts):
        exprs = ", ".join(expr.sql(dialect) for expr in self.expressions)
        return f"{self.operator_type}({exprs}, id={self.operator_id})"

    def __repr__(self) -> str:
        exprs = ", ".join(f"{expr}" for expr in self.expressions)
        return f"{self.operator_type}({exprs}, id={self.operator_id})"

    def schema(self):
        if "_schema" in self.args:
            return self.args.get("_schema")
        input_scm = self.this.schema()
        columns = []
        for expr in self.expressions:
            new_expr = expr.transform(resolve_schema, input_schema=input_scm)
            columns.append(new_expr)
        scm = Schema(expressions=columns)
        self.set("_schema", scm)
        return self.args.get("_schema")


class LogicalFilter(UnaryOperator):
    """
    Represents a logical filter operation in a REX plan.
    """

    arg_types = {
        "this": True,
        "condition": True,
        "operator_id": True,
    }

    @property
    def children(self) -> List[LogicalOperator]:
        children = [self.this]
        return children

    @property
    def condition(self) -> exp.Expression:
        return self.args.get("condition")

    def _sql(self, dialect=None, **opts):
        # for child in self.children[1:]:
        #     opts.setdefault("skips", set()).add(child.operator_id)
        return f"{self.operator_type}(condition={self.condition}, id={self.operator_id }, variableset={self.args.get('variableset')})"

    def __repr__(self) -> str:
        return (
            f"{self.operator_type}(condition={self.condition}, id={self.operator_id })"
        )


class LogicalHaving(LogicalFilter):
    pass


class LogicalSort(UnaryOperator):
    arg_types = {
        "this": True,
        "expressions": True,
        "dirs": True,
        "offset": True,
        "limit": True,
        "operator_id": True,
    }

    @property
    def sorts(self) -> List[exp.Expression]:
        return self.expressions

    @property
    def offset(self) -> int:
        return self.args.get("offset", 0)

    @property
    def limit(self) -> Optional[int]:
        return self.args.get("limit", None)

    @property
    def dirs(self) -> List:
        return self.args.get("dirs", [])

    def _sql(self, dialect=None, **opts):
        return f"{self.operator_type}({', '.join([s.sql(dialect, **opts) for s in self.sorts])}, dir={self.dirs}, offset={self.offset}, limit={self.limit})"

    def __repr__(self):
        return f"{self.operator_type}({', '.join([str(s) for s in self.sorts])}, dir={self.dirs}, offset={self.offset}, limit={self.limit})"


class LogicalAggregate(UnaryOperator):
    arg_types = {
        "this": True,
        "expressions": True,
        "aggs": True,
        "operator_id": True,
    }

    @property
    def keys(self) -> List[exp.Expression]:
        return self.expressions

    @property
    def aggs(self) -> List[exp.Expression]:
        return self.args.get("aggs", [])

    def _sql(self, dialect=None, **opts):
        keys_sql = ", ".join([k.sql(dialect) for k in self.keys])
        aggs_sql = ", ".join([a.sql(dialect) for a in self.aggs])
        return f"{self.operator_type}(keys=[{keys_sql}], aggs=[{aggs_sql}])"

    def __repr__(self):
        keys = ", ".join([str(k) for k in self.keys])
        agg_funcs = ", ".join([str(a) for a in self.aggs])
        return f"{self.operator_type}(keys=[{keys}], aggs=[{agg_funcs}]"

    def schema(self):
        if "_schema" in self.args:
            return self.args.get("_schema")
        input_schema = self.this.schema()
        columns = []
        for key in self.keys:
            colref = input_schema.columns[key.ref].copy()
            colref.set("unique", True)
            columns.append(colref)
        for agg_expr in self.aggs:
            agg = agg_expr.transform(resolve_schema, input_schema=input_schema)
            columns.append(agg)
        scm = Schema(expressions=columns)
        self.set("_schema", scm)
        return scm


class LogicalJoin(BinaryOperator):
    arg_types = {
        "this": True,
        "expression": True,
        "join_type": True,
        "condition": True,
        "operator_id": True,
    }

    @property
    def join_type(self) -> str:
        return self.args.get("join_type")

    @property
    def condition(self) -> Optional[Expression]:
        return self.args.get("condition", None)

    def _sql(self, dialect=None, **opts):
        return f"{self.operator_type}(condition={self.condition}, type={self.join_type}, id={self.operator_id })"

    def __repr__(self) -> str:
        return f"{self.operator_type}(condition= {self.condition}, type={self.join_type}, id={self.operator_id})"

    def schema(self):
        if "_schema" in self.args:
            return self.args.get("_schema")
        scm = Schema(
            expressions=[column for column in self.left.schema().columns]
            + [column for column in self.right.schema().columns]
        )
        self.set("_schema", scm)
        return self.args.get("_schema")


class LogicalUnion(BinaryOperator):
    arg_types = {
        "this": True,
        "expression": True,
        "union_all": True,
        "operator_id": True,
    }

    @property
    def union_all(self) -> bool:
        return self.args.get("union_all", False)

    def _sql(self, dialect=None, **opts):
        return f"{self.operator_type}(all={self.union_all})"

    def __repr__(self) -> str:
        return f"{self.operator_type}(all={self.all})"

    def schema(self):
        return self.left.schema()


class LogicalIntersect(LogicalUnion):
    pass


class LogicalDifference(LogicalUnion):
    pass


class LogicalCorrelate(UnaryOperator):
    arg_types = {
        "this": True,
        "expressions": False,
        "query": True,
        "correlated": False,
        "operator_id": True,
    }

    def query(self) -> LogicalOperator:
        return self.this

    def sql(self, dialect=None, **opts):
        indent = opts.get("indent", 0)
        pad = "  " * indent
        lines = [f"{pad}{repr(self)}"]
        # for child in self.children:
        #     opts["indent"] = indent + 1
        #     lines.append(child.sql(dialect=dialect, **opts))
        return "\n".join(lines)

    def __repr__(self) -> str:
        return f"{self.operator_type}({self.this}, id={self.operator_id})"

    def schema(self):
        return self.this.schema()


for klass in [
    # ColumnRef,
    Is_Null,
    Is_Not_Null,
    LogicalOperator,
    LogicalAggregate,
    LogicalJoin,
    LogicalFilter,
    LogicalProject,
    LogicalScan,
    LogicalSort,
    LogicalCorrelate,
    ScalarQuery,
    FieldAccess,
]:
    generator.Generator.TRANSFORMS[klass] = lambda self, expression: expression.sql(
        dialect=self.dialect
    )


def resolve_schema(expr, input_schema: Schema):
    """
    Resolve a ColumnRef expression to its corresponding schema entry.

    Args:
        expr (sql_exp.Expression): The expression to check.
        input_schema: The schema of children used to retrieve schema info.

    Returns:
        The resolved schema column if applicable, otherwise None.
    """
    if isinstance(expr, ColumnRef):
        return input_schema.columns[expr.ref]
    if isinstance(expr, ScalarQuery):
        sub_schema = expr.this.schema()
        return sub_schema.columns[0]
    return expr


def negate_predicate(expr) -> "exp.Expression":
    """Return the negation of this expression."""

    if expr.key == "is_null":
        return Is_Not_Null(this=expr.this)
    elif expr.key == "is_not_null":
        return Is_Null(this=expr.this)

    return simplify(expr.not_())
