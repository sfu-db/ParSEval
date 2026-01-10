import sys
import os

current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.abspath(os.path.join(current_dir, os.pardir))
sys.path.append(parent_dir)
from src.parseval.db_manager import DBManager
from parseval.generator import Generator

# from src.parseval.generator import Generator
from sqlglot import exp
import unittest
from sqlglot import parse
import logging, json

from pathlib import Path

from shutil import rmtree


def assert_folder(file_path):
    if not Path(file_path).exists():
        Path(file_path).mkdir(parents=True, exist_ok=True)
    return file_path


def rm_folder(folder_path):
    rmtree(Path(folder_path), ignore_errors=True)


def reset_folder(folder_path):
    rm_folder(folder_path)
    assert_folder(folder_path)


logger = logging.getLogger("src.test")
schema = """CREATE TABLE frpm (CDSCode TEXT NOT NULL PRIMARY KEY, `Academic Year` TEXT NULL, `County Code` TEXT NULL, `District Code` INT NULL, `School Code` TEXT NULL, `County Name` TEXT NULL, `District Name` TEXT NULL, `School Name` TEXT NULL, `District Type` TEXT NULL, `School Type` TEXT NULL, `Educational Option Type` TEXT NULL, `NSLP Provision Status` TEXT NULL, `Charter School (Y/N)` INT NULL, `Charter School Number` TEXT NULL, `Charter Funding Type` TEXT NULL, IRC INT NULL, `Low Grade` TEXT NULL, `High Grade` TEXT NULL, `Enrollment (K-12)` FLOAT NULL, `Free Meal Count (K-12)` FLOAT NULL, `Percent (%) Eligible Free (K-12)` FLOAT NULL, `FRPM Count (K-12)` FLOAT NULL, `Percent (%) Eligible FRPM (K-12)` FLOAT NULL, `Enrollment (Ages 5-17)` FLOAT NULL, `Free Meal Count (Ages 5-17)` FLOAT NULL, `Percent (%) Eligible Free (Ages 5-17)` FLOAT NULL, `FRPM Count (Ages 5-17)` FLOAT NULL, `Percent (%) Eligible FRPM (Ages 5-17)` FLOAT NULL, `2013-14 CALPADS Fall 1 Certification Status` INT NULL, FOREIGN KEY (CDSCode) REFERENCES schools (CDSCode));CREATE TABLE satscores (cds TEXT NOT NULL PRIMARY KEY, rtype TEXT NOT NULL, sname TEXT NULL, dname TEXT NULL, cname TEXT NULL, enroll12 INT NOT NULL, NumTstTakr INT NOT NULL, AvgScrRead INT NULL, AvgScrMath INT NULL, AvgScrWrite INT NULL, NumGE1500 INT NULL, FOREIGN KEY (cds) REFERENCES schools (CDSCode));CREATE TABLE schools (CDSCode TEXT NOT NULL PRIMARY KEY, NCESDist TEXT NULL, NCESSchool TEXT NULL, StatusType TEXT NOT NULL, County TEXT NOT NULL, District TEXT NOT NULL, School TEXT NULL, Street TEXT NULL, StreetAbr TEXT NULL, City TEXT NULL, Zip TEXT NULL, State TEXT NULL, MailStreet TEXT NULL, MailStrAbr TEXT NULL, MailCity TEXT NULL, MailZip TEXT NULL, MailState TEXT NULL, Phone TEXT NULL, Ext TEXT NULL, Website TEXT NULL, OpenDate DATE NULL, ClosedDate DATE NULL, Charter INT NULL, CharterNum TEXT NULL, FundingType TEXT NULL, DOC TEXT NOT NULL, DOCType TEXT NOT NULL, SOC TEXT NULL, SOCType TEXT NULL, EdOpsCode TEXT NULL, EdOpsName TEXT NULL, EILCode TEXT NULL, EILName TEXT NULL, GSoffered TEXT NULL, GSserved TEXT NULL, Virtual TEXT NULL, Magnet INT NULL, Latitude FLOAT NULL, Longitude FLOAT NULL, AdmFName1 TEXT NULL, AdmLName1 TEXT NULL, AdmEmail1 TEXT NULL, AdmFName2 TEXT NULL, AdmLName2 TEXT NULL, AdmEmail2 TEXT NULL, AdmFName3 TEXT NULL, AdmLName3 TEXT NULL, AdmEmail3 TEXT NULL, LastUpdate DATE NOT NULL);
"""


logging.basicConfig(
    level=logging.INFO,  # or DEBUG, WARNING, ERROR, etc.
    format="[%(asctime)s] %(levelname)s [%(filename)s:%(lineno)d]: %(message)s",
)

BIRD_DEV_FP = "datasets/bird/dev.sql"


class TestGenerator(unittest.TestCase):
    # @unittest.skip("Skipping data generation test")
    def test_data_generator(self):
        reset_folder("examples/db")
        with open(BIRD_DEV_FP, "r") as f:
            data = json.load(f)
        INDEX = 82
        for row in data:
            question_id = row["question_id"]
            if question_id < 79:
                continue
            if row["question_id"] <= INDEX:
                sql = row["SQL"]
                sql = """SELECT NumTstTakr  FROM satscores o                WHERE EXISTS (                SELECT 1                FROM frpm c                WHERE c.CDSCode = o.cds                )"""
                logger.info(f"Testing query: {sql}")
                generator = Generator(
                    schema=schema, query=sql, name=f"test_{question_id}"
                )

                with open(f"examples/db/{generator.name}_plan.sql", "w") as f:
                    f.write(f"-- Query: {sql}\n")
                    f.write(generator.plan.sql())

                instance = generator.generate(max_iter=325)
                instance.to_db("examples/db")

                break

    # @unittest.skip("Skipping data validation test")
    def test_data_validator(self):
        with open(BIRD_DEV_FP, "r") as f:
            data = json.load(f)
        INDEX = 82
        for row in data:
            question_id = row["question_id"]
            if question_id < 79:
                continue
            if row["question_id"] <= INDEX:
                sql = row["SQL"]
                host_or_path = "examples/db"
                db_id = "test_" + str(question_id) + ".sqlite"

                if not os.path.exists(os.path.join(host_or_path, db_id)):
                    print(f"Database {db_id} does not exist, skipping test.")
                    break
                with DBManager().get_connection(host_or_path, db_id) as conn:
                    data = conn.execute(sql, fetch="all")
                    if len(data) == 0:
                        print(f"Query {question_id} returned no results.")


if __name__ == "__main__":

    #
    runner = unittest.TextTestRunner(stream=sys.stdout, verbosity=2)
    unittest.main(testRunner=runner, exit=False)
