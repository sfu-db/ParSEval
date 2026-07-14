from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Dict, Mapping, Tuple, Optional, List, TYPE_CHECKING
from itertools import product
from sqlglot import exp

from parseval.solver.types import SolverVar

if TYPE_CHECKING:
    from parseval.instance import Instance
    from parseval.dtype import DATATYPE
    from .rex import Symbol

@dataclass
class IndicatorVar:
    """One boolean indicator for an atomic predicate.

    For symbolic rows: ``var`` is a SolverVar; ``concrete_value`` is None.
    For concrete rows: ``var`` is None; ``concrete_value`` is True/False.

    ``row_id`` ties the indicator to a specific row (by its ``rowid`` tuple)
    so the materialization step can skip concrete rows that fail filters.
    """

    step_id: str
    atom_id: int
    atom_expr: exp.Expression
    var: Optional[SolverVar] = None
    concrete_value: Optional[bool] = None
    row_id: Optional[Tuple[str, ...]] = None


class Row(exp.Expression):
    """One logical row produced by a plan step.

    A ``Row`` pairs a stable row identity (``this`` — a tuple of ids) with
    a mapping from column identity/name to the cell value. Rows are the
    unit of currency for :class:`DerivedSchema` and
    flow through :class:`parseval.symbolic.encoder.SymbolicScopeEncoder`
    between plan steps.

    Although it subclasses :class:`sqlglot.exp.Expression` (so it can be
    embedded in SQL-like pretty-printing and share the Symbol dispatch),
    ``Row`` is a runtime container, not an AST node used for planning.
    Keep the AST-flavored operations (``sql()`` etc.) minimal.
    """

    arg_types = {"this": True, "columns": True}

    @property
    def column_values(self) -> Mapping[exp.Identifier, "Symbol"]:
        return self.args.get("columns", {})

    @property
    def columns(self) -> Tuple[exp.Identifier, ...]:
        return tuple(self.column_values.keys())

    @property
    def rowid(self) -> Tuple[str, ...]:
        if isinstance(self.this, tuple):
            return self.this
        return (self.this,)

    def _key_name(self, key):
        if isinstance(key, exp.Expression):
            return key.alias_or_name or key.sql()
        return str(key)

    def items(self):
        return self.column_values.items()

    def values(self):
        return self.column_values.values()

    def __iter__(self):
        return iter(self.column_values)

    def __contains__(self, key):
        try:
            self[key]
        except KeyError:
            return False
        return True

    def __getitem__(self, key):
        columns = self.column_values
        if key in columns:
            return columns[key]
        normalized = self._key_name(key)
        for column_name, value in columns.items():
            if self._key_name(column_name) == normalized:
                return value
        raise KeyError(key)

    def get(self, table: exp.Table | str, column):
        del table
        return self[column]

    def __len__(self):
        return len(self.column_values)

    def __add__(self, other):
        assert isinstance(other, Row), f"Cannot add Row with {type(other)}"
        new_columns = {**self.column_values, **other.column_values}
        rid = self.rowid + other.rowid
        return Row(this=rid, columns=new_columns)

    def sql(self, dialect=None, **opts):
        return f"{self.key}({self.this})"


def is_concrete_row(row: Row) -> bool:
    """Return True if all column values in *row* are Python scalars."""
    return not any(isinstance(v, SolverVar) for v in row.column_values.values())


class DerivedSchema:
    def __init__(
        self,
        columns: Tuple[exp.Identifier | str, ...],
        rows=None,
        column_range=None,
        datatypes: Optional[Dict[Any, DATATYPE]] = None,
        nullables: Optional[Dict[Any, bool]] = None,
        uniqueness: Optional[Dict[Any, bool]] = None,
        indicators: Optional[List[IndicatorVar]] = None,
        constraints: Optional[List[exp.Expression]] = None,
        equalities: Optional[List[Any]] = None,
        obligations: Optional[List[Any]] = None,
        evidence: Optional[Dict[str, Any]] = None,
        expression_bindings: Optional[Dict[str, exp.Expression]] = None,
        row_provenance: Optional[Dict[Tuple[str, ...], Dict[str, Any]]] = None,
        create_rows: Optional[Mapping[Any, Any]] = None,
        problem: Optional[Any] = None,
        assignments: Optional[Mapping[Any, Any]] = None,
        bounds: Optional[Any] = None,
        status: str = "sat",
        reason: str = "",
        coverage_ratio: float = 0.0,
        coverage_tree: Optional[Any] = None,
    ):
        self.columns = tuple(columns)
        self.column_range = column_range
        self.reader = RowReader(self.columns, self.column_range)
        self.rows = rows or []
        self.mask = [True] * len(self.rows)
        self.datatypes = datatypes or {}
        self.nullables = nullables or {}
        self.uniqueness: Dict[Any, bool] = uniqueness or {}
        self.indicators: List[IndicatorVar] = indicators or []
        self.constraints: List[exp.Expression] = constraints or []
        self.equalities: List[Any] = equalities or []
        self.obligations: List[Any] = obligations or []
        self.evidence: Dict[str, Any] = evidence or {}
        self.expression_bindings: Dict[str, exp.Expression] = expression_bindings or {}
        self.row_provenance: Dict[Tuple[str, ...], Dict[str, Any]] = row_provenance or {}
        self.create_rows: Mapping[Any, Any] = create_rows or {}
        self.problem = problem
        self.assignments: Mapping[Any, Any] = assignments or {}
        self.bounds = bounds
        self.status = status
        self.reason = reason
        self.coverage_ratio = coverage_ratio
        self.coverage_tree = coverage_tree

        if rows:
            assert len(rows[0]) == len(
                self.columns
            ), f"Row length does not match number of columns. {len(rows[0])} != {len(self.columns)}"
        self.range_reader = RangeReader(self)

    def is_unique(self, column):
        return self.uniqueness.get(column, False)

    def nullable(self, column):
        return self.nullables.get(column, False)

    def get_column_type(self, column):
        return self.datatypes.get(column, None)

    def add_columns(self, *columns: exp.Identifier | str) -> None:
        self.columns += columns
        if self.column_range:
            self.column_range = range(
                self.column_range.start, self.column_range.stop + len(columns)
            )
        self.reader = RowReader(self.columns, self.column_range)

    def append(self, row):
        assert len(row) == len(
            self.columns
        ), f"Row length does not match number of columns. {len(row)} != {len(self.columns)}"
        self.rows.append(row)
        self.mask.append(True)

    def pop(self):
        self.rows.pop()

    def with_rows(
        self,
        rows: List[Row],
        *,
        columns: Optional[Tuple[Any, ...]] = None,
        column_range=None,
    ) -> "DerivedSchema":
        row_ids = {row.rowid for row in rows}
        return DerivedSchema(
            columns=self.columns if columns is None else columns,
            rows=rows,
            column_range=self.column_range if column_range is None else column_range,
            datatypes=self.datatypes,
            nullables=self.nullables,
            uniqueness=self.uniqueness,
            indicators=list(self.indicators),
            constraints=list(self.constraints),
            equalities=list(self.equalities),
            obligations=list(self.obligations),
            evidence=dict(self.evidence),
            expression_bindings=dict(self.expression_bindings),
            row_provenance=dict(self.row_provenance),
            create_rows=dict(self.create_rows),
            problem=self.problem,
            assignments=dict(self.assignments),
            bounds=self.bounds,
            status=self.status,
            reason=self.reason,
            coverage_ratio=self.coverage_ratio,
            coverage_tree=self.coverage_tree,
        )

    @property
    def width(self):
        return len(self.columns)

    def __len__(self):
        return len(self.rows)

    def __iter__(self):
        return TableIter(self)

    def __getitem__(self, index):
        self.reader.row = self.rows[index]
        return self.reader

    def __repr__(self):
        columns = tuple(
            column
            for i, column in enumerate(self.columns)
            if not self.column_range or i in self.column_range
        )
        lines = [" ".join(str(column) for column in columns)]

        for i, row in enumerate(self):
            if i > 10:
                break

            lines.append(
                " ".join(
                    str(row[column])
                    for column in columns  # .rjust(widths[column])[0 : widths[column]]
                )
            )
        return "\n".join(lines)


class TableIter:
    def __init__(self, table):
        self.table = table
        self.index = -1

    def __iter__(self):
        return self

    def __next__(self):
        self.index += 1
        if self.index < len(self.table):
            return self.table[self.index]
        raise StopIteration


class RangeReader:
    def __init__(self, table):
        self.table = table
        self.range = range(0)

    def __len__(self):
        return len(self.range)

    def __getitem__(self, column):
        return (self.table[i][column] for i in self.range)


class RowReader:
    def __init__(self, columns, column_range=None):
        self.columns = {
            column: i
            for i, column in enumerate(columns)
            if not column_range or i in column_range
        }
        self.row: Row | None = None

    def rowid(self):
        if self.row is not None:
            return self.row.rowid

    def _key_name(self, key):
        if isinstance(key, exp.Expression):
            return key.alias_or_name or key.sql()
        return str(key)

    def _column_key(self, column):
        if isinstance(column, exp.Column):
            return column.this
        return column

    def _slot_for_column_key(self, col_key):
        if col_key is None:
            return None
        if col_key in self.columns:
            return self.columns[col_key]
        key_name = self._key_name(col_key)
        for k, slot in self.columns.items():
            if self._key_name(k) == key_name:
                return slot
        return None

    def resolve(self, column):
        if self.row is None:
            raise KeyError(column)
        col_key = self._column_key(column)
        slot = self._slot_for_column_key(col_key)
        if slot is None:
            raise KeyError(column)
        try:
            return tuple(self.row.values())[slot]
        except IndexError as exc:
            raise KeyError(column) from exc

    def __getitem__(self, column):
        return self.resolve(column)

    def get(self, table, column):
        del table
        return self.resolve(column)

    def items(self):
        if self.row is not None:
            return self.row.items()
        return ()

    def get_cell(self, table, column):
        del table
        return self.resolve(column)


class ProductReader:
    def __init__(self, table_names, rows):
        self._names = table_names
        self._rows = rows

    def rowid(self):
        return sum((row.rowid for row in self._rows), ())

    def get(self, table, column):
        if isinstance(table, str):
            table = exp.to_table(table)
        idx = self._names.index(table)
        return self._rows[idx][column]


class Context:
    """
    Encoding context for sql expressions.
    Context is used to hold relevant data tables which can then be queried on with eval.
    References to columns can either be scalar or vectors. When set_row is used, column references
    evaluate to scalars while set_range evaluates to vectors. This allows convenient and efficient
    evaluation of aggregation functions.
    """

    def __init__(
        self, tables: Dict[exp.Table | str, DerivedSchema], external: Optional[Context] = None
    ) -> None:
        """
        Args
            tables: representing the scope of the current execution context.
            env: dictionary of functions within the execution context.
        """
        self.tables = tables
        self._table: Optional[DerivedSchema] = None
        self.range_readers = {
            name: table.range_reader for name, table in self.tables.items()
        }

        self.row_readers = {name: table.reader for name, table in tables.items()}

        self.external = external
        self.masks = set()
        
    @property
    def table(self):
        if self._table is None:
            self._table = list(self.tables.values())[0]
        return self._table

    def add_columns(self, *columns: exp.Identifier | str) -> None:
        for table in self.tables.values():
            table.add_columns(*columns)

    @property
    def columns(self) -> Tuple:
        return self.table.columns

    def resolve_table(self, table: exp.Table | str) -> DerivedSchema:
        if isinstance(table, str):
            table = exp.to_table(table)
        if table in self.tables:
            return self.tables[table]
        if self.external:
            return self.external.resolve_table(table)
        raise KeyError(f"Table '{table.sql()}' not found in scope chain.")

    def resolve_reader(self, table: exp.Table | str):
        if isinstance(table, str):
            table = exp.to_table(table)
        if table in self.row_readers:
            return self.row_readers[table]
        if self.external:
            return self.external.resolve_reader(table)
        raise KeyError(f"Reader '{table.sql()}' not found.")

    def __contains__(self, table: exp.Table | str) -> bool:
        if isinstance(table, str):
            table = exp.to_table(table)
        return table in self.tables

    def __iter__(self):
        for i in range(len(self.table.rows)):
            for table in self.tables.values():
                reader = table[i]
            yield reader, self

    def table_iter(self, table: exp.Table | str) -> TableIter:
        if isinstance(table, str):
            table = exp.to_table(table)
        return iter(self.tables[table])

    def set_mask(self, rowid) -> None:
        self.masks.add(rowid)

    def iters(self, mask=True):
        names = list(self.tables.keys())
        tables = [self.tables[name].rows for name in names]
        for rows in product(*tables):
            reader = ProductReader(names, rows)
            if mask and reader.rowid() in self.masks:
                continue
            yield reader


def build_context_from_instance(instance: Instance) -> Context:
    tables = {}
    for table_name, columns in instance.tables.items():
        table_node = instance.resolve_table(table_name)
        rows = instance.get_rows(table_node)
        derived_columns = []
        datatypes = {}
        uniques = {}
        nullables = {}
        for col_name in columns:
            col_ident = instance.resolve_column(table_node, col_name)
            dtype = instance.get_column_type(table_node, col_ident)
            is_unique = instance.is_unique(table_node, col_ident)
            nullable = instance.nullable(table_node, col_ident)
            datatypes[col_ident] = dtype
            uniques[col_ident] = is_unique
            nullables[col_ident] = nullable
            derived_columns.append(col_ident)
        tables[table_node] = DerivedSchema(
            columns=derived_columns,
            rows=rows,
            datatypes=datatypes,
            uniqueness=uniques,
            nullables=nullables,
        )
    return Context(tables=tables)
