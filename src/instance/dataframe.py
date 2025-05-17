from __future__ import annotations
from collections.abc import Iterator
from sqlglot import exp
from typing import Union, Tuple, List , Any

class RowIterator(Iterator):
    _position: int = None
    _reverse: bool = False
    def __init__(self, collection: DataFrame, reverse: bool = False) -> None:
        super().__init__()
        self._collection = collection
        self._reverse = reverse
        self._position = -1 if reverse else 0
    def __next__(self) -> Any:
        try:
            value = self._collection[self._position]
            self._position += -1 if self._reverse else 1
        except IndexError:
            raise StopIteration()
        return value


class DataFrame:
    @classmethod
    def create(cls, stmt: exp.Expression):
        assert isinstance(stmt, exp.Create), f'Cannot initialize database instance based on ddl : {stmt}'
        schema_obj = stmt.this
        primary_key = []
        foreign_keys = []
        column_defs = []
        for column_def in schema_obj.expressions:
            if isinstance(column_def, exp.ColumnDef):
                for constraint in column_def.constraints[:]:
                    if isinstance(constraint.kind, exp.PrimaryKeyColumnConstraint):
                        column_def.constraints.remove(constraint)
                        if column_def.this not in primary_key: primary_key.append(column_def.this)
                    elif isinstance(constraint.kind, exp.AutoIncrementColumnConstraint):
                        column_def.constraints.remove(constraint)
                column_defs.append(column_def)
            elif isinstance(column_def, exp.PrimaryKey):
                primary_key.extend([item for item in column_def.expressions if item not in primary_key])
            elif isinstance(column_def, exp.ForeignKey):
                foreign_keys.append(column_def)
        pk = exp.PrimaryKey(expressions = list(primary_key))
        return cls(name = schema_obj.this.name, column_defs = tuple(column_defs), primary_key = pk, foreign_keys = foreign_keys)

    def __init__(self, name: str, column_defs: Tuple[exp.ColumnDef] | None = None, **kwargs) -> None:
        self.name = name
        self.column_defs: Tuple[exp.ColumnDef] = column_defs if column_defs is not None else []
        self.tuples : List = []
        self.primary_key: exp.PrimaryKey = kwargs.get('primary_key', exp.PrimaryKey(expressions = []))
        self.foreign_keys: List[exp.ForeignKey] = kwargs.get('foreign_keys', [])
        self.check_constraints: List[exp.Check] = kwargs.get('check_constraints', [])
    
    @property
    def shape(self) -> Tuple[int, int]:
        return len(self.tuples), len(self.column_defs)
    
    def __getitem__(self, index :int) -> List[Any]:
        return self.tuples[index]
    
    def __iter__(self):
        return RowIterator(self)
    
    def get_column(self, column: Union[str, int]) -> exp.ColumnDef:
        '''return Column named `column_name` '''
        if isinstance(column, int):
            return self.column_defs[column]
        for c in self.column_defs:
            if c.name == column:
                return c
        raise RuntimeError(f'There is no columns named {column} in table {self.name}')

    def is_primarykey(self, column_name: Union[str, exp.ColumnDef]):
        column_def = self.get_column(column_name) if isinstance(column_name, str) else column_name
        if column_def.this in self.primary_key.expressions:
            return True
        return False

    def is_foreignkey(self, column_name: Union[str, exp.ColumnDef]):
        column_def = self.get_column(column_name) if isinstance(column_name, str) else column_name
        if any(column_def.this in k.expressions for k in self.foreign_keys):
            return True
        return False

    def is_notnull(self, column_name: Union[str, exp.ColumnDef]) -> bool:
        column_def = self.get_column(column_name) if isinstance(column_name, str) else column_name
        if self.is_primarykey(column_def) or self.is_foreignkey(column_def):
            return True
        if any(isinstance(constraint.kind, (exp.NotNullColumnConstraint, exp.PrimaryKeyColumnConstraint)) for constraint in column_def.constraints):
            return True
        return False

    def is_unique(self, column_name: Union[str, exp.ColumnDef]) -> bool:
        column_def = self.get_column(column_name) if isinstance(column_name, str) else column_name
        if self.is_primarykey(column_def):
            return True
        if any(isinstance(constraint.kind, (exp.UniqueColumnConstraint, exp.PrimaryKeyColumnConstraint)) for constraint in column_def.constraints):
            return True
        return False
    
    def get_column_index(self, column: Union[str, exp.Identifier]) -> int:
        for cidx, column_def in enumerate(self.column_defs):
            if str(column) == column_def.name:
                return cidx
        raise RuntimeError(f'Could not find column named {column} in table {self.name}')

    def get_column_data(self, column: Union[str, exp.Identifier]) -> List[Any]:
        cidx = self.get_column_index(column)
        return [row[cidx] for row in self.tuples]

    def __repr__(self):
        return f"Table(name={self.name}, columns={self.column_defs}, primary_key={self.primary_key}, foreign_keys={self.foreign_keys})"

    def __str__(self) -> str:
        column_names = [col.name for col in self.column_defs]
        rows = [", ".join(map(str, row)) for row in self.tuples]
        return f"Table: {self.name}\nColumns: {', '.join(column_names)}\nRows:\n" + "\n".join(rows)