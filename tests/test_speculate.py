import sys
import os

from sqlglot import exp
import unittest, json
import logging
from parseval.speculative import (
    FunctionSpec,
    SpeculativeGenerator,
    extract_condition_specs,
)

from parseval.instance import Instance
from parseval.query import preprocess_sql
from shutil import rmtree

from pathlib import Path


def assert_folder(file_path):
    if not Path(file_path).exists():
        Path(file_path).mkdir(parents=True, exist_ok=True)
    return file_path


def rm_folder(folder_path):
    rmtree(Path(folder_path), ignore_errors=True)


def reset_folder(folder_path):
    rm_folder(folder_path)
    assert_folder(folder_path)


# from parseval.logger import Logger

# Logger(
#     verbose={
#         "coverage": True,
#         "symbolic": False,
#         "smt": True,
#         "db": False,
#     },
#     log_file="log.log",
# )

logger = logging.getLogger("parseval.coverage")


class TestSpeculativeGenerator(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import threading

        logger.info("Setting up TestSpeculativeGenerator class")
        cls.workspace = "examples/tests"
        reset_folder(cls.workspace)

        import json

        with open("dataset/dev.json") as f:
            cls.dev_data = json.load(f)

        with open("dataset/schema.json") as f:
            cls.schema = json.load(f)

        cls.california_schools_schema = ";".join(cls.schema["california_schools"])

        cls.stop_event = threading.Event()  # Placeholder for a stop event if needed

    def early_stop(self, instance: Instance) -> bool:
        return False

    def run_query(self, sql, host_or_path, db_id, dialect="sqlite"):
        from parseval.db_manager import DBManager

        if dialect == "sqlite":
            db_id = db_id if db_id.endswith(".sqlite") else db_id + ".sqlite"

        with DBManager().get_connection(
            host_or_path=host_or_path, database=db_id
        ) as conn:
            results = conn.execute(sql, fetch="all")
            return results

    def test_extracts_function_specs(self):
        cases = [
            (
                "SELECT * FROM schools WHERE LENGTH(City) = 5",
                ("LENGTH", "EQ", 5),
            ),
            (
                "SELECT * FROM satscores WHERE ABS(NumGE1500) >= 10",
                ("ABS", "GTE", 10),
            ),
            (
                "SELECT * FROM schools WHERE INSTR(City, 'ab') > 0",
                ("INSTR", "GT", 0),
            ),
            (
                "SELECT * FROM schools WHERE STRFTIME('%Y-%m-%d', LastUpdate) = '2024-01-02'",
                ("STRFTIME", "EQ", "2024-01-02"),
            ),
        ]

        for sql, expected in cases:
            predicate = preprocess_sql(
                sql,
                Instance(
                    ddls=self.california_schools_schema,
                    name="extract_case",
                    dialect="sqlite",
                ),
                dialect="sqlite",
            ).find(exp.Predicate)
            specs = extract_condition_specs(predicate)
            self.assertEqual(len(specs), 1)
            self.assertIsInstance(specs[0], FunctionSpec)
            self.assertEqual((specs[0].name, specs[0].op, specs[0].value), expected)

    def test_function_validation_helpers(self):
        generator = SpeculativeGenerator.__new__(SpeculativeGenerator)

        length_spec = FunctionSpec(
            name="LENGTH", table="schools", column="City", op="EQ", value=5
        )
        self.assertTrue(generator._validate_function_candidate(length_spec, "abcde"))

        abs_spec = FunctionSpec(
            name="ABS", table="satscores", column="NumGE1500", op="GTE", value=10
        )
        self.assertTrue(generator._validate_function_candidate(abs_spec, -12))

        instr_spec = FunctionSpec(
            name="INSTR", table="schools", column="City", op="GT", value=0, args=["ab"]
        )
        self.assertTrue(generator._validate_function_candidate(instr_spec, "xxabyy"))

        strftime_spec = FunctionSpec(
            name="STRFTIME",
            table="schools",
            column="LastUpdate",
            op="EQ",
            value="2024-01-02",
            args=["%Y-%m-%d"],
        )
        self.assertTrue(
            generator._validate_function_candidate(
                strftime_spec, __import__("datetime").date(2024, 1, 2)
            )
        )

    @unittest.skip("Skipping this test for now")
    def test_spj_disjunct(self):
        instance = Instance(
            ddls=self.california_schools_schema,
            name=f"test_spj_disjunct",
            dialect="sqlite",
        )
        sql = """SELECT  T1.`CDSCode`, CASE WHEN T1.`School Name`  = 'SFU' THEN 1 WHEN T1.`School Name`  = 'SFU2' THEN 2 ELSE 0 END  FROM frpm AS T1  where T1.`Academic Year`  <> '2023' or CAST(  T1.`District Code`  AS INT) > 15"""
        sql = "SELECT T1.`sname` FROM satscores AS T1 JOIN frpm AS T2 on T1.cds = T2.CDSCode where T1.`NumGE1500` > 100 OR T1.`NumGE1500` < 80"  # order by

        expr = preprocess_sql(sql, instance, dialect="sqlite")
        generator = SpeculativeGenerator(expr, instance)
        generator.generate(early_stoper=self.early_stop, stop_event=self.stop_event)
        instance.to_db(self.workspace, instance.name)

        self.assertGreater(
            len(self.run_query(sql, host_or_path=self.workspace, db_id=instance.name)),
            0,
        )

    @unittest.skip("Skipping this test for now")
    def test_groupby(self):
        sql = """SELECT GSserved FROM schools WHERE City = 'Adelanto' GROUP BY GSserved """

        instance = Instance(
            ddls=self.california_schools_schema, name=f"test_groupby", dialect="sqlite"
        )
        expr = preprocess_sql(sql, instance, dialect="sqlite")
        generator = SpeculativeGenerator(expr, instance)
        generator.generate(early_stoper=self.early_stop, stop_event=self.stop_event)
        instance.to_db(self.workspace, instance.name)

        self.assertGreater(
            len(self.run_query(sql, host_or_path=self.workspace, db_id=instance.name)),
            0,
        )

    @unittest.skip("Skipping this test for now")
    def test_aggregate(self):
        sql = """SELECT GSserved, count(NCESDist) FROM schools WHERE City = 'Adelanto' GROUP BY GSserved """
        instance = Instance(
            ddls=self.california_schools_schema,
            name=f"test_groupby_aggregate",
            dialect="sqlite",
        )
        expr = preprocess_sql(sql, instance, dialect="sqlite")
        generator = SpeculativeGenerator(expr, instance)
        generator.generate(early_stoper=self.early_stop, stop_event=self.stop_event)
        instance.to_db(self.workspace, instance.name)
        self.assertGreater(
            len(self.run_query(sql, host_or_path=self.workspace, db_id=instance.name)),
            0,
        )

    def test_having(self):
        sql = """SELECT GSserved, count(NCESDist) FROM schools WHERE City = 'Adelanto' GROUP BY GSserved having count(NCESDist) > 5 """
        instance = Instance(
            ddls=self.california_schools_schema,
            name=f"test_groupby_aggregate_having",
            dialect="sqlite",
        )
        expr = preprocess_sql(sql, instance, dialect="sqlite")
        generator = SpeculativeGenerator(expr, instance)
        generator.generate(early_stoper=self.early_stop, stop_event=self.stop_event)
        insert_stmt = instance.to_db(
            self.workspace, instance.name, return_inserted=True
        )

        print(insert_stmt)

        result = self.run_query(sql, host_or_path=self.workspace, db_id=instance.name)

        print(f"Query Result: {result}")  # Debug print to check the output

        self.assertGreater(
            len(self.run_query(sql, host_or_path=self.workspace, db_id=instance.name)),
            0,
        )

    # @unittest.skip("Skipping this test for now")
    def test_case_when(self):
        sql = """SELECT  T1.`CDSCode`, CASE WHEN T1.`School Name`  = 'SFU' THEN 1 WHEN T1.`School Name`  = 'SFU2' THEN 2 ELSE 0 END  FROM frpm AS T1  where T1.`Academic Year`  <> '2023' """
        instance = Instance(
            ddls=self.california_schools_schema, name=f"test_casewhen", dialect="sqlite"
        )
        expr = preprocess_sql(sql, instance, dialect="sqlite")
        generator = SpeculativeGenerator(expr, instance)
        generator.generate(early_stoper=self.early_stop, stop_event=self.stop_event)
        instance.to_db(self.workspace, instance.name)

        self.assertGreater(
            len(self.run_query(sql, host_or_path=self.workspace, db_id=instance.name)),
            0,
        )

    @unittest.skip("Skipping this test for now")
    def test_scalar(self):
        sql = """SELECT T1.`sname` FROM satscores AS T1 where T1.cds = (SELECT T2.CDSCode from frpm AS T2 order by T2.CDSCode limit 1)"""
        instance = Instance(
            ddls=self.california_schools_schema, name=f"test_scalar", dialect="sqlite"
        )
        expr = preprocess_sql(sql, instance, dialect="sqlite")
        generator = SpeculativeGenerator(expr, instance)
        generator.generate(early_stoper=self.early_stop, stop_event=self.stop_event)
        instance.to_db(self.workspace, instance.name)

        self.assertGreater(
            len(self.run_query(sql, host_or_path=self.workspace, db_id=instance.name)),
            0,
        )

    @unittest.skip("Skipping this test for now")
    def test_exists(self):
        sql = """SELECT T1.`sname` FROM satscores AS T1 where exists (select 1 from frpm AS T2 where T1.cds = T2.CDSCode)"""
        instance = Instance(
            ddls=self.california_schools_schema, name=f"test_exists", dialect="sqlite"
        )
        expr = preprocess_sql(sql, instance, dialect="sqlite")
        generator = SpeculativeGenerator(expr, instance)
        generator.generate(early_stoper=self.early_stop, stop_event=self.stop_event)
        instance.to_db(self.workspace, instance.name)
        self.assertGreater(
            len(self.run_query(sql, host_or_path=self.workspace, db_id=instance.name)),
            0,
        )


if __name__ == "__main__":

    runner = unittest.TextTestRunner(stream=sys.stdout, verbosity=2)
    unittest.main(testRunner=runner, exit=False)
