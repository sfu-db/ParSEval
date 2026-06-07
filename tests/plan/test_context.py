from __future__ import annotations

import unittest

from parseval.identity import (
    ColumnKind,
    RelationKind,
    column_id,
    identifier_name,
    relation_id,
)
from parseval.plan.context import AggregateGroup, DerivedSchema, Row, WindowFrame


REL = relation_id(RelationKind.TABLE, identifier_name("employees"))
COL_DEPT = column_id(ColumnKind.PHYSICAL, identifier_name("dept"), REL)
COL_SALARY = column_id(ColumnKind.PHYSICAL, identifier_name("salary"), REL)
COL_AVG = column_id(ColumnKind.DERIVED, identifier_name("dept_avg"), REL)


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


if __name__ == "__main__":
    unittest.main()
