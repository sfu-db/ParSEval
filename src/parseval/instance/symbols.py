"""Symbol index for :class:`parseval.instance.Instance`.

An Instance needs a small amount of bookkeeping over the :class:`Variable`
objects it hands out: solvers look them up by their stable name, column
scans fetch every cell for a ``(table, column)`` pair, and row-level
operations walk every cell in a ``(table, rowid)`` tuple. Pre-refactor
the Instance kept five loose dicts for this — two used, three dead. This
module consolidates the live bookkeeping into one :class:`SymbolIndex`.

``SymbolIndex`` leans on the back-pointers every :class:`Variable`
carries (``table`` / ``column`` / ``rowid``) so reverse indices are
populated automatically at :meth:`register` time. Callers that used to
treat the Instance's ``symbols`` attribute as a dict keep working via
``__getitem__`` / ``__contains__`` / iteration; the richer lookups are
available as explicit methods.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, Iterable, Iterator, List, Optional, Tuple

from parseval.plan.rex import Variable


class SymbolIndex:
    """Bookkeeping layer over the :class:`Variable` cells an Instance owns.

    Invariants:
    * each Variable is registered exactly once per unique name; registering
      the same name twice updates the entry rather than duplicating it;
    * reverse indices (by column / by row) derive from the Variable's own
      ``table`` / ``column`` / ``rowid`` back-pointers — they're always
      consistent with the primary name index.
    """

    __slots__ = ("_by_name", "_by_column", "_by_row")

    def __init__(self) -> None:
        self._by_name: Dict[str, Variable] = {}
        self._by_column: Dict[Tuple[str, str], List[Variable]] = defaultdict(list)
        self._by_row: Dict[Tuple[str, Any], List[Variable]] = defaultdict(list)

    # ------------------------------------------------------------------
    # mutation
    # ------------------------------------------------------------------

    def register(self, variable: Variable) -> None:
        """Register ``variable`` under its stable name and reverse indices.

        Reverse indices are populated only when the Variable carries the
        corresponding back-pointer. This keeps index size proportional to
        the information actually available rather than padding missing
        fields with ``None``-keyed buckets.
        """
        name = variable.name
        existing = self._by_name.get(name)
        if existing is variable:
            return
        if existing is not None:
            self._remove_from_reverse_indices(existing)
        self._by_name[name] = variable

        table = variable.args.get("table")
        column = variable.args.get("column")
        rowid = variable.args.get("rowid")
        if table and column:
            self._by_column[(table, column)].append(variable)
        if table and rowid is not None:
            self._by_row[(table, rowid)].append(variable)

    def register_many(self, variables: Iterable[Variable]) -> None:
        for variable in variables:
            self.register(variable)

    def unregister(self, name: str) -> Optional[Variable]:
        """Remove a Variable by name; return it, or ``None`` if absent."""
        removed = self._by_name.pop(name, None)
        if removed is not None:
            self._remove_from_reverse_indices(removed)
        return removed

    def _remove_from_reverse_indices(self, variable: Variable) -> None:
        table = variable.args.get("table")
        column = variable.args.get("column")
        rowid = variable.args.get("rowid")
        if table and column:
            bucket = self._by_column.get((table, column))
            if bucket is not None:
                try:
                    bucket.remove(variable)
                except ValueError:
                    pass
                if not bucket:
                    self._by_column.pop((table, column), None)
        if table and rowid is not None:
            bucket = self._by_row.get((table, rowid))
            if bucket is not None:
                try:
                    bucket.remove(variable)
                except ValueError:
                    pass
                if not bucket:
                    self._by_row.pop((table, rowid), None)

    def clear(self) -> None:
        self._by_name.clear()
        self._by_column.clear()
        self._by_row.clear()

    # ------------------------------------------------------------------
    # primary lookup
    # ------------------------------------------------------------------

    def by_name(self, name: str) -> Optional[Variable]:
        """Return the :class:`Variable` registered under ``name``, or ``None``."""
        return self._by_name.get(name)

    def by_column(self, table: str, column: str) -> List[Variable]:
        """Return every cell registered for ``table.column``, in insertion order."""
        return list(self._by_column.get((table, column), ()))

    def by_row(self, table: str, rowid: Any) -> List[Variable]:
        """Return every cell registered for ``table`` at ``rowid``, in order."""
        return list(self._by_row.get((table, rowid), ()))

    # ------------------------------------------------------------------
    # dict-style ergonomics (for legacy call sites)
    # ------------------------------------------------------------------

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
