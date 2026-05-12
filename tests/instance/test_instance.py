import datetime as dt
import unittest

from parseval.instance import Instance
from parseval.plan.rex import Row, Variable
import json

BIRD_SCHEMA_FP = "data/sqlite/schema.json"


class InstanceSnapshotTests(unittest.TestCase):
    def test_instance_with_bird(self):
        with open(BIRD_SCHEMA_FP) as f:
            bird_schema = json.load(f)
        # bird_schema = json.load(open(BIRD_SCHEMA_FP))
        for db_id, scm in bird_schema.items():
            ddls = ';'.join(scm)
            instance = Instance(ddls, name = db_id, dialect="sqlite")
            
            for _ in range(5):
                for tbl in instance.tables:
                    instance.create_row(table_name=tbl)
            instance.to_db(f"sqlite:///tmp/{db_id}")
    


if __name__ == "__main__":
    unittest.main()
