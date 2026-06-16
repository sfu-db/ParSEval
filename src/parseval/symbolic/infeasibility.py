"""Quick infeasibility checks to avoid wasting solver time.

These are cheap, static checks that can determine a branch target is
impossible without invoking the SMT solver. The engine calls
:func:`is_infeasible` before generating constraints; if it returns a
reason string, the target is marked infeasible and skipped.
"""

from __future__ import annotations

from typing import Optional

from sqlglot import exp

from parseval.instance import Instance

from .types import BranchNode, BranchType


def is_infeasible(
    node: BranchNode,
    atom_id: int,
    target_outcome: BranchType,
    instance: Instance,
) -> Optional[str]:
    """Return a reason string if the target is obviously infeasible, else None."""
    atom = node.atoms[atom_id]
    atom_sql = atom.sql().upper().strip()

    # 1. IS NULL / IS NOT NULL are inherently 2VL — they never produce NULL.
    if target_outcome == BranchType.ATOM_NULL:
        if "IS NULL" in atom_sql or "IS NOT NULL" in atom_sql:
            return "IS [NOT] NULL predicates are inherently 2VL; cannot produce NULL"

    # 2. Tautologies: literal TRUE/FALSE predicates.
    if target_outcome == BranchType.ATOM_FALSE and atom_sql in ("TRUE", "1 = 1", "1"):
        return "Tautology: predicate is always TRUE"
    if target_outcome == BranchType.ATOM_TRUE and atom_sql in ("FALSE", "1 = 0", "0"):
        return "Contradiction: predicate is always FALSE"

    # 3. ATOM_NULL targeting a column that is NOT NULL in all relevant tables.
    if target_outcome == BranchType.ATOM_NULL:
        columns = list(atom.find_all(exp.Column))
        if columns:
            def _table_for_col(col):
                if col.table:
                    return col.table
                if node.tables:
                    rel = node.tables[0]
                    if isinstance(rel, str):
                        return rel
                    return rel.name.normalized if rel.name is not None else rel.display
                return ""

            all_not_null = all(
                not instance.nullable(
                    _table_for_col(col),
                    col.name,
                )
                for col in columns
                if _table_for_col(col) in instance.tables
            )
            if all_not_null and columns:
                return "All columns in atom are NOT NULL; atom cannot evaluate to NULL"

    return None


__all__ = ["is_infeasible"]
