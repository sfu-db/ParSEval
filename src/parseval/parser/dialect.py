"""SQL syntax policy and explicit conversion into semantic scalar types."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

from sqlglot import exp
from sqlglot.dialects.dialect import Dialect

from parseval.errors import CatalogError, DDLImportError
from parseval.identifiers import Identifier, NameInput, QualifiedName
from parseval.terms.sorts import (
    ScalarSort,
    BOOLEAN,
    DATE,
    DECIMAL,
    FLOAT,
    INTEGER,
    INTERVAL,
    STRING,
    TIME,
    TIMESTAMP,
    ScalarType,
    TypeKind,
    parse_interval_value,
    parse_iso_temporal_value,
)


class SQLDialect:
    def __init__(self, name: str = "postgres") -> None:
        self.sqlglot = Dialect.get_or_raise(name)
        self.name = type(self.sqlglot).__name__.lower()

    def identifier(
        self,
        value: str | Identifier | exp.Identifier,
        *,
        column: bool = False,
    ) -> Identifier:
        if isinstance(value, Identifier):
            node = exp.Identifier(this=value.text, quoted=value.quoted)
        elif isinstance(value, exp.Identifier):
            node = value.copy()
        else:
            node = exp.Identifier(this=value, quoted=False)
        node = self.sqlglot.normalize_identifier(node)
        if column and self.name == "mysql":
            node.set("this", node.name.lower())
        return Identifier(node.name, bool(node.quoted))

    def sql(self, expression: exp.Expression) -> str:
        return expression.sql(dialect=self.name)

    @property
    def division_by_zero_is_null(self) -> bool:
        """SQLite and MySQL return NULL for division by zero; others raise."""
        return self.name in ("sqlite", "mysql")

    @property
    def lenient_conversions(self) -> bool:
        """SQLite and MySQL convert any text to a number and accept negative
        substring lengths instead of raising."""
        return self.name in ("sqlite", "mysql")

    @property
    def text_temporals(self) -> bool:
        """SQLite keeps dates and times as text: date and time columns hold
        text and compare as text, DATE, DATETIME, TIME and CURRENT_* render
        text as STRFTIME does, date functions read text time values ('now' or
        none is the current time, unparsable text is NULL), and arithmetic
        reads the text's numeric prefix."""
        return self.name == "sqlite"

    @property
    def case_insensitive_text(self) -> bool:
        """MySQL's default collation compares text without regard to case."""
        return self.name == "mysql"

    def common_scalar_sort(
        self,
        left: ScalarSort,
        right: ScalarSort,
        *,
        prefer_float: bool = False,
        arithmetic: bool = False,
    ) -> ScalarSort | None:
        nullable = left.nullable or right.nullable
        numeric = {TypeKind.INTEGER, TypeKind.FLOAT, TypeKind.DECIMAL}
        kinds = {left.sql_type.kind, right.sql_type.kind}

        if self.name == "sqlite" and arithmetic:
            arithmetic_kinds = numeric | {
                TypeKind.BOOLEAN,
                TypeKind.STRING,
                TypeKind.DATE,
                TypeKind.TIME,
                TypeKind.TIMESTAMP,
            }
            if kinds <= arithmetic_kinds:
                # SQLite arithmetic applies numeric affinity. FLOAT is a safe
                # carrier when an operand is not already known to be integer.
                sql_type = (
                    INTEGER
                    if kinds == {TypeKind.INTEGER} and not prefer_float
                    else FLOAT
                )
                return ScalarSort(sql_type, nullable)

        # SQLGlot annotates numeric literals independently of the declared
        # type of the other operand. Keep that syntax information out of the
        # semantic IR by selecting the dialect's common numeric type here.
        # DECIMAL remains a finite real-valued domain in this model; it wins
        # over the other numeric carriers and retains a precision and scale.
        if kinds <= numeric:
            if TypeKind.DECIMAL in kinds:
                decimal_types = tuple(
                    sql_type
                    for sql_type in (left.sql_type, right.sql_type)
                    if sql_type.kind is TypeKind.DECIMAL
                )
                normalized = tuple(
                    DECIMAL
                    if sql_type.precision is None
                    else ScalarType(
                        TypeKind.DECIMAL,
                        sql_type.precision,
                        sql_type.scale if sql_type.scale is not None else 0,
                    )
                    for sql_type in decimal_types
                )
                scale = max(sql_type.scale or 0 for sql_type in normalized)
                integral = max(
                    (sql_type.precision or 0) - (sql_type.scale or 0)
                    for sql_type in normalized
                )
                sql_type = ScalarType(TypeKind.DECIMAL, integral + scale, scale)
            elif TypeKind.FLOAT in kinds:
                sql_type = FLOAT
            else:
                sql_type = FLOAT if prefer_float else INTEGER
            return ScalarSort(sql_type, nullable)
        if left.sql_type == right.sql_type:
            return ScalarSort(left.sql_type, nullable)

        if self.name in ("sqlite", "mysql") and kinds == {
            TypeKind.BOOLEAN,
            TypeKind.INTEGER,
        }:
            return ScalarSort(BOOLEAN, nullable)

        temporal = {TypeKind.DATE, TypeKind.TIME, TypeKind.TIMESTAMP}
        # SQLGlot represents unadorned temporal literals as strings. Choosing
        # the temporal operand's type lets elaboration turn the literal into a
        # typed IR value. Inputs are assumed to be valid for their dialect.
        if TypeKind.STRING in kinds:
            temporal_kinds = kinds & temporal
            if len(temporal_kinds) == 1:
                sql_type = (
                    left.sql_type
                    if left.sql_type.kind in temporal
                    else right.sql_type
                )
                return ScalarSort(sql_type, nullable)

        # DATE promotes to TIMESTAMP when the two temporal carriers interact.
        if kinds == {TypeKind.DATE, TypeKind.TIMESTAMP}:
            return ScalarSort(TIMESTAMP, nullable)

        if self.name == "sqlite" and TypeKind.STRING in kinds:
            # SQLite applies TEXT affinity when a text column is compared
            # with a numeric literal.  A symbolic cast retains that coercion
            # explicitly instead of rejecting otherwise valid SQLite SQL.
            if kinds & numeric:
                return ScalarSort(STRING, nullable)
        if self.name == "sqlite" and kinds & temporal and kinds & numeric:
            return ScalarSort(STRING, nullable)
        return None

    def literal_value(self, literal: exp.Literal, sql_type: ScalarType):
        text = str(literal.this)
        if literal.is_string:
            return (
                text
                if sql_type.kind is TypeKind.STRING
                else self.parse_string_cast_literal(text, sql_type)
            )
        if sql_type.kind is TypeKind.INTEGER:
            return int(Decimal(text))
        if sql_type.kind in (TypeKind.FLOAT, TypeKind.DECIMAL):
            return float(text)
        return text

    def parse_string_cast_literal(self, value: str, sql_type: ScalarType):
        if sql_type.kind is TypeKind.STRING:
            return value
        if sql_type.kind is TypeKind.INTEGER:
            return int(value)
        if sql_type.kind in (TypeKind.FLOAT, TypeKind.DECIMAL):
            return float(value)
        if sql_type.kind in (TypeKind.DATE, TypeKind.TIME, TypeKind.TIMESTAMP):
            return parse_iso_temporal_value(value, sql_type)
        if sql_type.kind is TypeKind.INTERVAL:
            return parse_interval_value(value)
        if sql_type.kind is TypeKind.BOOLEAN:
            normalized = value.casefold()
            if normalized in ("true", "1"):
                return True
            if normalized in ("false", "0"):
                return False
        raise ValueError(f"Unsupported string cast to {sql_type!r}")

    def array_literal_values(self, text: str) -> tuple[str | None, ...]:
        """Parse a one-dimensional PostgreSQL array input literal."""

        if self.name != "postgres" or len(text) < 2 or text[0] != "{" or text[-1] != "}":
            raise ValueError("Expected a PostgreSQL array literal")
        body = text[1:-1]
        if not body:
            return ()
        values: list[str | None] = []
        token: list[str] = []
        quoted = False
        escaped = False
        token_was_quoted = False
        for character in body:
            if escaped:
                token.append(character)
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                quoted = not quoted
                token_was_quoted = True
            elif character == "," and not quoted:
                value = "".join(token)
                values.append(
                    value
                    if token_was_quoted or value.casefold() != "null"
                    else None
                )
                token = []
                token_was_quoted = False
            elif character in "{}" and not quoted:
                raise ValueError("Nested PostgreSQL array literals are unsupported")
            else:
                token.append(character)
        if quoted or escaped:
            raise ValueError("Malformed PostgreSQL array literal")
        value = "".join(token)
        values.append(
            value if token_was_quoted or value.casefold() != "null" else None
        )
        return tuple(values)

    def nulls_first(self, *, descending: bool) -> bool:
        # SQLite and MySQL order NULL below every value, PostgreSQL above.
        if self.name in ("sqlite", "mysql"):
            return not descending
        return descending

    def qualified_name(self, value: NameInput | exp.Table) -> QualifiedName:
        if isinstance(value, str):
            value = exp.to_table(value, dialect=self.name)
        if isinstance(value, exp.Table):
            parts = value.parts
        elif isinstance(value, QualifiedName):
            parts = value.parts
        elif isinstance(value, Identifier):
            parts = (value,)
        else:
            parts = tuple(value)
        if not 1 <= len(parts) <= 3:
            raise CatalogError("Table names must have one to three components")
        return QualifiedName(tuple(self.identifier(part) for part in parts))

    def scalar_type(self, datatype: exp.DataType) -> ScalarType:
        """Map supported declared types; retain the original declaration in Catalog."""
        kind = datatype.this
        t = exp.DataType.Type
        if kind in (
            t.TINYINT,
            t.SMALLINT,
            t.MEDIUMINT,
            t.INT,
            t.BIGINT,
            t.SMALLSERIAL,
            t.SERIAL,
            t.BIGSERIAL,
        ):
            return INTEGER
        if kind in (t.FLOAT, t.DOUBLE):
            return FLOAT
        if kind == t.DECIMAL:
            if len(datatype.expressions) > 2:
                raise DDLImportError("DECIMAL accepts only precision and scale")
            try:
                parameters = [int(item.this.this) for item in datatype.expressions]
            except (ValueError, TypeError) as exc:
                raise DDLImportError("DECIMAL parameters must be integers") from exc
            if not parameters:
                return DECIMAL
            if len(parameters) == 1:
                parameters.append(0)
            return ScalarType(TypeKind.DECIMAL, *parameters)
        if kind in (
            t.CHAR,
            t.VARCHAR,
            t.TEXT,
            t.NCHAR,
            t.NVARCHAR,
            t.BPCHAR,
            t.TINYTEXT,
            t.MEDIUMTEXT,
            t.LONGTEXT,
            t.ENUM,
        ):
            return STRING
        if kind == t.BOOLEAN:
            return BOOLEAN
        if kind == t.DATE:
            return DATE
        if kind in (t.TIME, t.TIMETZ):
            return TIME
        if kind in (t.TIMESTAMP, t.TIMESTAMPTZ, t.DATETIME):
            return TIMESTAMP
        if kind == t.INTERVAL:
            return INTERVAL
        if kind in (t.BINARY, t.VARBINARY):
            return ScalarType(TypeKind.OPAQUE)
        raise DDLImportError(f"Unsupported SQL type: {datatype.sql(dialect=self.name)}")

    def storage_type(self, declaration: str, sql_type: ScalarType) -> ScalarType:
        """Refine a column's internal type with dialect-specific storage limits."""
        datatype = exp.DataType.build(declaration, dialect=self.name)
        t = exp.DataType.Type
        length = sql_type.max_length
        bits = sql_type.integer_bits
        if sql_type.kind is TypeKind.STRING and datatype.is_type(*exp.DataType.TEXT_TYPES):
            if datatype.expressions:
                try:
                    length = int(datatype.expressions[0].this.this)
                except (ValueError, TypeError, AttributeError) as exc:
                    raise DDLImportError("String length must be an integer") from exc
            elif (
                self.name == "postgres" and datatype.is_type(t.CHAR)
                or self.name == "mysql" and datatype.is_type(t.CHAR, t.NCHAR)
            ):
                length = 1
        if sql_type.kind is TypeKind.INTEGER:
            bits = {t.SMALLINT: 16, t.INT: 32, t.BIGINT: 64}.get(datatype.this, bits)
        if self.name == "sqlite":
            length = bits = None
        return replace(sql_type, max_length=length, integer_bits=bits)

    def column_type(self, datatype: exp.DataType) -> ScalarType:
        """A column's type: with text temporals, declared dates and times hold text."""
        sql_type = self.scalar_type(datatype)
        if self.text_temporals and sql_type.kind in (TypeKind.DATE, TypeKind.TIME, TypeKind.TIMESTAMP):
            return STRING
        return sql_type

    def sql_type(self, datatype: exp.DataType | None) -> ScalarType:
        if datatype is None:
            raise DDLImportError("SQLGlot could not infer an expression type")
        return self.scalar_type(datatype)

    def type_sql(self, datatype: ScalarType) -> str:
        if datatype.kind is TypeKind.STRING and datatype.max_length is not None:
            return f"VARCHAR({datatype.max_length})"
        if datatype.kind is TypeKind.INTEGER and datatype.integer_bits is not None:
            names = {16: "SMALLINT", 32: "INT", 64: "BIGINT"}
            try:
                return names[datatype.integer_bits]
            except KeyError as exc:
                raise CatalogError(f"No SQL representation for {datatype!r}") from exc
        if datatype.kind is TypeKind.DECIMAL:
            if datatype.precision is None:
                return "DECIMAL"
            suffix = f", {datatype.scale}" if datatype.scale is not None else ""
            return f"DECIMAL({datatype.precision}{suffix})"
        names = {
            TypeKind.BOOLEAN: "BOOLEAN",
            TypeKind.INTEGER: "INT",
            TypeKind.FLOAT: "DOUBLE",
            TypeKind.STRING: "TEXT",
            TypeKind.DATE: "DATE",
            TypeKind.TIME: "TIME",
            TypeKind.TIMESTAMP: "TIMESTAMP",
            TypeKind.INTERVAL: "INTERVAL",
            TypeKind.OPAQUE: "BLOB",
        }
        try:
            return names[datatype.kind]
        except KeyError as exc:
            raise CatalogError(f"No SQL representation for {datatype!r}") from exc

    def parse_ddl(self, sql: str) -> list[exp.Expression | None]:
        # Retain source spelling: storage limits (lengths, integer widths) and
        # catalog metadata come from the declared type.
        class SourceTypeParser(self.sqlglot.parser_class):
            def _parse_types(
                self, check_func=False, schema=False, allow_identifiers=True
            ):
                start = self._curr.start if self._curr is not None else None
                result = super()._parse_types(check_func, schema, allow_identifiers)
                if schema and isinstance(result, exp.DataType) and start is not None:
                    result.meta["declared_sql"] = self.sql[start : self._prev.end + 1]
                return result

        return SourceTypeParser(dialect=self.sqlglot).parse(
            self.sqlglot.tokenize(sql), sql
        )
