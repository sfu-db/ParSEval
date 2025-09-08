
from __future__ import annotations
from dataclasses import dataclass, field, asdict, fields
import ast, re, z3, sqlglot, random, logging
from sqlglot import expressions as exp
from collections.abc import Iterator
from collections import OrderedDict
import typing as t
from parseval.symbol import Term
from sqlglot.schema import normalize_name

from .connection import Connection

logger = logging.getLogger('app')

_TP = t.TypeVar("_TP", bound=t.Tuple[Term, ...])

class RowIterator(Iterator):
    _position: int = None
    _reverse: bool = False
    def __init__(self, collection: Table, reverse: bool = False) -> None:
        super().__init__()
        self._collection = collection
        self._reverse = reverse
        self._position = -1 if reverse else 0
    def __next__(self) -> t.Any:
        try:
            value = self._collection[self._position]
            self._position += -1 if self._reverse else 1
        except IndexError:
            raise StopIteration()
        return value


def _create_row(
    values: t.Union[t.Tuple[t.Any, ...], t.List[t.Any]], constraints: t.Set
) -> Row:
    row = Row(values)
    row._constraints.update(constraints)
    return row

class Row(t.Tuple[t.Any], t.Generic[_TP]):
    _data : t.Tuple[Term]
    def __init__(self, *args: t.Optional[t.Any]) -> None:
        super().__init__()
        self._data = tuple(*args)
        self.constraints: t.Set = set()

    def __bool__(self):
        return len(self._data) > 0

    def asList(self) -> t.List[Term]:
        """
            Return as a List
        """
        return list(self._data)

    def __add__(self, value: Row) -> Row:
        return _create_row(values= tuple(self._data) + tuple(value._data), constraints= self.constraints.union(value.constraints))

    def __iter__(self):
        return iter(self._data)

    def __repr__(self) -> str:
        """Printable representation of Row used in Python REPL."""
        return "<Row(%s); constraints(%s)>" % (", ".join(repr(field) for field in self), self.constraints)

    def _tuple(self) -> _TP:
        return self._data

class Table:
    _key = 'TABLE'
    
    @classmethod
    def from_ddl(cls, ddl: exp.Expression) -> Table:
        assert ddl.kind == 'TABLE', f'Cannot initialize database instance based on ddl : {ddl}'
        assert isinstance(ddl, exp.Create), f'Cannot initialize database instance based on ddl : {ddl}'
        schema_obj = ddl.this
        primary_key = []
        foreign_keys = []
        columns = []
        for column_def in schema_obj.expressions:
            if isinstance(column_def, exp.ColumnDef):
                for constraint in column_def.constraints[:]:
                    if isinstance(constraint.kind, exp.PrimaryKeyColumnConstraint):
                        column_def.constraints.remove(constraint)
                        if column_def.this not in primary_key: primary_key.append(column_def.this)
                    elif isinstance(constraint.kind, exp.AutoIncrementColumnConstraint):
                        column_def.constraints.remove(constraint)
                columns.append(column_def)
            elif isinstance(column_def, exp.PrimaryKey):
                primary_key.extend([item for item in column_def.expressions if item not in primary_key])
            elif isinstance(column_def, exp.ForeignKey):
                foreign_keys.append(column_def)
        pk = exp.PrimaryKey(expressions = list(primary_key))
        return cls(name = schema_obj.this, columns = tuple(columns), primary_key = pk, foreign_keys = foreign_keys)

    def __init__(self, name: str|exp.Identifier, columns: t.Tuple[exp.ColumnDef], primary_key: exp.PrimaryKey | None = None, data: t.List[Row] = None, **kw) -> None:
        self._name = normalize_name(name)
        self._columns: t.Dict[str, exp.ColumnDef] = OrderedDict()
        for column in columns:
            self._columns[column.name] = column
            column.constraints
        self._data = data if data is not None else []
        self._primary_key: exp.PrimaryKey = primary_key if primary_key is not None else exp.PrimaryKey(expressions = [])
        self.foreign_keys: t.List[exp.ForeignKey] = kw.get('foreign_keys', [])


    @property
    def primary_key(self):
        return self._primary_key

    @property
    def name(self):
        return self._name.name

    @property
    def shape(self):
        return (len(self._data), len(self._columns))
    
    def __getitem__(self, index :int) -> Row:
        return self._data[index]
    
    def __iter__(self):
        return RowIterator(self)
    
    def get_column_data(self, column_name) -> t.List[Term]:
        for index, current_key in enumerate(self._columns.keys()):
            if current_key == column_name:
                return [row[index] for row in self._data]
        raise KeyError(f"Column name '{column_name}' not found in table {self.name}.")
    
    def get_column(self, column_name) -> exp.ColumnDef:
        '''return ColumnDef named `column_name` '''
        return self._columns.get(column_name)
    
    def is_notnull(self, column_name: str) -> bool:
        column_def = self._columns[column_name]
        if any(isinstance(constraint.kind, (exp.NotNullColumnConstraint, exp.PrimaryKeyColumnConstraint)) for constraint in column_def.constraints):
            return True
        if column_def.this in self._primary_key.expressions:
            return True
        if any(column_def.this in k.expressions for k in self.foreign_keys):
                return True
        return False

    def is_unique(self, column_name: str) -> bool:
        column_def = self._columns[column_name]
        if any(isinstance(constraint.kind, (exp.UniqueColumnConstraint, exp.PrimaryKeyColumnConstraint)) for constraint in column_def.constraints):
            return True
        if column_def.this in self._primary_key.expressions:
            return True
        return False

    def append(self, row: Row):
        row.row_id = len(self._data)
        self._data.append(row)

    def reset(self):
        self._data.clear()

    def to_data(self):
        records = [[column_name for column_name in self._columns.keys()]]
        for data in self._data:
            records.append([d.to_db() for d in data])
        return records

    def sql(self, dialect = None) -> str:
        exps = [v for _, v in self._columns.items()]
        if self._primary_key and self._primary_key.expressions: exps.append(self._primary_key)
        exps.extend(self.foreign_keys)        
        # exp.parse_identifier
        # exp.maybe_parse(self.name, dialect= dialect, q)
        
        obj = exp.Create(this = exp.Schema(this = exp.Table(this = exp.parse_identifier(str(self._name))), expressions = exps), exists = True, kind = 'TABLE')
        return obj.sql(dialect= dialect)



    


    