

import sys
import os
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.abspath(os.path.join(current_dir, os.pardir))
sys.path.append(parent_dir)

from src.corekit import get_ctx, rm_folder, reset_folder, rm_folder, DBManager
from sqlglot import exp
import unittest
from sqlglot import parse
import logging
logger = logging.getLogger('src.test')
schema = """CREATE TABLE IF NOT EXISTS `frpm` (`CDSCode` TEXT, `Academic Year` TEXT NOT NULL, `County Code` TEXT, `District Code` INT, `Free Meal Count (K-12)` FLOAT);
CREATE TABLE IF NOT EXISTS `satscores` (`cds` TEXT, `rtype` TEXT, `sname` TEXT, `dname` TEXT, `cname` TEXT, `NumGE1500` INT, PRIMARY KEY (`cds`));
CREATE TABLE IF NOT EXISTS `schools` (`CDSCode` TEXT, `NCESDist` TEXT, `NCESSchool` TEXT, `StatusType` TEXT, PRIMARY KEY (`CDSCode`))"""

# sql = """SELECT T1.`District Code`, T2.`NumGE1500`  FROM frpm AS T1 left JOIN satscores AS T2 on T1.CDSCode = T2.cds where T2.`NumGE1500` is NOT NULL and T1.`District Code` > 15 """ 

# sql = """SELECT T1.`District Code`, T2.`NumGE1500`  FROM frpm AS T1 left JOIN satscores AS T2 on T1.CDSCode = T2.cds where T1.`Academic Year` <> '2023' or T1.`District Code` > 15  """ 

# sql = """SELECT T1.`District Code`, T2.`NumGE1500`  FROM frpm AS T1 INNER JOIN satscores AS T2 on T1.CDSCode = T2.cds where T1.`Academic Year` <> '2023' or T1.`District Code` > 15 """ 

sql = """SELECT  T1.`CDSCode`  FROM frpm AS T1  where T1.`Academic Year` <> '2023' or T1.`District Code` > 15""" 

# ORDER BY T2.`NumGE1500`
# sql = """SELECT T1.`Academic Year`, T1.CDSCode FROM frpm AS T1 where T1.`District Code` > 15 or T1.`Academic Year` <> '2023'  """ 
# sql = """SELECT T1.`District Code` FROM frpm AS T1 where T1.`CDSCode` is NULL  """ 
# where T1.`District Code` > 15 and T1.`Academic Year` <> '2023'
#   where T1.`District Code` > 15 and T1.`Academic Year` <> '2023'
# sql = """SELECT T1.`Academic Year`, T1.CDSCode FROM frpm AS T1 where T1.`District Code` > 15 or T1.`Academic Year` <> '2023'  """ 
# sql = """SELECT T1.`Academic Year` FROM frpm AS T1 where T1.`District Code` > 15 and T1.`Academic Year` = '2023'   """ 
#  or (T1.`District Code` < 5  and T1.`Academic Year` = '2023') 
# or (T1.`District Code` < 5  and T1.`Academic Year` = '2023') 

#   T1.`District Code` + T1.`Free Meal Count (K-12)` > 1000 
#  and T1.`Academic Year` > '2023' or T1.CDSCode = '123456'
# def test_single_query(query, schema, workspace):
#     generator = UExprGenerator(workspace= workspace, schema= schema, query= query, initial_values= {})
#     print(str(generator.plan))
#     generator._one_execution()
# import z3

# a = z3.SeqSort(z3.IntSort())

logging.basicConfig(
    level=logging.INFO,  # or DEBUG, WARNING, ERROR, etc.
    format='[%(asctime)s] %(levelname)s [%(filename)s:%(lineno)d]: %(message)s',
)

class TestGenerator(unittest.TestCase):
    @unittest.skip("skip covaerage")
    def test_executor(self):
        from src.instance.generators import ValueGeneratorRegistry, register_default_generators
        register_default_generators()
        from src.instance.instance import Instance
        from src.runtime.generator import Generator

        with DBManager().get_connection("results/dail/2025-01-21_12-11-32/california_schools_1", "instance_size2.sqlite") as conn:
            schema = conn.get_schema()

        generator = Generator('tests/db', schema, "tests/plan/aggregate.txt", name = 'test_coverage')
        # generator = Generator('tests/db', schema, "datasets/bird/plan/california_schools_7_gold.sql", name = 'test_coverage')
        # instance = Instance.
        # print(generator.plan)        
        result =generator.get_coverage(None)
        print(result)
        assert(len(result.data) == 3)

    @unittest.skip("skip")
    def test_instance_fk(self):
        from src.instance.generators import ValueGeneratorRegistry, register_default_generators

        register_default_generators()
        from src.instance.instance import Instance
        with DBManager().get_connection("results/dail/2025-01-21_12-11-32/california_schools_1", "instance_size2.sqlite") as conn:
            schema = conn.get_schema()
        
        instance =Instance.create(schema= schema, name = 'public', dialect = 'sqlite')
        for _ in range(3):
            for tbl in ['frpm', 'schools']:
            # for tbl in instance._tables:
                instance.create_row(tbl, {})
        
        print(instance._get_foreign_key_constraints())




    @unittest.skip('spj')
    def test_generate_spj(self):
        from src.instance.generators import ValueGeneratorRegistry, register_default_generators
        register_default_generators()
        from src.instance.instance import Instance
        from src.runtime.generator import Generator

        with DBManager().get_connection("results/dail/2025-01-21_12-11-32/california_schools_1", "instance_size2.sqlite") as conn:
            schema = conn.get_schema()
        
        # -- SQLite
        # SELECT schools.School from satscores JOIN schools on satscores.cds = schools.CDSCode where satscores.NumTstTakr > 500 and schools.Magnet = 1

        generator = Generator('tests/db', schema, "datasets/bird/plan/california_schools_7_gold.sql", name = 'test_spj')
        result =generator.generate(max_iter= 8)
        assert len(result.data) >= 3
    @unittest.skip('spj')
    def test_generate_spj_left_join_sort(self):
        from src.instance.generators import ValueGeneratorRegistry, register_default_generators
        register_default_generators()
        from src.instance.instance import Instance
        from src.runtime.generator import Generator

        with DBManager().get_connection("results/dail/2025-01-21_12-11-32/california_schools_1", "instance_size2.sqlite") as conn:
            schema = conn.get_schema()        
        # -- SQLite
        # SELECT schools.School from satscores JOIN schools on satscores.cds = schools.CDSCode where satscores.NumTstTakr > 500 and schools.Magnet = 1
        generator = Generator('tests/db', schema, "datasets/bird/plan/california_schools_8_gold.sql", name = 'test_spj_left')
        print(generator.plan)
        result =generator.generate(max_iter= 8)
        assert len(result.data) >= 1
    
    @unittest.skip('spj_disjunction')
    def test_generate_spj_disjunction(self):
        from src.instance.generators import ValueGeneratorRegistry, register_default_generators
        register_default_generators()
        from src.instance.instance import Instance
        from src.runtime.generator import Generator

        with DBManager().get_connection("results/dail/2025-01-21_12-11-32/california_schools_1", "instance_size2.sqlite") as conn:
            schema = conn.get_schema()
        
        # -- SQLite
        # SELECT schools.School from satscores JOIN schools on satscores.cds = schools.CDSCode where satscores.NumTstTakr > 500 and schools.Magnet = 1

        generator = Generator('tests/db', schema, "tests/plan/spj_disjunction.txt", name = 'test_spj_disjunction')
        result =generator.generate(max_iter= 8)
        assert len(result.data) >= 3

    @unittest.skip("aggregate")
    def test_generate_aggregate(self):
        from src.instance.generators import ValueGeneratorRegistry, register_default_generators

        register_default_generators()
        from src.instance.instance import Instance
        from src.runtime.generator import Generator

        with DBManager().get_connection("results/dail/2025-01-21_12-11-32/california_schools_1", "instance_size2.sqlite") as conn:
            schema = conn.get_schema()

        # -- SQLite
        # SELECT T2.School FROM satscores AS T1 INNER JOIN schools AS T2 ON T1.cds = T2.CDSCode WHERE T2.Magnet = 1 AND T1.NumTstTakr > 500

        # SELECT sname FROM satscores AS T1 INNER JOIN schools AS T2 ON T1.cds = T2.CDSCode WHERE T1.NumTstTakr > 500 AND T2.Magnet = 1

        sql = "select city, sum(`Enrollment (K-12)`) from frpm join schools on frpm.CDSCode = schools.CDSCode GROUP by schools.City "

        # generator = Generator('tests/db', schema, "tests/plan/aggregate.txt", name = 'test_agg')
        generator = Generator('tests/db', schema,  "datasets/bird/plan/california_schools_21_gold.sql", name = 'test_agg')
        print(generator.plan)        
        result =generator.generate(max_iter= 8)

    @unittest.skip("aggregate_having")
    def test_generate_aggregate_having(self):
        from src.instance.generators import ValueGeneratorRegistry, register_default_generators

        register_default_generators()
        from src.instance.instance import Instance
        from src.runtime.generator import Generator

        with DBManager().get_connection("results/dail/2025-01-21_12-11-32/california_schools_1", "instance_size2.sqlite") as conn:
            schema = conn.get_schema()

        # -- SQLite
        # SELECT T2.School FROM satscores AS T1 INNER JOIN schools AS T2 ON T1.cds = T2.CDSCode WHERE T2.Magnet = 1 AND T1.NumTstTakr > 500

        # SELECT sname FROM satscores AS T1 INNER JOIN schools AS T2 ON T1.cds = T2.CDSCode WHERE T1.NumTstTakr > 500 AND T2.Magnet = 1

        sql = "select city, sum(`Enrollment (K-12)`) from frpm join schools on frpm.CDSCode = schools.CDSCode GROUP by schools.City "

        # generator = Generator('tests/db', schema, "tests/plan/aggregate.txt", name = 'test_agg')
        generator = Generator('tests/db', schema,  "datasets/bird/plan/california_schools_26_gold.sql", name = 'test_agg')
        print(generator.plan)        
        result =generator.generate(max_iter= 8)

    # @unittest.skip('skip strftime')
    def test_strftime(self):
        from src.instance.generators import ValueGeneratorRegistry, register_default_generators

        register_default_generators()
        from src.instance.instance import Instance
        from src.runtime.generator import Generator

        with DBManager().get_connection("results/dail/2025-01-21_12-11-32/california_schools_1", "instance_size2.sqlite") as conn:
            schema = conn.get_schema()

        # -- SQLite
        # SELECT T2.School FROM satscores AS T1 INNER JOIN schools AS T2 ON T1.cds = T2.CDSCode WHERE T2.Magnet = 1 AND T1.NumTstTakr > 500
        # SELECT sname FROM satscores AS T1 INNER JOIN schools AS T2 ON T1.cds = T2.CDSCode WHERE T1.NumTstTakr > 500 AND T2.Magnet = 1

        sql = "select city, sum(`Enrollment (K-12)`) from frpm join schools on frpm.CDSCode = schools.CDSCode GROUP by schools.City "

        # generator = Generator('tests/db', schema, "tests/plan/aggregate.txt", name = 'test_agg')
        generator = Generator('tests/db', schema,  "datasets/bird/plan/california_schools_28_gold.sql", name = 'test_strftime')
        print(generator.plan)        
        result =generator.generate(max_iter= 8)


    @unittest.skip("case")
    def test_generate_case(self):
        from src.instance.generators import ValueGeneratorRegistry, register_default_generators
        register_default_generators()
        from src.instance.instance import Instance
        from src.runtime.generator import Generator

        with DBManager().get_connection("results/dail/2025-01-21_12-11-32/california_schools_1", "instance_size2.sqlite") as conn:
            schema = conn.get_schema()

        sql = "select city, sum(`Enrollment (K-12)`) from frpm join schools on frpm.CDSCode = schools.CDSCode GROUP by schools.City "

        generator = Generator('tests/db', schema, "tests/plan/case_when.txt", name = 'test_case_when')
        print(generator.plan)        
        result =generator.generate(max_iter= 8)


    def test_generator(self):
        ...

if __name__ == '__main__':
    reset_folder('tests/db')
    runner = unittest.TextTestRunner(stream=sys.stdout, verbosity=2)
    unittest.main(testRunner=runner, exit=False)


      