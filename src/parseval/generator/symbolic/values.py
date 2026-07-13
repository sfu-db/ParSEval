"""Detection utilities for the symbolic pipeline.

These functions check whether existing Instance rows satisfy or violate
conditions.  Row generation is delegated to the Solver (via operator steps).
"""

from __future__ import annotations

from itertools import product
from typing import Any, Dict, List, Optional, Tuple

from sqlglot import exp

from parseval.instance import Instance
from parseval.plan.rex import Environment, Variable, concrete


def _row_value_dict(row) -> Dict[exp.Identifier, Any]:
    """Extract concrete {col_ident: value} from a Row/Variable row."""
    d: Dict[exp.Identifier, Any] = {}
    for col_ident, val in row.column_values.items():
        if isinstance(val, Variable):
            d[col_ident] = val.concrete
        else:
            d[col_ident] = val
    return d


def existing_domain(
    instance: Instance, table: exp.Table, column: exp.Identifier
) -> List[Any]:
    """Return all distinct concrete values for *column* in *table*."""
    values: List[Any] = []
    for row in instance.get_rows(table):
        row_dict = _row_value_dict(row)
        val = row_dict.get(column)
        if val is not None:
            values.append(val)
    return values


def has_row_satisfying(
    instance: Instance,
    table: exp.Table,
    conjuncts: List[exp.Expression],
) -> bool:
    """True if any existing row in *table* satisfies ALL *conjuncts*."""
    for row in instance.get_rows(table):
        env = Environment.from_row(row)
        ok = True
        for atom in conjuncts:
            if concrete(atom, env) is not True:
                ok = False
                break
        if ok:
            return True
    return False


def has_row_violating(
    instance: Instance,
    table: exp.Table,
    conjuncts: List[exp.Expression],
    atom_index: int,
) -> bool:
    """True if any row fails *conjuncts[atom_index]* but passes all others."""
    for row in instance.get_rows(table):
        env = Environment.from_row(row)
        for j, atom in enumerate(conjuncts):
            val = concrete(atom, env)
            if j == atom_index:
                if val is not False:
                    break
            else:
                if val is not True:
                    break
        else:
            return True
    return False


def has_row_with_null_outcome(
    instance: Instance,
    table: exp.Table,
    condition: exp.Expression,
) -> bool:
    """True if any row makes the *condition* evaluate to NULL."""
    for row in instance.get_rows(table):
        env = Environment.from_row(row)
        if concrete(condition, env) is None:
            return True
    return False


def has_matching_pair(
    instance: Instance,
    left_table: exp.Table,
    right_table: exp.Table,
    on_keys: List[Tuple[exp.Expression, exp.Expression]],
    condition: Optional[exp.Expression] = None,
) -> bool:
    """True if a cross-product pair satisfies all ON equalities + condition."""
    left_rows = instance.get_rows(left_table)
    right_rows = instance.get_rows(right_table)
    for lrow, rrow in product(left_rows, right_rows):
        merged = {}
        merged.update(_row_value_dict(lrow))
        merged.update(_row_value_dict(rrow))
        env = Environment(row=merged)
        ok = True
        for lexpr, rexpr in on_keys:
            lv = concrete(lexpr, env)
            rv = concrete(rexpr, env)
            if lv is None or rv is None or lv != rv:
                ok = False
                break
        if ok and condition is not None:
            if concrete(condition, env) is not True:
                ok = False
        if ok:
            return True
    return False


def has_non_matching_row(
    instance: Instance,
    side_table: exp.Table,
    other_table: exp.Table,
    on_keys: List[Tuple[exp.Expression, exp.Expression]],
) -> bool:
    """True if a row on *side_table* has no matching partner on *other_table*."""
    other_rows = instance.get_rows(other_table)
    for srow in instance.get_rows(side_table):
        sdict = _row_value_dict(srow)
        matches = False
        for orow in other_rows:
            odict = _row_value_dict(orow)
            merged = {**sdict, **odict}
            env = Environment(row=merged)
            match = True
            for lexpr, rexpr in on_keys:
                lv = concrete(lexpr, env)
                rv = concrete(rexpr, env)
                if lv is None or rv is None or lv != rv:
                    match = False
                    break
            if match:
                matches = True
                break
        if not matches:
            return True
    return False
