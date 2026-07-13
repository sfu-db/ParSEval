from __future__ import annotations

import json
import time
import unittest
from pathlib import Path

from sqlglot import exp, parse_one

from parseval.dtype import DataType
from parseval.solver.csp import CspBackend
from parseval.solver.types import Problem, SolverVar


DATASET = Path("data/sqlite/dev.json")


def _dataset_sql(question_id: int) -> str:
    rows = json.loads(DATASET.read_text())
    for row in rows:
        if row["question_id"] == question_id:
            return row["SQL"]
    raise AssertionError(f"question_id {question_id} not found in {DATASET}")


def _flatten_and(expr: exp.Expression) -> list[exp.Expression]:
    if isinstance(expr, exp.And):
        return _flatten_and(expr.this) + _flatten_and(expr.expression)
    return [expr]


def _temporal_atoms(sql: str) -> list[exp.Expression]:
    query = parse_one(sql, read="sqlite")
    where = query.args.get("where")
    if where is None:
        return []
    atoms = []
    for atom in _flatten_and(where.this):
        if (
            atom.find(exp.TimeToStr) is not None
            or atom.find(exp.Date) is not None
            or any(
                isinstance(node, exp.Anonymous) and str(node.name).upper() == "STRFTIME"
                for node in atom.find_all(exp.Anonymous)
            )
        ):
            atoms.append(atom)
    return atoms


def _solverize_temporal_columns(atom: exp.Expression) -> exp.Expression:
    """Replace columns with SolverVars and insert DF-style CAST AS TEXT under strftime."""

    def replace(node: exp.Expression) -> exp.Expression:
        if isinstance(node, exp.Column):
            return SolverVar(key=node.sql(), dtype=DataType.build("DATETIME"))
        return node

    rewritten = atom.copy().transform(replace)

    def wrap_strftime_arg(node: exp.Expression) -> exp.Expression:
        if isinstance(node, exp.TimeToStr) and isinstance(node.this, SolverVar):
            return exp.TimeToStr(
                this=exp.Cast(this=node.this, to=DataType.build("TEXT")),
                format=node.args.get("format"),
            )
        if isinstance(node, exp.Anonymous) and str(node.name).upper() == "STRFTIME":
            args = list(node.expressions)
            if len(args) == 2 and isinstance(args[1], SolverVar):
                return exp.Anonymous(
                    this=node.this,
                    expressions=[
                        args[0],
                        exp.Cast(this=args[1], to=DataType.build("TEXT")),
                    ],
                )
        return node

    return rewritten.transform(wrap_strftime_arg)


class CspSqliteDatasetTemporalTests(unittest.TestCase):
    def test_real_sqlite_temporal_predicates_solve_quickly(self):
        question_ids = [
            27,  # two STRFTIME year inequalities
            66,  # STRFTIME year BETWEEN
            532,  # StackOverflow-style CreationDate year equality
            533,  # date(LastAccessDate) range predicate
            536,  # STRFTIME year greater-than with another filter
        ]
        constraints = []
        for question_id in question_ids:
            sql = _dataset_sql(question_id)
            constraints.extend(
                _solverize_temporal_columns(atom) for atom in _temporal_atoms(sql)
            )

        self.assertGreaterEqual(len(constraints), 6)

        backend = CspBackend()
        start = time.perf_counter()
        results = [backend.solve(Problem(constraints=[constraint])) for constraint in constraints]
        elapsed = time.perf_counter() - start

        self.assertLess(elapsed, 1.0)
        self.assertTrue(all(result.status == "sat" for result in results), results)
        for result in results:
            self.assertTrue(result.assignments)
            for value in result.assignments.values():
                self.assertIsNotNone(value)


if __name__ == "__main__":
    unittest.main()
