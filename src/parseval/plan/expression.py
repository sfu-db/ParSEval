from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Any, Optional, Dict, TYPE_CHECKING, List, Union, Tuple, Callable, Set

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

    def with_children(self, new_children: Tuple["Expression", ...]) -> "Expression":
        """
        Rebuild this expression with a new set of children.
        Subclasses should override this if they have custom constructor signatures.
        """
        if new_children == self.get_children():
            return self
        raise NotImplementedError(
            f"{self.__class__.__name__} must implement with_children()"
        )

    def find_all(self, target: "Expression") -> List["Expression"]:
        """Find all instances of a target expression in the tree."""
        matches = []
        stack = [self]
        while stack:
            current = stack.pop()
            if isinstance(current, target):
                matches.append(current)
            children = current.get_children()
            for child in children:
                if isinstance(child, Expression):
                    stack.append(child)
        return matches

    def transform(
        self, func: Callable[["Expression"], Optional["Expression"]]
    ) -> "Expression":
        new_self = func(self)
        if new_self is not None and new_self is not self:
            return new_self

        children = self.get_children()
        if not children:
            return self

        for child in children:
            if child is None:
                print(f"Warning: {repr(self)} has None child")
                raise ValueError(f"Expression has None child {self}")

        new_children = tuple(child.transform(func) for child in children)
        if new_children == children:
            return self

        return self.with_children(new_children)

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

    def is_null(self):
        """Check if this expression is an IS NULL predicate."""
        return IS(self, "IS", Literal(value=None, datatype=self.datatype))

    def negate(self) -> "Expression":
        """Return the negation of this expression."""

        if isinstance(self, NOT):
            return self.operand
        else:
            neg_map = {
                ">": "<=",
                ">=": "<",
                "<": ">=",
                "<=": ">",
                "=": "!=",
                "!=": "=",
            }
            if self.op in neg_map:
                return Predicate(
                    left=self.left,
                    op=neg_map[self.op],
                    right=self.right,
                    datatype=self.datatype,
                    metadata=self.metadata.copy(),
                )

            return NOT(self)
        # if isinstance(expr, EQ):
        #     return NEQ(context=expr.context, this=expr.left, operand=expr.right)
        # if isinstance(expr, NEQ):
        #     return EQ(context=expr.context, this=expr.left, operand=expr.right)
        # if isinstance(expr, GT):
        #     return LTE(context=expr.context, this=expr.left, operand=expr.right)
        # if isinstance(expr, GTE):
        #     return LT(context=expr.context, this=expr.left, operand=expr.right)
        # if isinstance(expr, LT):
        #     return GTE(context=expr.context, this=expr.left, operand=expr.right)
        # if isinstance(expr, LTE):
        #     return GT(context=expr.context, this=expr.left, operand=expr.right)
        # return Not(context=expr.context, this=expr)

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

    def with_children(self, new_children):
        return self

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

    def with_children(self, new_children):
        return self
        # ColumnRef(
        #     self.name,
        #     table_alias=self.table_alias,
        #     ref=self.ref,
        #     datatype=self.datatype,
        #     metadata=self.metadata.copy() ,
        # )

    def to_sql(self, dialect):
        return self.name

    def __repr__(self) -> str:
        return f"ColumnRef({self.qualified_name!r}, {self.datatype})"

    def __eq__(self, other: Any) -> bool:
        if not isinstance(other, ColumnRef):
            return False
        return (
            self.name == other.name
            and self.datatype == other.datatype
            and self.metadata.get("table", None) == other.metadata.get("table", None)
        )

    def __hash__(self) -> int:
        return hash(
            (
                self.__class__.__name__,
                self.name,
                self.metadata.get("table", None),
                str(self.datatype),
            )
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

    def with_children(self, new_children):
        return self

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

    def with_children(self, new_children):
        left, right = new_children
        return self.__class__(
            left=left,
            right=right,
            op=self.op,
            datatype=self.datatype,
            metadata=self.metadata,
        )

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

    def with_children(self, new_children):
        (operand,) = new_children
        return self.__class__(
            op=self.op, operand=operand, datatype=self.datatype, metadata=self.metadata
        )

    def infer_type(self, schema_context):
        if self.datatype:
            return self.datatype
        return self.operand.infer_type(schema_context)

    def to_sql(self, dialect):
        operand_sql = self.operand.to_sql(dialect)
        return f"({self.op} {operand_sql})"


# class Predicate(Expression):
#     """ Base class for boolean predicates."""
class Predicate(BinaryOp):
    def infer_type(self, schema_context):
        return DataType.build("BOOLEAN")

    def not_(self):
        if isinstance(self, NOT):
            return self.operand
        return NOT(self)


class IS(Predicate):
    def __init__(self, left, op, right, datatype=None, metadata=None):
        super().__init__(left, op, right, datatype, metadata)

    def infer_type(self, schema_context):
        return DataType.build("BOOLEAN")


class AND(BinaryOp):
    """Logical AND"""

    def __init__(self, left: Expression, right: Expression, datatype="bool", **kwargs):
        super().__init__(left, "AND", right, datatype=datatype, **kwargs)


class OR(BinaryOp):
    """Logical OR"""

    def __init__(self, left: Expression, right: Expression, datatype="bool", **kwargs):
        super().__init__(left, "OR", right, datatype=datatype, **kwargs)


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

    # def infer_type(self, schema_context):
    #     if self.datatype:
    #         return self.datatype
    #     raise ValueError("Cannot infer type for generic FunctionCall")

    def with_children(self, new_children):
        return self.__class__(
            # name=self.name,
            args=list(new_children),
            datatype=self.datatype,
            metadata=self.metadata.copy(),
        )

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

    def with_children(self, new_children):
        operand = new_children[0]

        return self.__class__(
            operand=operand,
            to_type=self.to_type,
            metadata=self.metadata.copy(),
        )

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
            children.append(when)
        if self.default:
            children.append(self.default)
        return tuple(children)

    def infer_type(self, schema_context):
        if self.whens:
            return self.whens[0].result.infer_type(schema_context)
        if self.default:
            return self.default.infer_type(schema_context)
        return DataType.build("NULL")

    def with_children(self, new_children):
        default = new_children[-1]
        whens = new_children[:-1]
        # whens, default = new_children

        return self.__class__(
            whens=whens,
            default=default,
            datatype=self.datatype,
            metadata=self.metadata.copy(),
        )

        expected_len = len(self.whens) * 2 + (1 if self.default else 0)
        if len(new_children) != expected_len:
            raise ValueError(
                f"{self.__class__.__name__}: expected {expected_len} children, got {len(new_children)}"
            )

        i = 0
        new_whens = []
        for _ in self.whens:
            cond = new_children[i]
            result = new_children[i + 1]
            new_whens.append((cond, result))
            i += 2

        new_default = new_children[-1] if self.default else None

        print("new_whens:", new_whens)
        print("old whens:", self.whens)

        # Check if nothing has changed
        if (
            all(
                c1 is c2 and r1 is r2
                for (c1, r1), (c2, r2) in zip(self.whens, new_whens)
            )
            and self.default is new_default
        ):
            return self

        return Case(
            whens=new_whens,
            default=new_default,
            datatype=self.datatype,
            metadata=self.metadata.copy(),
        )

    def to_sql(self, dialect):
        when_sql = " ".join(
            f"WHEN {when.condition.to_sql(dialect)} THEN {when.true_expr.to_sql(dialect)}"
            for when in self.whens
        )
        default_sql = f" ELSE {self.default.to_sql(dialect)}" if self.default else ""
        return f"CASE {when_sql}{default_sql} END"


class IF(FunctionCall):
    __slots__ = ("true_expr", "false_expr")

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
        if self.false_expr:
            return (self.condition, self.true_expr, self.false_expr)
        return (self.condition, self.true_expr)

    def infer_type(self, schema_context):
        return self.true_expr.infer_type(schema_context)

    def with_children(self, new_children: Tuple[Expression, ...]) -> "IF":
        """
        Create a new IF expression with replaced children.

        Args:
            new_children: A tuple of new child expressions in order:
                          (condition, true_expr, false_expr)

        Returns:
            A new IF expression with updated children.
        """
        # Defensive validation
        if len(new_children) not in (2, 3):
            raise ValueError(f"IF expects 2 or 3 children, got {len(new_children)}")

        condition = new_children[0]
        true_expr = new_children[1]
        false_expr = new_children[2] if len(new_children) == 3 else None
        return self.__class__(
            condition=condition,
            true_expr=true_expr,
            false_expr=false_expr,
            datatype=self.datatype,
            metadata=self.metadata.copy(),
        )

    def to_sql(self, dialect):
        if self.false_expr:
            return f"{self.name}({self.condition.to_sql(dialect)}, {self.true_expr.to_sql(dialect)}, {self.false_expr.to_sql(dialect)})"
        return f"{self.name}({self.condition.to_sql(dialect)}, {self.true_expr.to_sql(dialect)}"

    # f"{self.name}({self.args[0].to_sql(dialect)}, {self.true_expr.to_sql(dialect)}, {self.false_expr.to_sql(dialect)})"


class When(IF): ...


class AggFunc(FunctionCall):
    __slots__ = ("distinct", "ignore_nulls")
    FUNCTION_RETURN_TYPE = {
        "COUNT": lambda self, schema: DataType.build("INTEGER"),
        "SUM": lambda self, schema: (
            self.args[0].infer_type(schema)
            if self.args[0].infer_type(schema).is_numeric()
            else DataType.build("FLOAT")
        ),
        "AVG": lambda self, schema: DataType.build("FLOAT"),
        "MIN": lambda self, schema: self.args[0].infer_type(schema),
        "MAX": lambda self, schema: self.args[0].infer_type(schema),
    }

    def __init__(
        self,
        name: str,
        args: Expression,
        distinct: bool = False,
        ignore_nulls: bool = False,
        datatype: Optional[Union[DATATYPE, DataType]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(name, args, datatype, metadata)
        self.distinct = distinct
        self.ignore_nulls = ignore_nulls

    def infer_type(self, schema_context):
        fn_name = self.name.upper()
        if fn_name in self.FUNCTION_RETURN_TYPE:
            return self.FUNCTION_RETURN_TYPE[fn_name](self, schema_context)
        raise ValueError(f"Cannot infer type for aggregate function {self.name}")

    def with_children(self, new_children: Tuple[Expression, ...]) -> "AggFunc":
        if len(new_children) != len(self.args):
            raise ValueError(
                f"{self.__class__.__name__}: expected {len(self.args)} args, got {len(new_children)}"
            )
        # If nothing changed, return self
        if tuple(self.args) == new_children:
            return self

        return AggFunc(
            name=self.name,
            args=list(new_children),
            distinct=self.distinct,
            ignore_nulls=self.ignore_nulls,
            datatype=self.datatype,
            metadata=self.metadata.copy(),
        )

    def to_sql(self, dialect) -> str:
        distinct_kw = "DISTINCT " if self.distinct else ""

        args_sql = ", ".join(arg.to_sql(dialect) for arg in self.args)
        return f"{self.name}({distinct_kw}{args_sql})"


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
        # Support both single query node and iterable of nodes
        if self.query is None:
            return ()
        elif isinstance(self.query, (list, tuple)):
            return tuple(self.query)
        else:
            return (self.query,)

    def infer_type(self, schema_context: Schema) -> DataType:
        if self.datatype:
            return self.datatype
        if self.subquery_type == SubqueryType.SCALAR:
            schema = self.query.get_output_schema(None)
            if schema and len(schema) > 0:
                return schema[0].infer_type(schema_context)
        return DataType.build("UNKNOWN")

    def with_children(self, new_children: Tuple["Expression", ...]) -> "Subquery":
        """
        Return a new Subquery instance with replaced child(ren).
        """
        if not new_children:
            raise ValueError("Subquery must have at least one child expression")

        # Rebuild query structure: single or list
        new_query = new_children[0] if len(new_children) == 1 else list(new_children)

        return Subquery(
            query=new_query,
            subquery_type=self.subquery_type,
            correlated=self.correlated,
            modifier=self.modifier,
            datatype=self.datatype,
            metadata=self.metadata.copy(),
        )

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
        return tuple(self.args)

    def infer_type(self, schema_context):
        return DataType.build("VARCHAR")


class ABS(FunctionCall):
    def __init__(self, arg, datatype=None, metadata=None):
        super().__init__("ABS", [arg], datatype, metadata)

    def get_children(self):
        return tuple(self.args)

    def infer_type(self, schema_context):
        return self.args[0].infer_type(schema_context)

    def with_children(self, new_children):
        return ABS(new_children[0], self.datatype, self.metadata.copy())

    def to_sql(self, dialect):
        return f"ABS({self.args[0].to_sql(dialect)})"


class Table(Expression):
    __slots__ = (
        "name",
        "schema",
        "constraints",
        "_columns",
        "primary_key",
        "foreign_key",
    )

    def __init__(
        self,
        name: str,
        schema: Schema,
        constraints: Dict[str, Set[Any]] = None,
        primary_key=None,
        foreign_key=None,
    ):
        super().__init__(None, None)
        self.name = name
        self.schema = schema
        self.constraints = constraints or {}
        self.primary_key = primary_key
        self.foreign_key = foreign_key
        self._columns = []

    @property
    def columns(self) -> List[ColumnRef]:
        if self._columns:
            return self._columns
        self._columns = []
        for column in self.schema.columns:
            nullable = self.nullable(column.name)
            unique = self.is_unique(column.name)
            column.metadata["table"] = self.name
            column.metadata["unique"] = unique
            column.datatype.nullable = nullable
            self._columns.append(column)
        return self._columns

    def add_constraint(self, column_name, constraint):
        self.constraints.setdefault(column_name, set()).add(constraint)
        # self.constraints[column_name].setdefault(set()).add(constraint)

    def get_children(self):
        return []

    def infer_type(self, schema_context):
        return

    def nullable(self, column_name):
        from sqlglot import exp

        if self.primary_key:
            for column_name in self.primary_key.find_all(exp.Identifier):
                if column_name.this.name == column_name:
                    return False
        for constraint in self.constraints.get(column_name, []):

            if isinstance(constraint.kind, exp.NotNullColumnConstraint):
                return constraint.kind.args.get("allow_null", False)
                # return False

        return True

    def is_unique(self, column_name):
        for constraint in self.constraints.get(column_name, []):
            from sqlglot import exp

            if isinstance(
                constraint.kind,
                (exp.UniqueColumnConstraint, exp.PrimaryKeyColumnConstraint),
            ):
                return True
        if self.primary_key:
            for column_name in self.primary_key.find_all(exp.Identifier):
                if column_name.this.name == column_name:
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
