from __future__ import annotations

import unittest

from sqlglot import exp

from parseval.plan.context import DerivedSchema, IndicatorVar, Row, is_concrete_row
from parseval.plan.rex import Variable


KEY_DEPT = exp.to_identifier("dept")
KEY_SALARY = exp.to_identifier("salary")
KEY_AVG = exp.to_identifier("dept_avg")


class ContextMetadataTests(unittest.TestCase):
    def test_row_access_by_identifier(self):
        row = Row(this=("employees", 0), columns={KEY_DEPT: "sales", KEY_SALARY: 50})

        self.assertEqual(row[KEY_DEPT], "sales")
        self.assertEqual(row["dept"], "sales")
        self.assertEqual(row.columns, (KEY_DEPT, KEY_SALARY))
        self.assertEqual(dict(row.items()), {KEY_DEPT: "sales", KEY_SALARY: 50})

    def test_derived_schema_basic(self):
        row = Row(this=("employees", 0), columns={KEY_DEPT: "sales", KEY_SALARY: 50})
        schema = DerivedSchema(columns=(KEY_DEPT, KEY_SALARY), rows=[row])

        self.assertEqual(len(schema), 1)
        self.assertEqual(schema.columns, (KEY_DEPT, KEY_SALARY))

    def test_derived_schema_indicators_and_constraints(self):
        row = Row(this=("employees", 0), columns={KEY_DEPT: "sales", KEY_SALARY: 50})
        indicator = IndicatorVar(
            step_id="s1",
            atom_id=0,
            atom_expr=exp.column("dept", table="employees"),
            concrete_value=True,
        )
        constraint = exp.Literal.number(1)

        schema = DerivedSchema(
            columns=(KEY_DEPT, KEY_SALARY),
            rows=[row],
            indicators=[indicator],
            constraints=[constraint],
        )

        self.assertEqual(schema.indicators, [indicator])
        self.assertEqual(schema.constraints, [constraint])
        self.assertTrue(is_concrete_row(row))

    def test_row_reader_resolves_column_via_identifier(self):
        cell = Variable(
            this="employees_0_dept",
            table=exp.to_table("employees"),
            column=exp.to_identifier("dept"),
            rowid=("employees", 0),
        )
        row = Row(this=("employees", 0), columns={KEY_DEPT: cell, KEY_SALARY: 50})
        schema = DerivedSchema(columns=(KEY_DEPT, KEY_SALARY), rows=[row])
        reader = schema[0]

        self.assertIs(reader.resolve(KEY_DEPT), cell)
        col = exp.column("dept")
        self.assertIs(reader.resolve(col), cell)

    def test_derived_schema_with_rows_preserves_indicators_and_constraints(self):
        row1 = Row(this=("employees", 0), columns={KEY_DEPT: "sales", KEY_SALARY: 50})
        row2 = Row(this=("employees", 1), columns={KEY_DEPT: "eng", KEY_SALARY: 80})
        indicator = IndicatorVar(
            step_id="s1",
            atom_id=0,
            atom_expr=exp.column("dept"),
            concrete_value=True,
        )

        schema = DerivedSchema(
            columns=(KEY_DEPT, KEY_SALARY),
            rows=[row1, row2],
            indicators=[indicator],
            constraints=[exp.GT(this=exp.column("salary"), expression=exp.Literal.number(0))],
            equalities=[("left", "right")],
            obligations=[{"id": "filter.true", "status": "covered"}],
            evidence={"filter.true": (row1.rowid,)},
            expression_bindings={"salary_plus_one": exp.column("salary")},
            row_provenance={row1.rowid: {"source": "existing"}},
        )
        subset = schema.with_rows([row2])

        self.assertEqual(subset.indicators, [indicator])
        self.assertEqual(subset.constraints, schema.constraints)
        self.assertEqual(subset.equalities, schema.equalities)
        self.assertEqual(subset.obligations, schema.obligations)
        self.assertEqual(subset.evidence, schema.evidence)
        self.assertEqual(subset.expression_bindings, schema.expression_bindings)
        self.assertEqual(subset.row_provenance, schema.row_provenance)
        self.assertEqual(len(subset), 1)

if __name__ == "__main__":
    unittest.main()
