from parseval.instance import Instance
from parseval.symbolic import CoverageThresholds, SymbolicEngine


def test_plain_not_null_text_projection_uses_domain_builder_values():
    instance = Instance(
        ddls="CREATE TABLE person (id INT PRIMARY KEY, first_name VARCHAR(255), last_name VARCHAR(255))",
        name="text_projection",
        dialect="mysql",
    )
    sql = "SELECT first_name, last_name FROM person"

    result = SymbolicEngine(instance, sql, dialect="mysql", max_iterations=5).generate(
        thresholds=CoverageThresholds(atom_null=0)
    )

    assert result.rows_generated > 0
    rows = {
        table.table_name: list(table.rows)
        for table in instance.snapshot().tables
    }
    person = rows["person"][0]
    assert person["first_name"] != "value"
    assert person["last_name"] != "value"
    assert person["first_name"] != person["last_name"]
