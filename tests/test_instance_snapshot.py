import datetime as dt
import unittest

from parseval.identity import (
    ColumnKind,
    RelationKind,
    column_id,
    identifier_name,
    relation_id,
)
from parseval.instance import Instance
from parseval.plan.rex import Row, Variable


SCHEMA = """
CREATE TABLE users (
    id INT PRIMARY KEY,
    name TEXT NOT NULL,
    created_at DATE NULL
);
"""

REL_USERS = relation_id(RelationKind.TABLE, identifier_name("users"))


def _row(rowid: str, **values):
    return Row(
        this=rowid,
        columns={
            key: Variable(
                this=f"{rowid}_{key}",
                _type="TEXT",
                concrete=value,
                column_id=column_id(
                    ColumnKind.PHYSICAL,
                    identifier_name(key),
                    REL_USERS,
                ),
                rowid=rowid,
            )
            for key, value in values.items()
        },
    )


class InstanceSnapshotTests(unittest.TestCase):
    def test_snapshot_keeps_in_memory_rows_unchanged(self):
        instance = Instance(ddls=SCHEMA, name="snapshot", dialect="sqlite")
        instance.add_row(
            "users",
            _row("users_rowid_0", id=1, name="alpha", created_at=dt.date(2024, 1, 1)),
        )
        instance.add_row(
            "users",
            _row("users_rowid_1", id=1, name="beta", created_at=dt.date(2024, 1, 2)),
        )
        original_count = len(instance.get_rows("users"))

        snapshot = instance.snapshot()

        self.assertEqual(len(instance.get_rows("users")), original_count)
        self.assertEqual(
            snapshot.tables[0].rows,
            (
                {"id": 1, "name": "alpha", "created_at": dt.date(2024, 1, 1)},
                {"id": 1, "name": "beta", "created_at": dt.date(2024, 1, 2)},
            ),
        )

    def test_snapshot_preserves_null_rows(self):
        instance = Instance(ddls=SCHEMA, name="snapshot", dialect="sqlite")
        instance.add_row(
            "users",
            _row("users_rowid_0", id=1, name="alpha", created_at=None),
        )
        instance.add_row(
            "users",
            _row("users_rowid_1", id=1, name="beta", created_at=None),
        )

        snapshot = instance.snapshot()

        self.assertEqual(
            snapshot.tables[0].rows,
            (
                {"id": 1, "name": "alpha", "created_at": None},
                {"id": 1, "name": "beta", "created_at": None},
            ),
        )


if __name__ == "__main__":
    unittest.main()
