from parseval.instance import Instance
from parseval.symbolic import CoverageThresholds, SymbolicEngine
import json

db_id = "toxicology"
sql = "SELECT T1.bond_type FROM bond AS T1 INNER JOIN connected AS T2 ON T1.bond_id = T2.bond_id WHERE T2.atom_id = 'TR004_8' AND T2.atom_id2 = 'TR004_20' OR T2.atom_id2 = 'TR004_8' AND T2.atom_id = 'TR004_20'"

with open("data/sqlite/schema.json") as f:
    schemas = json.load(f)
ddls = ";".join(schemas[db_id])

instance = Instance(ddls=ddls, name="test_213", dialect="sqlite")
engine = SymbolicEngine(instance, sql, dialect="sqlite", max_iterations=50)
result = engine.generate(thresholds=CoverageThresholds(atom_null=0), speculate_first=True)

print("generated rows:", result.rows_generated)

from parseval.instance.io import to_db
from parseval.db_manager import DBManager

db_path = "tmp/test_213.db"
connection_string = f"sqlite:///{db_path}"
import os
if os.path.exists(db_path):
    os.remove(db_path)
to_db(instance, connection_string, dialect="sqlite")

with DBManager().get_connection(connection_string, "sqlite") as conn:
    rows = conn.execute(sql, fetch="all", timeout=30)
    print("query result:", rows)
    print("tables:")
    for table in ["bond", "connected", "atom"]:
        print(f"--- {table} ---")
        try:
            for r in conn.execute(f"select * from {table}", fetch="all"):
                print(r)
        except Exception as e:
            print("Error reading", table, e)
