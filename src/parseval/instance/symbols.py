"""Symbol index for :class:`parseval.instance.Instance`.

Indexes :class:`~parseval.plan.rex.Variable` cells by name and by
sqlglot ``(table, column)`` identity — not ``parseval.identity`` types.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, Iterable, Iterator, List, Optional, Tuple

from sqlglot import exp

from parseval.plan.rex import Variable

from .schema import table_key


def _column_key(variable: Variable) -> Tuple[str, str]:
    table = variable.table
    column = variable.column
    tname = table_key(table) if isinstance(table, exp.Table) else (variable.table_name or "")
    cname = column.name if isinstance(column, exp.Identifier) else variable.column_name
    return (tname, cname)


class SymbolIndex:
    __slots__ = ("_by_name", "_by_column", "_by_row")

    def __init__(self) -> None:
        self._by_name: Dict[str, Variable] = {}
        self._by_column: Dict[Tuple[str, str], List[Variable]] = defaultdict(list)
        self._by_row: Dict[Tuple[str, Any], List[Variable]] = defaultdict(list)

    def register(self, variable: Variable) -> None:
        name = variable.name
        existing = self._by_name.get(name)
        if existing is variable:
            return
        if existing is not None:
            self._remove_from_reverse_indices(existing)
        self._by_name[name] = variable

        col_key = _column_key(variable)
        self._by_column[col_key].append(variable)
        table_name = col_key[0]
        self._by_row[(table_name, variable.rowid)].append(variable)

    def register_many(self, variables: Iterable[Variable]) -> None:
        for variable in variables:
            self.register(variable)

    def unregister(self, name: str) -> Optional[Variable]:
        removed = self._by_name.pop(name, None)
        if removed is not None:
            self._remove_from_reverse_indices(removed)
        return removed

    def _remove_from_reverse_indices(self, variable: Variable) -> None:
        col_key = _column_key(variable)
        bucket = self._by_column.get(col_key)
        if bucket is not None:
            if variable in bucket:
                bucket.remove(variable)
            if not bucket:
                self._by_column.pop(col_key, None)
        row_key = (col_key[0], variable.rowid)
        bucket = self._by_row.get(row_key)
        if bucket is not None:
            if variable in bucket:
                bucket.remove(variable)
            if not bucket:
                self._by_row.pop(row_key, None)

    def clear(self) -> None:
        self._by_name.clear()
        self._by_column.clear()
        self._by_row.clear()

    def by_name(self, name: str) -> Optional[Variable]:
        return self._by_name.get(name)

    def by_column(
        self, table: exp.Table | str, column: exp.Identifier | str
    ) -> List[Variable]:
        tname = table_key(table) if isinstance(table, exp.Table) else str(table)
        cname = column.name if isinstance(column, exp.Identifier) else str(column)
        return list(self._by_column.get((tname, cname), ()))

    def by_row(self, table: exp.Table | str, rowid: Any) -> List[Variable]:
        tname = table_key(table) if isinstance(table, exp.Table) else str(table)
        return list(self._by_row.get((tname, rowid), ()))

    def __getitem__(self, name: str) -> Variable:
        return self._by_name[name]

    def __setitem__(self, name: str, variable: Variable) -> None:
        if variable.name != name:
            variable.set("this", name)
        self.register(variable)

    def __contains__(self, name: str) -> bool:
        return name in self._by_name

    def __iter__(self) -> Iterator[Variable]:
        return iter(self._by_name.values())

    def __len__(self) -> int:
        return len(self._by_name)

    def get(self, name: str, default: Optional[Variable] = None) -> Optional[Variable]:
        return self._by_name.get(name, default)

    def names(self) -> List[str]:
        return list(self._by_name.keys())

    def values(self) -> List[Variable]:
        return list(self._by_name.values())

    def items(self) -> List[Tuple[str, Variable]]:
        return list(self._by_name.items())


__all__ = ["SymbolIndex"]
