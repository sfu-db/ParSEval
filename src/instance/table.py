from __future__ import annotations
from collections.abc import Iterator
from src.expression.symbol import Row
from collections import defaultdict
from sqlglot import exp
from typing import List, Any, Optional, Union, Tuple, Iterator

class RowIterator(Iterator):
    _position: int = None
    _reverse: bool = False
    def __init__(self, collection: Table, reverse: bool = False) -> None:
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


class Table:
    """
    A class representing a database table with columns and rows.
    
    This class manages the structure of a database table, including column definitions,
    constraints, and the actual data rows.
    """
    def __init__(self, name: str, column_defs: Optional[Tuple[exp.ColumnDef, ...]] = None, **kwargs) -> None:
        self.name = name
        self.column_defs: Tuple[exp.ColumnDef, ...] = column_defs if column_defs is not None else ()
        self._column_index = {c.name: i for i, c in enumerate(self.column_defs)}
        self.tuples: List[Row] = []
        self.primary_key: exp.PrimaryKey = kwargs.get('primary_key', exp.PrimaryKey(expressions=[]))
        self.foreign_keys: List[exp.ForeignKey] = kwargs.get('foreign_keys', [])
        self.check_constraints: List[exp.Check] = kwargs.get('check_constraints', [])

        self.stmt = kwargs.get('stmt', None)


    @classmethod
    def create(cls, stmt: exp.Expression):
        """
        Create a new Table instance from a CREATE TABLE statement.
        
        Args:
            stmt: A SQLGlot CREATE TABLE expression
            
        Returns:
            A new Table instance
            
        Raises:
            ValueError: If the statement is invalid or contains unsupported features
            RuntimeError: If there are constraint violations
        """
        if not isinstance(stmt, exp.Create):
            raise ValueError(f'Expected CREATE statement, got {type(stmt).__name__}')
        
        schema_obj = stmt.this
        table_name = schema_obj.this.name
        table_components = {
            'column_defs': [],
            'primary_key': set(),
            'foreign_keys': [],
            'check_constraints': [],
            'constraints': defaultdict(list),
            'column_names': set()  # For uniqueness validation
        }
        try:
            for expr in schema_obj.expressions:
                if isinstance(expr, exp.ColumnDef):
                    for constraint in expr.constraints[:]:
                        if isinstance(constraint.kind, exp.PrimaryKeyColumnConstraint):
                            table_components['primary_key'].add(expr.this)
                        elif isinstance(constraint.kind, exp.AutoIncrementColumnConstraint):
                            expr.constraints.remove(constraint)
                    table_components['constraints'][expr.name].extend(expr.constraints)
                    table_components['column_defs'].append(expr)
                elif isinstance(expr, exp.PrimaryKey):
                    table_components['primary_key'].update([item for item in expr.expressions])
                elif isinstance(expr, exp.ForeignKey):
                    table_components['foreign_keys'].append(expr)
        except Exception as e:
            raise RuntimeError(f'Error creating table {table_name}: {e}')
        
        pk = exp.PrimaryKey(expressions = list(table_components['primary_key']))
        return cls(name = table_name, column_defs = tuple(table_components['column_defs']), primary_key = pk, foreign_keys = table_components['foreign_keys'], stmt = stmt)

    @property
    def shape(self) -> Tuple[int, int]:
        return len(self.tuples), len(self.column_defs)
    
    def __getitem__(self, index :int) -> List[Any]:
        return self.tuples[index]
    
    def __iter__(self):
        return RowIterator(self)
    
    def get_column(self, column: Union[str, int]) -> exp.ColumnDef:
        """
        Get a column definition by name or index.
        
        Args:
            column: Column name or index
            
        Returns:
            The column definition
            
        Raises:
            ValueError: If the column does not exist
        """
        if isinstance(column, int):
            if 0 <= column < len(self.column_defs):
                return self.column_defs[column]
            raise ValueError(f"Column index {column} out of range for table {self.name}")
        
        for c in self.column_defs:
            if c.name == column:
                return c
        raise ValueError(f"There is no column named {column} in table {self.name}")
    
    def get_column_data(self, column: Union[str, exp.Identifier]) -> List[Any]:
        column_name = column.name  if isinstance(column, exp.Identifier) else column
        cidx = self._column_index[column_name]
        return [row[cidx] for row in self.tuples]
    
    def get_column_index(self, column: Union[str, exp.Identifier]) -> int:
        """
            Get the index of a column by name.
            
            Args:
                column: Column name or identifier
                
            Returns:
                int: Column index
                
            Raises:
                RuntimeError: If the column does not exist
        """
        column_name = column.name  if isinstance(column, exp.Identifier) else column
        return self._column_index[column_name]
    
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
    

    def __repr__(self):
        return f"Table(name={self.name}, columns={self.column_defs}, primary_key={self.primary_key}, foreign_keys={self.foreign_keys})"

    def __str__(self) -> str:
        column_names = [col.name for col in self.column_defs]
        rows = [", ".join(map(str, row)) for row in self.tuples]
        return f"Table: {self.name}\nColumns: {', '.join(column_names)}\nRows:\n" + "\n".join(rows)