import sys, os, sqlite3

from sqlglot import exp
import unittest, json
import logging
from parseval.to_dot import display_uexpr

from parseval.utils import Logger

from parseval.instance import Instance
from parseval.constants import PBit
from parseval.data_generator import (
    DataGenerator,
    OperatorConstraintRequest,
    OperatorRuleRegistry,
)
from parseval.query import preprocess_sql
from parseval.speculative import SpeculativeGenerator

# from tqdm import tqdm

# schema = """CREATE TABLE frpm (CDSCode TEXT NOT NULL PRIMARY KEY, `Academic Year` TEXT NULL, `County Code` TEXT NULL, `District Code` INT NULL, `School Code` TEXT NULL, `County Name` TEXT NULL, `District Name` TEXT NULL, `School Name` TEXT NULL, `District Type` TEXT NULL, `School Type` TEXT NULL, `Educational Option Type` TEXT NULL, `NSLP Provision Status` TEXT NULL, `Charter School (Y/N)` INT NULL, `Charter School Number` TEXT NULL, `Charter Funding Type` TEXT NULL, IRC INT NULL, `Low Grade` TEXT NULL, `High Grade` TEXT NULL, `Enrollment (K-12)` FLOAT NULL, `Free Meal Count (K-12)` FLOAT NULL, `Percent (%) Eligible Free (K-12)` FLOAT NULL, `FRPM Count (K-12)` FLOAT NULL, `Percent (%) Eligible FRPM (K-12)` FLOAT NULL, `Enrollment (Ages 5-17)` FLOAT NULL, `Free Meal Count (Ages 5-17)` FLOAT NULL, `Percent (%) Eligible Free (Ages 5-17)` FLOAT NULL, `FRPM Count (Ages 5-17)` FLOAT NULL, `Percent (%) Eligible FRPM (Ages 5-17)` FLOAT NULL, `2013-14 CALPADS Fall 1 Certification Status` INT NULL, FOREIGN KEY (CDSCode) REFERENCES schools (CDSCode));CREATE TABLE satscores (cds TEXT NOT NULL PRIMARY KEY, rtype TEXT NOT NULL, sname TEXT NULL, dname TEXT NULL, cname TEXT NULL, enroll12 INT NOT NULL, NumTstTakr INT NOT NULL, AvgScrRead INT NULL, AvgScrMath INT NULL, AvgScrWrite INT NULL, NumGE1500 INT NULL, FOREIGN KEY (cds) REFERENCES schools (CDSCode));CREATE TABLE schools (CDSCode TEXT NOT NULL PRIMARY KEY, NCESDist TEXT NULL, NCESSchool TEXT NULL, StatusType TEXT NOT NULL, County TEXT NOT NULL, District TEXT NOT NULL, School TEXT NULL, Street TEXT NULL, StreetAbr TEXT NULL, City TEXT NULL, Zip TEXT NULL, State TEXT NULL, MailStreet TEXT NULL, MailStrAbr TEXT NULL, MailCity TEXT NULL, MailZip TEXT NULL, MailState TEXT NULL, Phone TEXT NULL, Ext TEXT NULL, Website TEXT NULL, OpenDate DATE NULL, ClosedDate DATE NULL, Charter INT NULL, CharterNum TEXT NULL, FundingType TEXT NULL, DOC TEXT NOT NULL, DOCType TEXT NOT NULL, SOC TEXT NULL, SOCType TEXT NULL, EdOpsCode TEXT NULL, EdOpsName TEXT NULL, EILCode TEXT NULL, EILName TEXT NULL, GSoffered TEXT NULL, GSserved TEXT NULL, Virtual TEXT NULL, Magnet INT NULL, Latitude FLOAT NULL, Longitude FLOAT NULL, AdmFName1 TEXT NULL, AdmLName1 TEXT NULL, AdmEmail1 TEXT NULL, AdmFName2 TEXT NULL, AdmLName2 TEXT NULL, AdmEmail2 TEXT NULL, AdmFName3 TEXT NULL, AdmLName3 TEXT NULL, AdmEmail3 TEXT NULL, LastUpdate DATE NOT NULL);
# """
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


logger = logging.getLogger("parseval.coverage")


class TestMain(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        logger.info("Setting up TestMain class")
        cls.workspace = "examples/instantiations"
        reset_folder(cls.workspace)
        import json

        with open("dataset/dev.json") as f:
            cls.dev_data = json.load(f)

        with open("dataset/schema.json") as f:
            cls.schema = json.load(f)

    def _run_query(self, instance: Instance, sql: str):

        # instance.to_db(self.workspace)
        database = os.path.join(self.workspace, f"{instance.name}.sqlite")
        with sqlite3.connect(database) as conn:
            return conn.execute(sql).fetchall()

    def _execute_sql(self, host_or_path: str, database: str, sql: str):
        database_path = os.path.join(host_or_path, f"{database}.sqlite")
        with sqlite3.connect(database_path) as conn:
            return conn.execute(sql).fetchall()

    @unittest.skip("Skipping this test for now")
    def test_speculative(self):
        import threading

        db_queue = []
        stop_event = threading.Event()
        for idx, row in enumerate(self.dev_data):
            sql = row["SQL"]
            db_id = row["db_id"]
            question_id = row["question_id"]
            name = f"{row['db_id']}_{row['question_id']}"
            schema = ";".join(self.schema[db_id])
            instance = Instance(ddls=schema, name=name, dialect="sqlite")
            try:
                expr = preprocess_sql(sql, instance, dialect="sqlite")
                generator = SpeculativeGenerator(expr=expr, instance=instance)
                result = generator.generate(
                    db_queue=db_queue,
                    stop_event=stop_event,
                    host_or_path=self.workspace,
                )
                good = self._run_query(instance, sql)

                if idx % 50 == 0:
                    print(f"Processed {idx} examples")
            except Exception as e:
                good = str(e)
                print(f"Error processing {sql}: ")
                # raise e
                continue
            finally:
                with open(os.path.join(self.workspace, f"progress.json"), "a") as f:
                    f.write(f"{db_id}_{question_id}: {good}\n")

    @unittest.skip("Skipping this test for now")
    def test_spj_disjunct(self):
        successes = 0
        unsupported = []
        for row in self.dev_data:
            sql = row["SQL"]
            db_id = row["db_id"]
            if db_id != "california_schools":
                break
            question_id = row["question_id"]
            name = f"{row['db_id']}_{row['question_id']}"
            instance = Instance(ddls=schema, name=name, dialect="sqlite")
            try:
                expr = preprocess_sql(sql, instance, dialect="sqlite")
                generator = DataGenerator(expr=expr, instance=instance, verbose=False)
                result = generator.generate(timeout=10)
                good = self._run_query(instance, sql)
                successes += 1
                with open(os.path.join(self.workspace, f"progress.json"), "a") as f:
                    f.write(f"{db_id}_{question_id}: {good}\n")
            except Exception as e:
                print(sql)
                print(f"Error processing {name}: {e}")
                unsupported.append((name, str(e)))
                continue

        print(f"successful generations: {successes}")
        if unsupported:
            print(f"unsupported queries: {len(unsupported)}")
        self.assertGreaterEqual(successes, 70)

    def test_instantiate_db(self):
        from parseval import instantiate_db

        for idx, row in enumerate(self.dev_data):
            sql = row["SQL"]
            db_id = row["db_id"]
            question_id = row["question_id"]
            name = f"{row['db_id']}_{row['question_id']}"
            schema = ";".join(self.schema[db_id])
            try:
                instantiate_db(
                    query=sql,
                    schema=schema,
                    host_or_path=self.workspace,
                    db_id=name,
                    dialect="sqlite",
                )
                good = self._execute_sql(self.workspace, f"{name}", sql)
                if idx % 50 == 0:
                    print(f"Processed {idx} examples")
            except Exception as e:
                good = str(e)
                print(f"Error processing {sql}: ")
                raise e
                continue
            finally:
                with open(os.path.join(self.workspace, f"progress.json"), "a") as f:
                    f.write(f"{db_id}_{question_id}: {good}\n")


if __name__ == "__main__":
    runner = unittest.TextTestRunner(stream=sys.stdout, verbosity=2)
    unittest.main(testRunner=runner, exit=False)
