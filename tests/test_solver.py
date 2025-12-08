import sys
import os

current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.abspath(os.path.join(current_dir, os.pardir))
sys.path.append(parent_dir)

from src.corekit import get_ctx, rm_folder, reset_folder, rm_folder, DBManager

import unittest

import logging
from src.parseval.generator import Generator
from src.parseval.to_dot import display_uexpr


logger = logging.getLogger("src.test")
schema = """CREATE TABLE frpm (CDSCode TEXT NOT NULL PRIMARY KEY, `Academic Year` TEXT NULL, `County Code` TEXT NULL, `District Code` INT NULL, `School Code` TEXT NULL, `County Name` TEXT NULL, `District Name` TEXT NULL, `School Name` TEXT NULL, `District Type` TEXT NULL, `School Type` TEXT NULL, `Educational Option Type` TEXT NULL, `NSLP Provision Status` TEXT NULL, `Charter School (Y/N)` INT NULL, `Charter School Number` TEXT NULL, `Charter Funding Type` TEXT NULL, IRC INT NULL, `Low Grade` TEXT NULL, `High Grade` TEXT NULL, `Enrollment (K-12)` FLOAT NULL, `Free Meal Count (K-12)` FLOAT NULL, `Percent (%) Eligible Free (K-12)` FLOAT NULL, `FRPM Count (K-12)` FLOAT NULL, `Percent (%) Eligible FRPM (K-12)` FLOAT NULL, `Enrollment (Ages 5-17)` FLOAT NULL, `Free Meal Count (Ages 5-17)` FLOAT NULL, `Percent (%) Eligible Free (Ages 5-17)` FLOAT NULL, `FRPM Count (Ages 5-17)` FLOAT NULL, `Percent (%) Eligible FRPM (Ages 5-17)` FLOAT NULL, `2013-14 CALPADS Fall 1 Certification Status` INT NULL, FOREIGN KEY (CDSCode) REFERENCES schools (CDSCode));CREATE TABLE satscores (cds TEXT NOT NULL PRIMARY KEY, rtype TEXT NOT NULL, sname TEXT NULL, dname TEXT NULL, cname TEXT NULL, enroll12 INT NOT NULL, NumTstTakr INT NOT NULL, AvgScrRead INT NULL, AvgScrMath INT NULL, AvgScrWrite INT NULL, NumGE1500 INT NULL, FOREIGN KEY (cds) REFERENCES schools (CDSCode));CREATE TABLE schools (CDSCode TEXT NOT NULL PRIMARY KEY, NCESDist TEXT NULL, NCESSchool TEXT NULL, StatusType TEXT NOT NULL, County TEXT NOT NULL, District TEXT NOT NULL, School TEXT NULL, Street TEXT NULL, StreetAbr TEXT NULL, City TEXT NULL, Zip TEXT NULL, State TEXT NULL, MailStreet TEXT NULL, MailStrAbr TEXT NULL, MailCity TEXT NULL, MailZip TEXT NULL, MailState TEXT NULL, Phone TEXT NULL, Ext TEXT NULL, Website TEXT NULL, OpenDate DATE NULL, ClosedDate DATE NULL, Charter INT NULL, CharterNum TEXT NULL, FundingType TEXT NULL, DOC TEXT NOT NULL, DOCType TEXT NOT NULL, SOC TEXT NULL, SOCType TEXT NULL, EdOpsCode TEXT NULL, EdOpsName TEXT NULL, EILCode TEXT NULL, EILName TEXT NULL, GSoffered TEXT NULL, GSserved TEXT NULL, Virtual TEXT NULL, Magnet INT NULL, Latitude FLOAT NULL, Longitude FLOAT NULL, AdmFName1 TEXT NULL, AdmLName1 TEXT NULL, AdmEmail1 TEXT NULL, AdmFName2 TEXT NULL, AdmLName2 TEXT NULL, AdmEmail2 TEXT NULL, AdmFName3 TEXT NULL, AdmLName3 TEXT NULL, AdmEmail3 TEXT NULL, LastUpdate DATE NOT NULL);
"""

logging.basicConfig(
    level=logging.INFO,  # or DEBUG, WARNING, ERROR, etc.
    format="[%(asctime)s] %(levelname)s [%(filename)s:%(lineno)d]: %(message)s",
)

INDEX = 21


class TestGenerator(unittest.TestCase):

    @unittest.skip("skipping for now")
    def test_column_domain(self):
        from src.parseval.smt.domain import ColumnDomainPool, DomainSpec

        pool = ColumnDomainPool()
        domain = DomainSpec("t1", "c1", "TEXT", unique=True, generated={})
        domain2 = DomainSpec("t1", "c2", "INT", unique=False)
        domain3 = DomainSpec("t1", "c3", "REAL", unique=False)
        domain4 = DomainSpec("t1", "c4", "DATETIME", unique=False)

        pool.register_domain(domain)
        pool.register_domain(domain2)
        pool.register_domain(domain3)
        pool.register_domain(domain4)

        value_pool1 = pool.get_or_create_pool("a1", "t1", "c1")
        value_pool2 = pool.get_or_create_pool("a2", "t1", "c2")
        value_pool3 = pool.get_or_create_pool("a3", "t1", "c3")
        value_pool4 = pool.get_or_create_pool("a4", "t1", "c4")

        value_pool1.expand_domain(4)
        logger.info(value_pool1.get_domain_values())

        value_pool2.expand_domain(10)
        value_pool3.expand_domain(10)
        value_pool4.expand_domain(10)

        logger.info(value_pool2.get_domain_values())
        logger.info(value_pool3.get_domain_values())
        logger.info(value_pool4.get_domain_values())

    @unittest.skip("skipping for now")
    def test_columndomain(self):
        from src.parseval.solver.domain import CSPSolver, DomainSpec, ValuePool

        solver = CSPSolver()

        solver.register_domain(
            DomainSpec(table_name="t1", column_name="c1", datatype="INT", unique=True)
        )

        # domain = ColumnDomain(
        #     table_name="t1", column_name="c1", datatype="TEXT", unique=True
        # )
        # value = domain.sample(cast_type="datetime", generated_values={1, 3, 5})
        # logging.info(value)

    @unittest.skip("skipping for now")
    def test_spj(self):
        plan_path = f"datasets/bird/plan/california_schools_{INDEX}_gold.sql"

        generator = Generator(schema=schema, query=plan_path)

        generator.query.pprint()
        tracer = generator.generate(max_iter=1, threshold=1)

        print("========" * 10)
        print(display_uexpr(tracer.root_constraint))

    # @unittest.skip("skipping for now")
    def test_smtsolver(self):
        from src.parseval.smt.smt_solver import SMTSolver

        from src.parseval import symbol as sym

        variables = []
        for i in range(5):
            variables.append(sym.Variable(f"var{i}", dtype="STRING", concrete=i))

        conditions = []

        # for i in range(4):
        #     cond = variables[i].ne(variables[i + 1])
        #     conditions.append(cond)
        from datetime import datetime

        variable6 = sym.Variable(
            f"var5", dtype="datetime", concrete=datetime(2021, 1, 1, 1, 1, 1)
        )

        # conditions.append(
        #     variable6 > sym.Const("2019-05-05 12:00:00", dtype="datetime")
        # )

        strftime_var = sym.Strftime(
            "strftime", variable6, sym.Const("%Y-%d", dtype="STRING"), dtype="STRING"
        )
        cond = strftime_var > sym.Const("2020-12", dtype="STRING")
        conditions.append(cond)

        solver = SMTSolver("z3")
        context = {}
        result = solver.solve(variables, conditions, context=context)
        logger.info(result)

        logger.info(context.get("str_format", []))

        logger.info(cond.concrete)

    @unittest.skip("skipping for now")
    def test_cspsolver(self):
        from src.parseval.plan import rex
        from src.parseval.smt.speculative import SpeculativeSolver
        from src.parseval import symbol as sym

        from src.parseval.generator import get_domainpool, ExprEncoder

        from src.parseval.instance import Instance

        instance = Instance(ddls=schema)

        pool_mgr = get_domainpool(instance)

        solver = SpeculativeSolver(name="speculative", pool_mgr=pool_mgr)
        var_to_columnref = {}
        columnref_to_var = {}

        school_tbl = instance.catalog.get_table("schools")
        constraints = [
            rex.sqlglot_exp.EQ(
                this=school_tbl.columns[0],
                expression=rex.sqlglot_exp.Literal.string("LOS ANGELES"),
            )
        ]
        conditions = []
        variables = {}
        for constraint in constraints:
            columnrefs = set(constraint.find_all(rex.ColumnRef))
            for columnref in columnrefs:
                var_name = f"{columnref.qualified_name}"
                if var_name not in var_to_columnref:
                    domain = pool_mgr.get_or_create_pool(
                        var_name,
                        table_name=columnref.table,
                        column_name=columnref.name,
                    )
                    var = sym.Variable(var_name, dtype=domain.datatype)
                    var_to_columnref[var_name] = columnref
                    columnref_to_var[columnref] = var
                    variables[var_name] = var
            condition = ExprEncoder().visit(constraint, context={**columnref_to_var})
            conditions.append(condition)

        logging.info(
            solver.supports(variables=variables, constraints=conditions, context={})
        )
        solver.variables.update(variables)
        res = solver.solve(variables=variables, constraints=conditions, context={})

        logger.info(res)

        #                 if domain.unique:
        #                     data = domain.domain.generated
        #                     values = [sym.Const(d, dtype=domain.datatype) for d in data]
        #                     unique_constraint = sym.Distinct(var, *values, dtype="bool")
        #                     solver.add_constraint(unique_constraint)

        # solver.solve()

    @unittest.skip("skipping for now")
    def test_solver(self):
        from src.parseval.smt.solver import Solver
        from src.parseval import symbol as sym

        variables = []
        for i in range(5):
            variables.append(sym.Variable(f"var{i}", dtype="INT", concrete=i))

        solver = Solver(None)
        solver.add_constraint(variables[0] > 100)

        logger.info(solver.solve())


if __name__ == "__main__":

    reset_folder("tests/db")
    runner = unittest.TextTestRunner(stream=sys.stdout, verbosity=2)
    unittest.main(testRunner=runner, exit=False)
