from typing import Dict, List, Any, Optional, Tuple
from dataclasses import dataclass

from .dtype import DATATYPE


@dataclass(frozen=True)
class Column:
    """Represents a column in the logical plan"""

    name: str
    DATATYPE: DATATYPE
    nullable: bool = True
    unique: bool = False
    default_value: Optional[Any] = None
    table_alias: Optional[str] = None

    @property
    def qualified_name(self) -> str:
        return f"{self.table_alias}.{self.name}" if self.table_alias else self.name

    def __str__(self):
        return self.qualified_name


@dataclass(frozen=True)
class Schema:
    """Represents the schema of a logical plan operator"""

    columns: List[Column]

    def get_column(self, name: str) -> Optional[Column]:
        for col in self.columns:
            if col.name == name or col.qualified_name == name:
                return col
        return None

    def column_names(self) -> List[str]:
        return [col.name for col in self.columns]


class Table: ...


class Catalog:
    # function_name -> metadata
    functions: Dict[str, Dict[str, Any]] = {
        "COUNT": {"is_aggregate": True, "return_type": DATATYPE.INT},
        "SUM": {"is_aggregate": True, "return_type": DATATYPE.FLOAT},
        "AVG": {"is_aggregate": True, "return_type": DATATYPE.FLOAT},
        "MIN": {"is_aggregate": True, "return_type": DATATYPE.UNKNOWN},
        "MAX": {"is_aggregate": True, "return_type": DATATYPE.UNKNOWN},
        "UPPER": {"is_aggregate": False, "return_type": DATATYPE.TEXT},
        "LOWER": {"is_aggregate": False, "return_type": DATATYPE.TEXT},
        "LENGTH": {"is_aggregate": False, "return_type": DATATYPE.INT},
    }

    def __init__(self, tables: Dict[str, Table] = None):
        self.tables: Dict[str, Any] = tables or {}

    def register_table(self, table_info: Table):
        """Register a table in the catalog"""
        self.tables[table_info.name] = table_info

    def get_table(self, name: str) -> Optional[Table]:
        """Get table information by name"""
        return self.tables.get(name)

    def resolve_column(
        self, column_name: str, table_context: Optional[str] = None
    ) -> Optional[Column]:
        """Resolve a column reference to its full information"""
        if table_context:
            table = self.get_table(table_context)
            if table:
                return table.get_column(column_name)

        # If no table context, search all tables (ambiguous if found in multiple)
        matches = []
        for table in self.tables.values():
            col = table.get_column(column_name)
            if col:
                matches.append(col)

        if len(matches) == 1:
            return matches[0]
        elif len(matches) > 1:
            raise ValueError(f"Ambiguous column reference: {column_name}")

        return None

    def get_function_info(self, func_name: str) -> Optional[Dict[str, Any]]:
        """Get function metadata"""
        return self.functions.get(func_name.upper())

    def __str__(self):
        return f"Catalog(tables={list(self.tables.keys())})"
