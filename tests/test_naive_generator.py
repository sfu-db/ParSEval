

import sys
import os
# Get the current directory (where your_script.py resides)
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.abspath(os.path.join(current_dir, os.pardir))
sys.path.append(parent_dir)
from src.corekit import get_ctx, rm_folder, reset_folder

from src.uexpr.instance import Instance
from src.runtime.naive.executor import NaiveExecutor
from src.runtime.naive.generator import UExprGenerator
from sqlglot import exp, parse_one
import logging, time
import pandas as pd
logger = logging.getLogger('src.test.naive')

schema = """CREATE TABLE IF NOT EXISTS `frpm` (`CDSCode` TEXT, `Academic Year` TEXT, `County Code` TEXT, `District Code` INT, `School Code` TEXT, `County Name` TEXT, `District Name` TEXT, `School Name` TEXT, `District Type` TEXT, `School Type` TEXT, `Educational Option Type` TEXT, `NSLP Provision Status` TEXT, `Charter School (Y/N)` INT, `Charter School Number` TEXT, `Charter Funding Type` TEXT, `IRC` INT, `Low Grade` TEXT, `High Grade` TEXT, `Enrollment (K-12)` FLOAT, `Free Meal Count (K-12)` FLOAT, `Percent (%) Eligible Free (K-12)` FLOAT, `FRPM Count (K-12)` FLOAT, `Percent (%) Eligible FRPM (K-12)` FLOAT, `Enrollment (Ages 5-17)` FLOAT, `Free Meal Count (Ages 5-17)` FLOAT, `Percent (%) Eligible Free (Ages 5-17)` FLOAT, `FRPM Count (Ages 5-17)` FLOAT, `Percent (%) Eligible FRPM (Ages 5-17)` FLOAT, `2013-14 CALPADS Fall 1 Certification Status` INT, PRIMARY KEY (`CDSCode`), FOREIGN KEY (`CDSCode`) REFERENCES schools (`CDSCode`));CREATE TABLE IF NOT EXISTS `satscores` (`cds` TEXT, `rtype` TEXT, `sname` TEXT, `dname` TEXT, `cname` TEXT, `enroll12` INT, `NumTstTakr` INT, `AvgScrRead` INT, `AvgScrMath` INT, `AvgScrWrite` INT, `NumGE1500` INT, PRIMARY KEY (`cds`), FOREIGN KEY (`cds`) REFERENCES schools (`CDSCode`));CREATE TABLE IF NOT EXISTS `schools` (`CDSCode` TEXT, `NCESDist` TEXT, `NCESSchool` TEXT, `StatusType` TEXT, `County` TEXT, `District` TEXT, `School` TEXT, `Street` TEXT, `StreetAbr` TEXT, `City` TEXT, `Zip` TEXT, `State` TEXT, `MailStreet` TEXT, `MailStrAbr` TEXT, `MailCity` TEXT, `MailZip` TEXT, `MailState` TEXT, `Phone` TEXT, `Ext` TEXT, `Website` TEXT, `OpenDate` DATE, `ClosedDate` DATE, `Charter` INT, `CharterNum` TEXT, `FundingType` TEXT, `DOC` TEXT, `DOCType` TEXT, `SOC` TEXT, `SOCType` TEXT, `EdOpsCode` TEXT, `EdOpsName` TEXT, `EILCode` TEXT, `EILName` TEXT, `GSoffered` TEXT, `GSserved` TEXT, `Virtual` TEXT, `Magnet` INT, `Latitude` FLOAT, `Longitude` FLOAT, `AdmFName1` TEXT, `AdmLName1` TEXT, `AdmEmail1` TEXT, `AdmFName2` TEXT, `AdmLName2` TEXT, `AdmEmail2` TEXT, `AdmFName3` TEXT, `AdmLName3` TEXT, `AdmEmail3` TEXT, `LastUpdate` DATE, PRIMARY KEY (`CDSCode`))
"""

sql = """ SELECT T2.Phone, T2.Ext FROM satscores AS T1 INNER JOIN schools AS T2 ON T1.cds = T2.CDSCode ORDER BY T1.AvgScrWrite DESC LIMIT 332, 1 """

# sql = """ SELECT T2.AdmEmail1 FROM schools AS T2  WHERE  strftime('%Y', T2.OpenDate) BETWEEN '2009' AND '2010' """

# JOIN satscores AS T2 on T1.CDSCode = T2.cds
# sql = """SELECT NCESDist, NCESSchool FROM schools WHERE CDSCode = (SELECT cds FROM satscores ORDER BY NumGE1500 DESC LIMIT 1 OFFSET 332)"""
# T1.`District Code` > 15 and T1.`Academic Year` = '2023'
 #INNER JOIN schools AS T2 ON T1.CDSCode = T2.CDSCode



def test_instance(workspace):
    start_time = time.perf_counter()
    context = ({}, [], {}, {})
    instance = Instance.initialize(context, schema, {}, size = 1)
    instance.to_db(workspace, f'{instance.name}.sqlite')
    end_time = time.perf_counter() - start_time
    logger.info(f'running time: {end_time}')
def test_generator(dataset_fp, index, workspace):
    df = pd.read_json(dataset_fp, lines= True)
    for idx, row in df.iterrows():
        qidx = idx + 1
        if qidx != index:
            continue
        gold = row['pair'][0]

        print(gold)
        
        ddl = row['ddl']
        name = row.get('name') if 'name' in row else row.get('benchmark', f'q{qidx}')
        tpath = os.path.join(workspace, f'{name}_{qidx}')
        reset_folder(tpath)
        generator = UExprGenerator(workspace= tpath, schema= ddl, query= gold, initial_values= {}, db_id = name, question_id = index)
        
        # print(generator.plan.root)
        # r = generator._one_execution( max_tries= 2)
        generator.generate(2)
    # logger.info(r)
    # print(generator.context)

if __name__ == '__main__':
    get_ctx(log_level = 'INFO')
    workspace = 'tests/db'
    reset_folder(workspace)
    dataset_fp = "datasets/bird/bird_dail2.jsonlines"
    # for i in range(92, 220):
    #     test_generator(dataset_fp= dataset_fp, index= i, workspace= workspace)
    test_generator(dataset_fp= dataset_fp, index= 25, workspace= workspace)
    # test_instance(workspace)
    rm_folder(get_ctx().result_path)

  