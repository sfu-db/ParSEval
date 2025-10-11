from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Any, Optional, Dict, TYPE_CHECKING, List, Union, Tuple

if TYPE_CHECKING:
    from ..dtype import DATATYPE
from ..dtype import DataType
from abc import ABC, abstractmethod
from enum import Enum


class SubqueryType(Enum):
    """Types of subqueries encountered in SQL"""

    SCALAR = "scalar_query"  # Returns single value
    EXISTS = "exists"  # EXISTS predicate
    IN = "in"  # IN predicate
    ANY = "any"  # ANY/SOME predicate
    ALL = "all"  # ALL predicate
    FROM = "from"  # Subquery in FROM clause


class Expression(ABC):
    __slots__ = ("datatype", "metadata")

    def __init__(self, datatype: Optional[DATATYPE] = None, metadata=None):
        super().__init__()
        self.datatype = DataType.build(datatype) if datatype else None
        self.metadata = metadata or {}

    @abstractmethod
    def get_children(self) -> Tuple[Expression]:
        """
        Return the child expressions.

        Returns:
            Tuple of child Expression objects
        """
        pass

    @abstractmethod
    def infer_type(self, schema_context: Schema) -> DataType:
        """
        Infer the data type of this expression.

        Args:
            schema_context: Schema providing column type information

        Returns:
            Inferred DataType

        Raises:
            TypeInferenceError: If type cannot be inferred
        """
        pass

    def to_sql(self, dialect: str):
        pass

    def accept(self, visitor: "ExpressionVisitor"):
        return visitor.visit(self)

    def predicates(self, include_nested: bool = False) -> List[Expression]:
        """Collect all boolean predicate symbols in the expression tree.

        Args:
            include_nested (bool): If True, include nested predicates within other predicates.
                                   If False, only include top-level predicates.

        Returns:
            List[Symbol]: List of predicate symbols.
        """
        results: List[Expression] = []
        self._collect_predicates(results, include_nested, is_root=True)
        return results

    def _collect_predicates(
        self, results: List[Expression], include_nested: bool, is_root: bool = False
    ):
        if isinstance(self, Predicate):
            results.append(self)
            if not include_nested and not is_root:
                return
        for child in self.children():
            child._collect_predicates(results, include_nested, is_root=False)

    def __str__(self):
        return self.to_sql(None)

    def __eq__(self, other):
        if not isinstance(other, self.__class__):
            return False
        return (
            self.datatype == other.datatype
            and self.get_children() == other.get_children()
        )

    def __hash__(self) -> int:
        """Hash for use in sets/dicts"""
        return hash(
            (self.__class__.__name__, str(self.datatype), tuple(self.get_children()))
        )


class Literal(Expression):
    """
    Create a literal value.

    Args:
        value: The literal value
        datatype: Data type of the literal
        metadata: Additional metadata
    """

    __slots__ = ("value",)

    def __init__(self, value: Any, datatype: Optional[DATATYPE] = None, metadata=None):
        super().__init__(datatype, metadata)
        self.value = value
        if self.datatype is None:
            self.datatype = self._infer_literal_type(value)

    @staticmethod
    def _infer_literal_type(value: Any) -> DataType:
        """Infer datatype from Python value"""
        if isinstance(value, bool):
            return DataType.build("BOOLEAN")
        elif isinstance(value, int):
            return DataType.build("INTEGER")
        elif isinstance(value, float):
            return DataType.build("FLOAT")
        elif isinstance(value, str):
            return DataType.build("VARCHAR")
        elif value is None:
            return DataType.build("NULL")
        else:
            return DataType.build("VARCHAR")

    def get_children(self) -> Tuple[Expression]:
        return ()

    def infer_type(self, schema_context: Schema) -> DataType:
        return self.datatype

    def to_sql(self, dialect):
        if self.value is None:
            return "NULL"
        from sqlglot import exp

        if self.datatype.is_numeric():
            return exp.Literal.number(self.value).sql(dialect)
        else:
            return exp.Literal.string(self.value).sql(dialect)

    def __eq__(self, other: Any) -> bool:
        if not isinstance(other, Literal):
            return False
        return self.value == other.value and self.datatype == other.datatype

    def __hash__(self) -> int:
        return hash((self.__class__.__name__, self.value, str(self.datatype)))


class ColumnRef(Expression):
    __slots__ = ("name", "table_alias", "ref")

    def __init__(
        self,
        name: str,
        table_alias: Optional[str] = None,
        ref: Optional[int] = None,
        datatype=None,
        metadata=None,
    ):
        """
        Create a column reference.

        Args:
            name: Column name
            table_alias: Optional table alias
            ref: Optional column reference ID
            datatype: Column data type
            metadata: Additional metadata

        Raises:
            ValidationError: If name is empty
        """
        super().__init__(datatype, metadata)
        self.name = name
        self.table_alias = table_alias
        self.ref = ref

    @property
    def qualified_name(self) -> str:
        """Get fully qualified column name"""
        return f"{self.table_alias}.{self.name}" if self.table_alias else self.name

    def get_children(self):
        return ()

    def infer_type(self, schema_context: Schema) -> DataType:
        if self.datatype:
            return self.datatype
        if schema_context:
            col = schema_context.get_column(self.ref)
            if col and col.datatype:
                return col.datatype
        return DataType.build("UNKNOWN")

    def to_sql(self, dialect):
        return self.name

    def __repr__(self) -> str:
        return f"ColumnRef({self.qualified_name!r}, {self.datatype})"

    def __eq__(self, other: Any) -> bool:
        if not isinstance(other, ColumnRef):
            return False
        return (
            self.name == other.name
            and self.table_alias == other.table_alias
            and self.datatype == other.datatype
        )

    def __hash__(self) -> int:
        return hash(
            (self.__class__.__name__, self.name, self.table_alias, str(self.datatype))
        )


class Schema(Expression):
    __slots__ = ("columns",)

    def __init__(self, columns, datatype=None, metadata=None):
        super().__init__(datatype, metadata)
        self.columns = columns or []

    def column_names(self) -> List[str]:
        """Get list of column names"""
        return [col.name for col in self.columns]

    def get_children(self):
        return ()

    def infer_type(self, schema_context: Schema) -> DataType:
        raise ValueError("Schema does not have a single data type")

    def to_sql(self, dialect):
        return ", ".join(col.name for col in self.columns)

    def __repr__(self):
        return f"Schema({len(self.columns)} columns)"


class Star(Expression):
    __slots__ = ("table_ref",)

    def __init__(self, table_ref: Optional[Expression] = None, metadata=None):
        super().__init__(None, metadata)
        self.table_ref = table_ref

    def get_children(self):
        return (self.table_ref,) if self.table_ref else ()

    def infer_type(self, schema_context):
        raise ValueError("Star does not have a single data type")

    def to_sql(self, dialect):
        return "*"


class BinaryOp(Expression):
    __slots__ = ("left", "op", "right")

    def __init__(
        self, left: Expression, op: str, right: Expression, datatype=None, metadata=None
    ):
        """
        Create a binary operation.

        Args:
            left: Left operand
            op: Operator string
            right: Right operand
            datatype: Result data type
            metadata: Additional metadata

        Raises:
            ValidationError: If operands or operator are invalid
        """
        super().__init__(datatype, metadata)

        self.left = left
        self.op = op
        self.right = right

    def get_children(self):
        return (self.left, self.right)

    def infer_type(self, schema_context):
        if self.datatype:
            return self.datatype
        left_type = self.left.infer_type(schema_context)
        right_type = self.right.infer_type(schema_context)
        # Default: use left type (subclasses can override)
        return left_type

    def to_sql(self, dialect):
        left_sql = self.left.to_sql(dialect)
        right_sql = self.right.to_sql(dialect)
        return f"{left_sql} {self.op} {right_sql}"


class UnaryOp(Expression):
    __slots__ = ("op", "operand")

    def __init__(self, op: str, operand: Expression, datatype=None, metadata=None):
        """
        Create a unary operation.

        Args:
            op: Operator string
            operand: Operand expression
            datatype: Result data type
            metadata: Additional metadata

        Raises:
            ValidationError: If operand or operator are invalid
        """
        super().__init__(datatype, metadata)
        self.op = op
        self.operand = operand

    def get_children(self):
        return (self.operand,)

    def infer_type(self, schema_context):
        if self.datatype:
            return self.datatype
        return self.operand.infer_type(schema_context)

    def to_sql(self, dialect):
        operand_sql = self.operand.to_sql(dialect)
        return f"({self.op} {operand_sql})"


class Predicate(BinaryOp):
    def infer_type(self, schema_context):
        return DataType.build("BOOLEAN")


class AND(Predicate):
    """Logical AND"""

    def __init__(self, left: Expression, right: Expression, **kwargs):
        super().__init__(left, "AND", right, **kwargs)


class OR(Predicate):
    """Logical OR"""

    def __init__(self, left: Expression, right: Expression, **kwargs):
        super().__init__(left, "OR", right, **kwargs)


class NOT(UnaryOp):
    """Logical NOT"""

    def __init__(self, operand: Expression, **kwargs):
        super().__init__("NOT", operand, **kwargs)

    def infer_type(self, schema_context: Schema) -> DataType:
        return DataType.build("BOOLEAN")


class IsNull(UnaryOp):
    """IS NULL predicate"""

    def __init__(self, operand: Expression, **kwargs):
        super().__init__("IS NULL", operand, **kwargs)

    def infer_type(self, schema_context: Schema) -> DataType:
        return DataType.build("BOOLEAN")

    def to_sql(self, dialect) -> str:
        return f"({self.operand.to_sql(dialect)} IS NULL)"


class FunctionCall(Expression):
    __slots__ = ("name", "args")

    def __init__(self, name, args: List[Expression], datatype=None, metadata=None):
        """Create a function call.

        Args:
            name: Function name
            args: List of argument expressions
            datatype: Return type
            metadata: Additional metadata
        """
        super().__init__(datatype, metadata)
        self.name = name
        self.args = args

    def get_children(self):
        return tuple(self.args)

    def infer_type(self, schema_context):
        if self.datatype:
            return self.datatype
        raise ValueError("Cannot infer type for generic FunctionCall")

    def to_sql(self, dialect):
        args_sql = ", ".join(arg.to_sql(dialect) for arg in self.args)
        return f"{self.name}({args_sql})"


class Cast(FunctionCall):
    """CAST function"""

    def __init__(self, operand: Expression, to_type: DATATYPE, **kwargs):
        super().__init__("CAST", [operand], datatype=to_type, **kwargs)

    @property
    def to_type(self):
        return self.datatype

    def infer_type(self, schema_context):
        return self.to_type

    def to_sql(self, dialect):
        return f"CAST({self.args[0].to_sql(dialect)} AS {self.to_type})"


class Case(FunctionCall):
    __slots__ = ("whens", "default")

    def __init__(
        self,
        whens: List[Tuple[Expression, Expression]],
        default: Optional[Expression] = None,
        **kwargs,
    ):
        """
        Create a CASE expression.

        Args:
            whens: List of (condition, result) tuples
            default: Optional default expression
            kwargs: Additional arguments for FunctionCall
        """
        super().__init__("CASE", [], **kwargs)
        self.whens = whens
        self.default = default

    def get_children(self):
        children = []
        for when in self.whens:
            children.extend([when.condition, when.result])
        if self.default:
            children.append(self.default)
        return tuple(children)

    def infer_type(self, schema_context):
        if self.whens:
            return self.whens[0].result.infer_type(schema_context)
        if self.default:
            return self.default.infer_type(schema_context)
        return DataType.build("NULL")

    def to_sql(self, dialect):
        when_sql = " ".join(
            f"WHEN {when.condition.to_sql(dialect)} THEN {when.true_expr.to_sql(dialect)}"
            for when in self.whens
        )
        default_sql = f" ELSE {self.default.to_sql(dialect)}" if self.default else ""
        return f"CASE {when_sql}{default_sql} END"


class IF(FunctionCall):
    __slots__ = "true_expr" "false_expr"

    def __init__(
        self, condition, true_expr, false_expr=None, datatype=None, metadata=None
    ):
        super().__init__(
            self.__class__.__name__.upper(), [condition], datatype, metadata
        )
        self.true_expr = true_expr
        self.false_expr = false_expr

    @property
    def condition(self):
        return self.args[0]

    def get_children(self):
        return (self.args[0], self.true_expr, self.false_expr)

    def infer_type(self, schema_context):
        return self.true_expr.infer_type(schema_context)

    def to_sql(self, dialect):
        return f"{self.name}({self.args[0].to_sql(dialect)}, {self.true_expr.to_sql(dialect)}, {self.false_expr.to_sql(dialect)})"


class When(IF): ...


class AggFunc(FunctionCall):
    __slots__ = ("distinct", "ignorenulls")

    def __init__(
        self,
        name: str,
        args: List[Expression],
        distinct: bool = False,
        ignore_nulls: bool = False,
        datatype: Optional[Union[DATATYPE, DataType]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(name, args, datatype, metadata)
        self.distinct = distinct
        self.ignore_nulls = ignore_nulls

    def to_sql(self, dialect) -> str:
        distinct_kw = "DISTINCT " if self.distinct else ""
        args_sql = ", ".join(arg.to_sql(dialect) for arg in self.args)
        return f"{self.name}({distinct_kw}{args_sql})"


class Count(AggFunc):
    def __init__(self, arg: Expression, distinct: bool = False, **kwargs):
        super().__init__("COUNT", [arg], distinct, **kwargs)

    def infer_type(self, schema_context):
        return DataType.build("INTEGER")


class Sum(AggFunc):
    def __init__(
        self,
        arg,
        distinct=False,
        ignore_nulls=False,
        datatype=None,
        metadata=None,
    ):
        super().__init__("SUM", [arg], distinct, ignore_nulls, datatype, metadata)

    def infer_type(self, schema_context):
        arg_type = self.args[0].infer_type(schema_context)
        return arg_type if arg_type.is_numeric() else DataType.build("FLOAT")


class Avg(AggFunc):
    def __init__(
        self,
        arg,
        distinct=False,
        ignore_nulls=False,
        datatype=None,
        metadata=None,
    ):
        super().__init__("AVG", [arg], distinct, ignore_nulls, datatype, metadata)

    def infer_type(self, schema_context: Schema) -> DataType:
        return DataType.build("FLOAT")


class Max(AggFunc):
    def __init__(
        self,
        arg,
        distinct=False,
        ignore_nulls=False,
        datatype=None,
        metadata=None,
    ):
        super().__init__("MAX", [arg], distinct, ignore_nulls, datatype, metadata)

    def infer_type(self, schema_context: Schema) -> DataType:
        return self.args[0].infer_type(schema_context)


class Min(AggFunc):
    def __init__(
        self,
        arg,
        distinct=False,
        ignore_nulls=False,
        datatype=None,
        metadata=None,
    ):
        super().__init__("MIN", [arg], distinct, ignore_nulls, datatype, metadata)

    def infer_type(self, schema_context: Schema) -> DataType:
        return self.args[0].infer_type(schema_context)


class Subquery(Expression):
    __slots__ = ("query", "subquery_type", "correlated", "modifier")

    def __init__(
        self,
        query,
        subquery_type: Union[str, SubqueryType] = SubqueryType.SCALAR,
        correlated: bool = False,
        modifier: Optional[str] = None,
        datatype: Optional[Union[DATATYPE, DataType]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ):
        """
        Create a subquery.

        Args:
            query: Inner query expression
            subquery_type: Type of subquery
            correlated: Whether subquery references outer scope
            modifier: Quantifier (ANY, ALL, etc.)
            datatype: Result type
            metadata: Additional metadata
        """
        super().__init__(datatype, metadata)
        self.query = query
        self.subquery_type = (
            SubqueryType(subquery_type)
            if isinstance(subquery_type, str)
            else subquery_type
        )
        self.correlated = correlated
        self.modifier = modifier

    def get_children(self):
        return (self.query) if self.query else ()

    def infer_type(self, schema_context: Schema) -> DataType:
        if self.datatype:
            return self.datatype
        if self.subquery_type == SubqueryType.SCALAR:
            schema = self.query.get_output_schema(None)
            if schema and len(schema) > 0:
                return schema[0].infer_type(schema_context)
        return DataType.build("UNKNOWN")

    def to_sql(self, dialect) -> str:
        sql = [q.pprint() for q in self.query]
        # if self.query else []
        return (
            f"Subquery({sql}, type={self.subquery_type}, correlated={self.correlated})"
        )


class Strftime(FunctionCall):
    def __init__(self, args, datatype=None, metadata=None):
        super().__init__("STRFTIME", args, datatype, metadata)

        self._format = args[1]
        self.culture = args[2] if len(args) > 2 else None

    def to_sql(self, dialect):
        return f"STRFTIME({self.args[0].to_sql(dialect)}, '{self._format}')"

    def get_children(self):
        return (self.args[0],)

    def infer_type(self, schema_context):
        return DataType.build("VARCHAR")


class ABS(FunctionCall):
    def __init__(self, arg, datatype=None, metadata=None):
        super().__init__("ABS", [arg], datatype, metadata)

    def get_children(self):
        return (self.args[0],)

    def infer_type(self, schema_context):
        return self.args[0].infer_type(schema_context)

    def to_sql(self, dialect):
        return f"ABS({self.args[0].to_sql(dialect)})"


class Table(Expression):
    __slots__ = ("name", "schema", "constraints")

    def __init__(self, name: str, schema: Schema, constraints: List = None):
        super().__init__(None, None)
        self.name = name
        self.schema = schema
        self.constraints = constraints or []

    @property
    def columns(self) -> List[ColumnRef]:
        return self.schema.columns

    def add_constraint(self, constraint):
        self.constraints.append(constraint)

    def get_children(self):
        return []

    def infer_type(self, schema_context):
        return

    def is_unique(self, column_name):
        for constraint in self.constraints:
            from sqlglot import exp

            if constraint.this == column_name:
                if isinstance(
                    constraint.kind,
                    (exp.UniqueColumnConstraint, exp.PrimaryKeyColumnConstraint),
                ):
                    return True
        return False


class Catalog:
    functions: Dict[str, Dict[str, Any]] = {
        # "COUNT": {"is_aggregate": True, "return_type": DataType.INT},
        # "SUM": {"is_aggregate": True, "return_type": DataType.FLOAT},
        # "AVG": {"is_aggregate": True, "return_type": DataType.FLOAT},
        # "MIN": {"is_aggregate": True, "return_type": DataType.UNKNOWN},
        # "MAX": {"is_aggregate": True, "return_type": DataType.UNKNOWN},
        # "UPPER": {"is_aggregate": False, "return_type": DataType.TEXT},
        # "LOWER": {"is_aggregate": False, "return_type": DataType.TEXT},
        # "LENGTH": {"is_aggregate": False, "return_type": DataType.INT},
    }

    def __init__(self, tables: Dict[str, Table] = None):
        self.tables: Dict[str, Table] = tables or {}

    def add_table(self, table_info: Table):
        """Register a table in the catalog"""
        self.tables[table_info.name] = table_info

    def get_table(self, name: str) -> Optional[Table]:
        """Get table information by name"""
        return self.tables.get(name)


class ExpressionVisitor:

    def visit(self, expr: Expression):
        method = "visit_" + expr.__class__.__name__.lower()
        return getattr(self, method, self.visit_default)(expr)

    def visit_default(self, expr: Expression):
        raise NotImplementedError(f"No visitor method for {expr.__class__.__name__}")
