import sys
import os

current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.abspath(os.path.join(current_dir, os.pardir))
sys.path.append(parent_dir)

from sqlglot import exp
import unittest, json
import logging
from src.parseval.plan.planner import Planner
from src.parseval.instance import Instance
from src.parseval.query import preprocess_sql
from src.parseval.data_generator import DataGenerator
from src.parseval.uexpr.uexprs import UExprToConstraint
from src.parseval.plan.speculate import Speculative
from src.parseval.configuration import Config

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
from src.parseval.to_dot import display_uexpr
from src.parseval.logger import Logger
Logger(
    verbose={
        "coverage": True,
        "symbolic": False,
        "smt": True,
        "db": False,
    },
    log_file="log.log"
)

logger = logging.getLogger("parseval.coverage")

class TestGenerator(unittest.TestCase):
    # @unittest.skip("passed")
    def test_spj_disjunct(self):
        config = Config()
        for i in range(1):
            logger.info("==== Running test_parse_spj iteration {} ====".format(i))
            print("==== Running test_parse_spj iteration {} ====".format(i))
            instance = Instance(ddls=schema, name=f"test_spj_disjunct{i}", dialect="sqlite")
            tracer = UExprToConstraint()
                
            sql = """SELECT  T1.`CDSCode`, CASE WHEN T1.`School Name`  = 'SFU' THEN 1 WHEN T1.`School Name`  = 'SFU2' THEN 2 ELSE 0 END  FROM frpm AS T1  where T1.`Academic Year`  <> '2023' or CAST(  T1.`District Code`  AS INT) > 15"""
            sql = "SELECT T1.`sname` FROM satscores AS T1 JOIN frpm AS T2 on T1.cds = T2.CDSCode where T1.`NumGE1500` > 100 or T1.`NumGE1500` < 80 " #order by `NumGE1500`
            expr = preprocess_sql(sql, instance, dialect="sqlite")
            
            speculate = Speculative(instance= instance, expr = expr, config= config, tracer= tracer)
            
            speculate.encode()
            logger.info(f"Speculative done")
            
            
            
            
            
            # generator = DataGenerator(expr= expr, instance= instance, name=f"test_spj_disjunct{i}", workspace="examples/tests", verbose=True)
            # generator.generate()
            
            # generator.instance.to_db("examples/tests", f"test_spj_disjunct{i}")
                
            display_uexpr(tracer.root).write(
                "examples/tests/dot_coverage_" + instance.name + ".png", format="png"
            )

    @unittest.skip("passed")
    def test_groupby(self):
        
        for i in range(1):
            logger.info("==== Running test_groupby iteration {} ====".format(i))
            print("==== Running test_groupby iteration {} ====".format(i))
            instance = Instance(ddls=schema, name=f"test_groupby_{i}", dialect="sqlite")
                
            sql = """SELECT GSserved FROM schools WHERE City = 'Adelanto' GROUP BY GSserved """
            expr = preprocess_sql(sql, instance, dialect="sqlite")
            generator = DataGenerator(expr= expr, instance= instance, name=f"test_groupby_{i}", workspace="examples/tests", verbose=True)
            generator.generate()
            generator.instance.to_db("examples/tests", f"test_groupby_{i}")
            display_uexpr(generator.tracer.root).write(
                "examples/tests/dot_coverage_groupby" + instance.name + ".png", format="png"
            )
    @unittest.skip("passed")
    def test_aggregate(self):
        for i in range(1):
            logger.info("==== Running test_parse_spj iteration {} ====".format(i))
            print("==== Running test_parse_spj iteration {} ====".format(i))
            instance = Instance(ddls=schema, name=f"test_aggregate{i}", dialect="sqlite")
            sql = """SELECT GSserved, count(NCESDist) FROM schools WHERE City = 'Adelanto' GROUP BY GSserved """
            expr = preprocess_sql(sql, instance, dialect="sqlite")
            generator = DataGenerator(expr= expr, instance= instance, name=f"test_aggregate{i}", workspace="examples/tests", verbose=True)
            generator.generate()
            generator.instance.to_db("examples/tests", f"test_aggregate{i}")
                
            display_uexpr(generator.tracer.root).write(
                "examples/tests/dot_coverage_aggregate" + instance.name + ".png", format="png"
            )
    @unittest.skip("passed")
    def test_having(self):
        for i in range(1):
            logger.info("==== Running test_having iteration {} ====".format(i))
            instance = Instance(ddls=schema, name=f"test_{i}", dialect="sqlite")
            sql = """SELECT GSserved, count(NCESDist) FROM schools WHERE City = 'Adelanto' GROUP BY GSserved having count(NCESDist) > 1 """
            expr = preprocess_sql(sql, instance, dialect="sqlite")
            generator = DataGenerator(expr= expr, instance= instance, name=f"test_{i}", workspace="examples/tests", verbose=True)
            generator.generate()
            generator.instance.to_db("examples/tests", f"test_having{i}")
                
            display_uexpr(generator.tracer.root).write(
                "examples/tests/dot_coverage_having" + instance.name + ".png", format="png"
            )
    
    @unittest.skip("case when passed")
    def test_case_when(self):
        
        for i in range(1):
            logger.info("==== Running test_parse_spj iteration {} ====".format(i))
            print("==== Running test_parse_spj iteration {} ====".format(i))
            instance = Instance(ddls=schema, name=f"test_case_when{i}", dialect="sqlite")
                
            sql = """SELECT  T1.`CDSCode`, CASE WHEN T1.`School Name`  = 'SFU' THEN 1 WHEN T1.`School Name`  = 'SFU2' THEN 2 ELSE 0 END  FROM frpm AS T1  where T1.`Academic Year`  <> '2023' """
            expr = preprocess_sql(sql, instance, dialect="sqlite")
            generator = DataGenerator(expr= expr, instance= instance, name=f"test_case_when{i}", workspace="examples/tests", verbose=True)
            generator.generate()
            
            generator.instance.to_db("examples/tests", f"test_case_when_{i}")
                
            display_uexpr(generator.tracer.root).write(
                "examples/tests/dot_coverage_" + instance.name + ".png", format="png"
            )
    @unittest.skip("passed")
    def test_scalar(self):
        for i in range(1):
            logger.info("==== Running test_scalar iteration {} ====".format(i))
            print("==== Running test_scalar iteration {} ====".format(i))
            instance = Instance(ddls=schema, name=f"test_scalar{i}", dialect="sqlite")
                
            sql = """SELECT  T1.`CDSCode`, (SELECT count(*) FROM schools) as cnt  FROM frpm AS T1  where T1.`Academic Year`  <> '2023' """
            sql = """SELECT T1.`sname` FROM satscores AS T1 where T1.cds = (SELECT T2.CDSCode from frpm AS T2 order by T2.CDSCode limit 1)"""
            
            # sql = """SELECT T1.`sname` FROM satscores AS T1 where exists (select 1 from frpm AS T2 where T1.cds = T2.CDSCode)"""
            # sql = "SELECT T1.`sname` FROM satscores AS T1 JOIN frpm AS T2 on T1.cds = T2.CDSCode where T1.`NumGE1500` > 100 or T1.`NumGE1500` < 80 "
            expr = preprocess_sql(sql, instance, dialect="sqlite")
            generator = DataGenerator(expr= expr, instance= instance, name=f"test_scalar{i}", workspace="examples/tests", verbose=True)
            generator.generate()
            generator.instance.to_db("examples/tests", f"test_scalar_{i}")
            display_uexpr(generator.tracer.root).write(
                "examples/tests/dot_coverage_scalar" + instance.name + ".png", format="png"
            )

if __name__ == "__main__":
    reset_folder("examples/tests")
    runner = unittest.TextTestRunner(stream=sys.stdout, verbosity=2)
    unittest.main(testRunner=runner, exit=False)
