import sys
from sqlglot import exp
import unittest, json
import logging, random

from parseval.instance import Instance
from parseval.query import preprocess_sql
from parseval.data_generator import DataGenerator

schema = """CREATE TABLE frpm (CDSCode TEXT NOT NULL PRIMARY KEY, `Academic Year` TEXT NULL, `County Code` TEXT NULL, `District Code` INT NULL, `School Code` TEXT NULL, `County Name` TEXT NULL, `District Name` TEXT NULL, `School Name` TEXT NULL, `District Type` TEXT NULL, `School Type` TEXT NULL, `Educational Option Type` TEXT NULL, `NSLP Provision Status` TEXT NULL, `Charter School (Y/N)` INT NULL, `Charter School Number` TEXT NULL, `Charter Funding Type` TEXT NULL, IRC INT NULL, `Low Grade` TEXT NULL, `High Grade` TEXT NULL, `Enrollment (K-12)` FLOAT NULL, `Free Meal Count (K-12)` FLOAT NULL, `Percent (%) Eligible Free (K-12)` FLOAT NULL, `FRPM Count (K-12)` FLOAT NULL, `Percent (%) Eligible FRPM (K-12)` FLOAT NULL, `Enrollment (Ages 5-17)` FLOAT NULL, `Free Meal Count (Ages 5-17)` FLOAT NULL, `Percent (%) Eligible Free (Ages 5-17)` FLOAT NULL, `FRPM Count (Ages 5-17)` FLOAT NULL, `Percent (%) Eligible FRPM (Ages 5-17)` FLOAT NULL, `2013-14 CALPADS Fall 1 Certification Status` INT NULL, FOREIGN KEY (CDSCode) REFERENCES schools (CDSCode));CREATE TABLE satscores (cds TEXT NOT NULL PRIMARY KEY, rtype TEXT NOT NULL, sname TEXT NULL, dname TEXT NULL, cname TEXT NULL, enroll12 INT NOT NULL, NumTstTakr INT NOT NULL, AvgScrRead INT NULL, AvgScrMath INT NULL, AvgScrWrite INT NULL, NumGE1500 INT NULL, FOREIGN KEY (cds) REFERENCES schools (CDSCode));CREATE TABLE schools (CDSCode TEXT NOT NULL PRIMARY KEY, NCESDist TEXT NULL, NCESSchool TEXT NULL, StatusType TEXT NOT NULL, County TEXT NOT NULL, District TEXT NOT NULL, School TEXT NULL, Street TEXT NULL, StreetAbr TEXT NULL, City TEXT NULL, Zip TEXT NULL, State TEXT NULL, MailStreet TEXT NULL, MailStrAbr TEXT NULL, MailCity TEXT NULL, MailZip TEXT NULL, MailState TEXT NULL, Phone TEXT NULL, Ext TEXT NULL, Website TEXT NULL, OpenDate DATE NULL, ClosedDate DATE NULL, Charter INT NULL, CharterNum TEXT NULL, FundingType TEXT NULL, DOC TEXT NOT NULL, DOCType TEXT NOT NULL, SOC TEXT NULL, SOCType TEXT NULL, EdOpsCode TEXT NULL, EdOpsName TEXT NULL, EILCode TEXT NULL, EILName TEXT NULL, GSoffered TEXT NULL, GSserved TEXT NULL, Virtual TEXT NULL, Magnet INT NULL, Latitude FLOAT NULL, Longitude FLOAT NULL, AdmFName1 TEXT NULL, AdmLName1 TEXT NULL, AdmEmail1 TEXT NULL, AdmFName2 TEXT NULL, AdmLName2 TEXT NULL, AdmEmail2 TEXT NULL, AdmFName3 TEXT NULL, AdmLName3 TEXT NULL, AdmEmail3 TEXT NULL, LastUpdate DATE NOT NULL);
"""


class TestDomainValue(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
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

        cls.workspace = "examples/tests"
        reset_folder(cls.workspace)

    @unittest.skip("skipping for now")
    def test_value_generator(self):
        instance = Instance(
            ddls=schema, name=f"test_random_value_gen", dialect="sqlite"
        )
        for i in range(10):
            table = random.choice(list(instance.tables.keys()))
            print(f"Generating row for table: {table}")
            instance.create_row(table)
        instance.to_db(self.workspace, workspace=self.workspace)

    def test_value_generator_spec(self):
        instance = Instance(
            ddls=schema, name=f"test_random_value_gen_spec", dialect="sqlite"
        )

        column_names = [
            "ClosedDate",
            "OpenDate",
            "Magnet",
            "County",
            "CDSCode",
        ]
        for column_name in column_names:
            normalized_name = instance._normalize_name(column_name)
            pool = instance.column_domains.get_or_create_pool(
                "schools", normalized_name
            )

            for index, op in enumerate(["EQ", "NEQ", "GT", "GTE", "LT", "LTE"]):
                base = 2023 + index
                value = pool.generate_for_spec(op, base)
                print(
                    f"Generated value for {column_name}: {pool.datatype} > {base} {op} {value}"
                )
                aa = {column_name: value}
                index = instance._create_row("schools", concretes={column_name: value})
                row = instance.get_column_data("schools", column_name)[index]

        instance.to_db(self.workspace, workspace=self.workspace)


if __name__ == "__main__":
    runner = unittest.TextTestRunner(stream=sys.stdout, verbosity=2)
    unittest.main(testRunner=runner, exit=False)
