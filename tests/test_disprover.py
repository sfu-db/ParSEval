import sys
import os

from sqlglot import exp
import unittest, json
import logging
from parseval.speculative import SpeculativeGenerator

from parseval.disprover import Disprover, DisproverConfig

from parseval.instance import Instance
from parseval.query import preprocess_sql

schema = """CREATE TABLE frpm (CDSCode TEXT NOT NULL PRIMARY KEY, `Academic Year` TEXT NULL, `County Code` TEXT NULL, `District Code` INT NULL, `School Code` TEXT NULL, `County Name` TEXT NULL, `District Name` TEXT NULL, `School Name` TEXT NULL, `District Type` TEXT NULL, `School Type` TEXT NULL, `Educational Option Type` TEXT NULL, `NSLP Provision Status` TEXT NULL, `Charter School (Y/N)` INT NULL, `Charter School Number` TEXT NULL, `Charter Funding Type` TEXT NULL, IRC INT NULL, `Low Grade` TEXT NULL, `High Grade` TEXT NULL, `Enrollment (K-12)` FLOAT NULL, `Free Meal Count (K-12)` FLOAT NULL, `Percent (%) Eligible Free (K-12)` FLOAT NULL, `FRPM Count (K-12)` FLOAT NULL, `Percent (%) Eligible FRPM (K-12)` FLOAT NULL, `Enrollment (Ages 5-17)` FLOAT NULL, `Free Meal Count (Ages 5-17)` FLOAT NULL, `Percent (%) Eligible Free (Ages 5-17)` FLOAT NULL, `FRPM Count (Ages 5-17)` FLOAT NULL, `Percent (%) Eligible FRPM (Ages 5-17)` FLOAT NULL, `2013-14 CALPADS Fall 1 Certification Status` INT NULL, FOREIGN KEY (CDSCode) REFERENCES schools (CDSCode));CREATE TABLE satscores (cds TEXT NOT NULL PRIMARY KEY, rtype TEXT NOT NULL, sname TEXT NULL, dname TEXT NULL, cname TEXT NULL, enroll12 INT NOT NULL, NumTstTakr INT NOT NULL, AvgScrRead INT NULL, AvgScrMath INT NULL, AvgScrWrite INT NULL, NumGE1500 INT NULL, FOREIGN KEY (cds) REFERENCES schools (CDSCode));CREATE TABLE schools (CDSCode TEXT NOT NULL PRIMARY KEY, NCESDist TEXT NULL, NCESSchool TEXT NULL, StatusType TEXT NOT NULL, County TEXT NOT NULL, District TEXT NOT NULL, School TEXT NULL, Street TEXT NULL, StreetAbr TEXT NULL, City TEXT NULL, Zip TEXT NULL, State TEXT NULL, MailStreet TEXT NULL, MailStrAbr TEXT NULL, MailCity TEXT NULL, MailZip TEXT NULL, MailState TEXT NULL, Phone TEXT NULL, Ext TEXT NULL, Website TEXT NULL, OpenDate DATE NULL, ClosedDate DATE NULL, Charter INT NULL, CharterNum TEXT NULL, FundingType TEXT NULL, DOC TEXT NOT NULL, DOCType TEXT NOT NULL, SOC TEXT NULL, SOCType TEXT NULL, EdOpsCode TEXT NULL, EdOpsName TEXT NULL, EILCode TEXT NULL, EILName TEXT NULL, GSoffered TEXT NULL, GSserved TEXT NULL, Virtual TEXT NULL, Magnet INT NULL, Latitude FLOAT NULL, Longitude FLOAT NULL, AdmFName1 TEXT NULL, AdmLName1 TEXT NULL, AdmEmail1 TEXT NULL, AdmFName2 TEXT NULL, AdmLName2 TEXT NULL, AdmEmail2 TEXT NULL, AdmFName3 TEXT NULL, AdmLName3 TEXT NULL, AdmEmail3 TEXT NULL, LastUpdate DATE NOT NULL);
"""
# sql = """SELECT T1.`District Code`, T2.`NumGE1500`  FROM frpm AS T1 left JOIN satscores AS T2 on T1.CDSCode = T2.cds where T2.`NumGE1500` is NOT NULL and T1.`District Code` > 15 """

# sql = """SELECT T1.`District Code`, T2.`NumGE1500`  FROM frpm AS T1 left JOIN satscores AS T2 on T1.CDSCode = T2.cds where T1.`Academic Year` <> '2023' or T1.`District Code` > 15  """

# sql = """SELECT T1.`District Code`, T2.`NumGE1500`  FROM frpm AS T1 INNER JOIN satscores AS T2 on T1.CDSCode = T2.cds where T1.`Academic Year` <> '2023' or T1.`District Code` > 15 """
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


from parseval import Logger

Logger(
    log_file="logs/test_disprover.log",
    level=logging.DEBUG,
    structured_logs={"coverage"},
    log_files={"coverage": "logs/test_disprover_coverage.log"},
)

logger = logging.getLogger("parseval.coverage")


class TestSpeculativeGenerator(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        logger.info("Setting up TestSpeculativeGenerator class")
        cls.workspace = "examples/tests"
        reset_folder(cls.workspace)
        cls.db_queue = None  # Placeholder for a database queue if needed
        cls.stop_event = None  # Placeholder for a stop event if needed

    def run_query(self, sql, host_or_path, db_id, dialect="sqlite"):
        from parseval.db_manager import DBManager

        if dialect == "sqlite":
            db_id = db_id if db_id.endswith(".sqlite") else db_id + ".sqlite"

        with DBManager().get_connection(
            host_or_path=host_or_path, database=db_id
        ) as conn:
            results = conn.execute(sql, fetch="all")
            return results

    def test_spj_disjunct(self):
        instance = Instance(ddls=schema, name=f"test_spj_disjunct", dialect="sqlite")
        sql = """SELECT  T1.`CDSCode`, CASE WHEN T1.`School Name`  = 'SFU' THEN 1 WHEN T1.`School Name`  = 'SFU2' THEN 2 ELSE 0 END  FROM frpm AS T1  where T1.`Academic Year`  <> '2023' or CAST(  T1.`District Code`  AS INT) > 15"""
        q1 = "SELECT T1.`sname` FROM satscores AS T1 JOIN frpm AS T2 on T1.cds = T2.CDSCode where T1.`NumGE1500` > 100 OR T1.`NumGE1500` < 80"  # order by

        q2 = "SELECT T1.`sname` FROM satscores AS T1 JOIN frpm AS T2 on T1.cds = T2.CDSCode where T1.`NumGE1500` > 100"  # order by

        from parseval.configuration import DisproverConfig

        config = DisproverConfig(
            host_or_path=self.workspace,
            db_id=instance.name,
            global_timeout=30,
            query_timeout=10,
        )

        disprover = Disprover(q1, q2, schema=schema, config=config)

        res = disprover.run()

        print(res)

    def test_casewhen(self):

        q1 = """SELECT  T1.`CDSCode`, CASE WHEN T1.`School Name`  = 'SFU' THEN 1 WHEN T1.`School Name`  = 'SFU2' THEN 2 ELSE 0 END  FROM frpm AS T1  where T1.`Academic Year`  <> '2023' or CAST(  T1.`District Code`  AS INT) > 15"""

        q2 = """SELECT  T1.`CDSCode`, CASE WHEN T1.`School Name`  = 'SFU3' THEN 1 WHEN T1.`School Name`  = 'SFU2' THEN 2 ELSE 0 END  FROM frpm AS T1  where T1.`Academic Year`  <> '2023' or CAST(  T1.`District Code`  AS INT) > 15"""

        instance = Instance(ddls=schema, name=f"test_casewhen", dialect="sqlite")

        from parseval.configuration import DisproverConfig

        config = DisproverConfig(
            host_or_path=self.workspace,
            db_id=instance.name,
            global_timeout=30,
            query_timeout=10,
        )

        disprover = Disprover(q1, q2, schema=schema, config=config)

        res = disprover.run()

        print(res)

    def test_groupby(self):

        q1 = """SELECT  T1.`CDSCode`, COUNT(*) FROM frpm AS T1  where T1.`Academic Year`  <> '2023' or CAST(  T1.`District Code`  AS INT) > 15 GROUP BY T1.CDSCode"""

        q2 = """SELECT  T1.`CDSCode`, COUNT(*) FROM frpm AS T1  where T1.`Academic Year`  <> '2023' or T1.`District Code` > 15 GROUP BY T1.CDSCode order by T1.CDSCode"""

        instance = Instance(ddls=schema, name=f"test_groupby", dialect="sqlite")

        from parseval.configuration import DisproverConfig

        config = DisproverConfig(
            host_or_path=self.workspace,
            db_id=instance.name,
            global_timeout=30,
            query_timeout=10,
        )

        disprover = Disprover(q1, q2, schema=schema, config=config)

        res = disprover.run()

        print(res)

    def test_having(self):

        q1 = """SELECT  T1.`CDSCode`, COUNT(*) FROM frpm AS T1  where T1.`Academic Year`  <> '2023' or CAST(  T1.`District Code`  AS INT) > 15 GROUP BY T1.CDSCode HAVING COUNT(*) > 2"""

        q2 = """SELECT  T1.`CDSCode`, COUNT(*) FROM frpm AS T1  where T1.`Academic Year`  <> '2023' or T1.`District Code` > 15 GROUP BY T1.CDSCode HAVING COUNT(*) > 2 order by T1.CDSCode"""

        instance = Instance(ddls=schema, name=f"test_having", dialect="sqlite")

        from parseval.configuration import DisproverConfig

        config = DisproverConfig(
            host_or_path=self.workspace,
            db_id=instance.name,
            global_timeout=30,
            query_timeout=10,
        )

        disprover = Disprover(q1, q2, schema=schema, config=config)

        res = disprover.run()

        print(res)


if __name__ == "__main__":

    runner = unittest.TextTestRunner(stream=sys.stdout, verbosity=2)
    unittest.main(testRunner=runner, exit=False)
