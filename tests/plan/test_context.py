from __future__ import annotations

import unittest

from sqlglot import exp

from parseval.identity import (
    ColumnKind,
    RelationKind,
    column_id,
    identifier_name,
    relation_id,
)
from parseval.plan.context import AggregateGroup, DerivedSchema, Row, WindowFrame
from parseval.plan.rex import Variable
from parseval.identity import PARSEVAL_COLUMN_ID


REL = relation_id(RelationKind.TABLE, identifier_name("employees"))
COL_DEPT = column_id(ColumnKind.PHYSICAL, identifier_name("dept"), REL)
COL_SALARY = column_id(ColumnKind.PHYSICAL, identifier_name("salary"), REL)
COL_AVG = column_id(ColumnKind.DERIVED, identifier_name("dept_avg"), REL)
COL_GROUP = column_id(
    ColumnKind.DERIVED,
    identifier_name("_g0"),
    REL,
    source_column_id=COL_DEPT,
)


class ContextMetadataTests(unittest.TestCase):
    def test_row_prefers_column_identity_keys(self):
        row = Row(this=("employees", 0), columns={COL_DEPT: "sales", COL_SALARY: 50})

        self.assertEqual(row[COL_DEPT], "sales")
        self.assertEqual(row["dept"], "sales")
        self.assertEqual(row.columns, (COL_DEPT, COL_SALARY))
        self.assertEqual(dict(row.items()), {COL_DEPT: "sales", COL_SALARY: 50})

    def test_derived_schema_stores_aggregate_group_metadata(self):
        output_row = Row(this=("agg", 1, "sales"), columns={COL_DEPT: "sales", COL_AVG: 60})
        group = AggregateGroup(
            output_row_id=output_row.rowid,
            group_key=("sales",),
            source_row_ids=(("employees", 0), ("employees", 1)),
            aggregate_values={COL_AVG: 60},
        )

        schema = DerivedSchema(
            columns=(COL_DEPT, COL_AVG),
            rows=[output_row],
            aggregate_groups={output_row.rowid: group},
        )

        self.assertEqual(schema.aggregate_groups[output_row.rowid], group)

    def test_aggregate_group_stores_group_key_provenance(self):
        output_row = Row(this=("agg", 1, "sales"), columns={COL_GROUP: "sales"})
        group_expr = exp.column("dept")
        group = AggregateGroup(
            output_row_id=output_row.rowid,
            group_key=("sales",),
            source_row_ids=(("employees", 0), ("employees", 1)),
            aggregate_values={},
            group_expressions={COL_GROUP: group_expr},
            group_sources={COL_GROUP: (COL_DEPT,)},
            group_key_values={COL_GROUP: "sales"},
        )

        self.assertEqual(group.group_expressions[COL_GROUP], group_expr)
        self.assertEqual(group.group_sources[COL_GROUP], (COL_DEPT,))
        self.assertEqual(group.group_key_values[COL_GROUP], "sales")

    def test_derived_schema_stores_window_frame_metadata(self):
        output_row = Row(this=("employees", 0), columns={COL_DEPT: "sales", COL_AVG: 60})
        frame = WindowFrame(
            column_id=COL_AVG,
            source_row_id=output_row.rowid,
            partition_key=("sales",),
            order_key=(),
            frame_row_ids=(("employees", 0), ("employees", 1)),
            value=60,
        )

        schema = DerivedSchema(
            columns=(COL_DEPT, COL_AVG),
            rows=[output_row],
            window_frames={output_row.rowid: (frame,)},
        )

        self.assertEqual(schema.window_frames[output_row.rowid], (frame,))

    def test_row_reader_resolves_stamped_column_to_original_cell(self):
        cell = Variable(this="employees_0_dept", column_id=COL_DEPT, rowid=("employees", 0))
        row = Row(this=("employees", 0), columns={COL_DEPT: cell, COL_SALARY: 50})
        schema = DerivedSchema(columns=(COL_DEPT, COL_SALARY), rows=[row])
        reader = schema[0]
        col = exp.column("dept")
        col.meta[PARSEVAL_COLUMN_ID] = COL_DEPT

        self.assertIs(reader.resolve(col), cell)
        self.assertIs(reader.resolve(COL_DEPT), cell)

    def test_row_reader_resolves_source_column_identity(self):
        projected_dept = column_id(
            ColumnKind.PROJECTED,
            identifier_name("dept"),
            REL,
            source_column_id=COL_DEPT,
        )
        cell = Variable(
            this="employees_0_dept",
            column_id=projected_dept,
            rowid=("employees", 0),
        )
        row = Row(this=("employees", 0), columns={projected_dept: cell})
        reader = DerivedSchema(columns=(projected_dept,), rows=[row])[0]

        self.assertIs(reader.resolve(COL_DEPT), cell)


if __name__ == "__main__":
    unittest.main()
