"""Schema hints stamped on ``exp.Column`` nodes (legacy planner enrichment)."""

from __future__ import annotations

from typing import Optional

from sqlglot import exp


def column_meta(col: exp.Column) -> Optional[dict]:
    """Read schema hints stamped on a Column.

    Returns a dict with keys ``table``, ``nullable``, ``unique``, ``domain``,
    or ``None`` if the column was not enriched.
    """
    raw = col.args.get("_parseval_meta")
    if raw is None:
        return None
    return dict(raw)


def set_column_meta(col: exp.Column, meta: dict) -> None:
    """Stamp schema hints onto a Column node.

    Internally stored as a frozenset of ``(key, value)`` pairs so the
    Column remains hashable.
    """
    col.set("_parseval_meta", frozenset(meta.items()))


__all__ = ["column_meta", "set_column_meta"]
