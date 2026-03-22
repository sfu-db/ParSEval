import sys
from sqlglot import exp
import unittest, json
import logging
from parseval.plan.planner import Planner
from parseval.instance import Instance
from parseval.query import preprocess_sql
from parseval.uexpr.uexprs import UExprToConstraint, Constraint

schema = """CREATE TABLE frpm (CDSCode TEXT NOT NULL PRIMARY KEY, `Academic Year` TEXT NULL, `County Code` TEXT NULL, `District Code` INT NULL, `School Code` TEXT NULL, `County Name` TEXT NULL, `District Name` TEXT NULL, `School Name` TEXT NULL, `District Type` TEXT NULL, `School Type` TEXT NULL, `Educational Option Type` TEXT NULL, `NSLP Provision Status` TEXT NULL, `Charter School (Y/N)` INT NULL, `Charter School Number` TEXT NULL, `Charter Funding Type` TEXT NULL, IRC INT NULL, `Low Grade` TEXT NULL, `High Grade` TEXT NULL, `Enrollment (K-12)` FLOAT NULL, `Free Meal Count (K-12)` FLOAT NULL, `Percent (%) Eligible Free (K-12)` FLOAT NULL, `FRPM Count (K-12)` FLOAT NULL, `Percent (%) Eligible FRPM (K-12)` FLOAT NULL, `Enrollment (Ages 5-17)` FLOAT NULL, `Free Meal Count (Ages 5-17)` FLOAT NULL, `Percent (%) Eligible Free (Ages 5-17)` FLOAT NULL, `FRPM Count (Ages 5-17)` FLOAT NULL, `Percent (%) Eligible FRPM (Ages 5-17)` FLOAT NULL, `2013-14 CALPADS Fall 1 Certification Status` INT NULL, FOREIGN KEY (CDSCode) REFERENCES schools (CDSCode));CREATE TABLE satscores (cds TEXT NOT NULL PRIMARY KEY, rtype TEXT NOT NULL, sname TEXT NULL, dname TEXT NULL, cname TEXT NULL, enroll12 INT NOT NULL, NumTstTakr INT NOT NULL, AvgScrRead INT NULL, AvgScrMath INT NULL, AvgScrWrite INT NULL, NumGE1500 INT NULL, FOREIGN KEY (cds) REFERENCES schools (CDSCode));CREATE TABLE schools (CDSCode TEXT NOT NULL PRIMARY KEY, NCESDist TEXT NULL, NCESSchool TEXT NULL, StatusType TEXT NOT NULL, County TEXT NOT NULL, District TEXT NOT NULL, School TEXT NULL, Street TEXT NULL, StreetAbr TEXT NULL, City TEXT NULL, Zip TEXT NULL, State TEXT NULL, MailStreet TEXT NULL, MailStrAbr TEXT NULL, MailCity TEXT NULL, MailZip TEXT NULL, MailState TEXT NULL, Phone TEXT NULL, Ext TEXT NULL, Website TEXT NULL, OpenDate DATE NULL, ClosedDate DATE NULL, Charter INT NULL, CharterNum TEXT NULL, FundingType TEXT NULL, DOC TEXT NOT NULL, DOCType TEXT NOT NULL, SOC TEXT NULL, SOCType TEXT NULL, EdOpsCode TEXT NULL, EdOpsName TEXT NULL, EILCode TEXT NULL, EILName TEXT NULL, GSoffered TEXT NULL, GSserved TEXT NULL, Virtual TEXT NULL, Magnet INT NULL, Latitude FLOAT NULL, Longitude FLOAT NULL, AdmFName1 TEXT NULL, AdmLName1 TEXT NULL, AdmEmail1 TEXT NULL, AdmFName2 TEXT NULL, AdmLName2 TEXT NULL, AdmEmail2 TEXT NULL, AdmFName3 TEXT NULL, AdmLName3 TEXT NULL, AdmEmail3 TEXT NULL, LastUpdate DATE NOT NULL);
"""
# sql = """SELECT T1.`District Code`, T2.`NumGE1500`  FROM frpm AS T1 left JOIN satscores AS T2 on T1.CDSCode = T2.cds where T2.`NumGE1500` is NOT NULL and T1.`District Code` > 15 """

# sql = """SELECT T1.`District Code`, T2.`NumGE1500`  FROM frpm AS T1 left JOIN satscores AS T2 on T1.CDSCode = T2.cds where T1.`Academic Year` <> '2023' or T1.`District Code` > 15  """

# sql = """SELECT T1.`District Code`, T2.`NumGE1500`  FROM frpm AS T1 INNER JOIN satscores AS T2 on T1.CDSCode = T2.cds where T1.`Academic Year` <> '2023' or T1.`District Code` > 15 """


from parseval.logger import Logger
from parseval.to_dot import display_uexpr
from parseval.plan import build_context_from_instance
from parseval.plan.planner import build_graph_from_scopes

Logger(
    verbose={
        "coverage": True,
        "symbolic": True,
        "smt": True,
        "db": False,
    }
)


class TestPlanner(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        logging.info("Setting up TestPlanner class...")
        cls.instance = Instance(ddls=schema, name="test", dialect="sqlite")
        for _ in range(5):
            cls.instance.create_row("frpm")
            cls.instance.create_row("satscores")
        cls.ctx = build_context_from_instance(cls.instance)

    @unittest.skip("skipping for now")
    def test_parse_spj(self):

        tracer = UExprToConstraint()
        sql = """SELECT  T1.`CDSCode`, CASE WHEN T1.`School Name`  = 'SFU' THEN 1 WHEN T1.`School Name`  = 'SFU2' THEN 2 ELSE 0 END  FROM frpm AS T1  where T1.`Academic Year`  <> '2023' or CAST(  T1.`District Code`  AS INT) > 15"""
        sql = "SELECT T1.`sname` FROM satscores AS T1 JOIN frpm AS T2 on T1.cds = T2.CDSCode where T1.`NumGE1500` > 100"  # order by `NumGE1500`
        expr = preprocess_sql(sql, self.instance, dialect="sqlite")

        graph_nodes = build_graph_from_scopes(expr)

        for nodeid in graph_nodes.get_dependency_order():
            scope_node = graph_nodes.get_node(nodeid)
            # scope_node = graph_nodes[nodeid]

            planner = Planner(
                ctx=self.ctx, scope_node=scope_node, tracer=tracer, dialect="sqlite"
            )
            planner.encode()

        display_uexpr(tracer.root).write(
            "examples/tests/dot_planner_spj.png", format="png"
        )

    @unittest.skip("skipping for now")
    def test_spj_disjunct(self):
        for i in range(1):
            print("==== Running test_parse_spj iteration {} ====".format(i))
            sql = """SELECT  T1.`CDSCode`, CASE WHEN T1.`School Name`  = 'SFU' THEN 1 WHEN T1.`School Name`  = 'SFU2' THEN 2 ELSE 0 END  FROM frpm AS T1  where T1.`Academic Year`  <> '2023' or CAST(  T1.`District Code`  AS INT) > 15"""
            sql = "SELECT T1.`sname` FROM satscores AS T1 JOIN frpm AS T2 on T1.cds = T2.CDSCode where T1.`NumGE1500` > 100 or T1.`NumGE1500` < 80 "  # order by `NumGE1500`
            expr = preprocess_sql(sql, self.instance, dialect="sqlite")
            graph_nodes = build_graph_from_scopes(expr)
            tracer = UExprToConstraint()

            for nodeid in graph_nodes.get_dependency_order():
                scope_node = graph_nodes.get_node(nodeid)
                planner = Planner(
                    ctx=self.ctx, scope_node=scope_node, tracer=tracer, dialect="sqlite"
                )
                planner.encode()
            display_uexpr(tracer.root).write(
                "examples/tests/dot_planner_spj_disjunct.png", format="png"
            )

    @unittest.skip("skipping for now")
    def test_groupby(self):
        tracer = UExprToConstraint()
        sql = """SELECT GSserved, COUNT(CDSCode) FROM schools WHERE City <> 'Adelanto' GROUP BY GSserved """  # having COUNT(CDSCode) > 10
        expr = preprocess_sql(sql, self.instance, dialect="sqlite")

        graph_nodes = build_graph_from_scopes(expr)

        for nodeid in graph_nodes.get_dependency_order():
            scope_node = graph_nodes.get_node(nodeid)
            planner = Planner(
                ctx=self.ctx, scope_node=scope_node, tracer=tracer, dialect="sqlite"
            )
            planner.encode()

        display_uexpr(tracer.root).write(
            "examples/tests/dot_planner_groupby.png", format="png"
        )

    # @unittest.skip("skipping for now")
    def test_groupby_having(self):
        tracer = UExprToConstraint()
        sql = """SELECT GSserved FROM schools WHERE City <> 'Adelanto' GROUP BY GSserved  having COUNT(distinct CDSCode) > 10"""
        expr = preprocess_sql(sql, self.instance, dialect="sqlite")

        graph_nodes = build_graph_from_scopes(expr)

        for nodeid in graph_nodes.get_dependency_order():
            scope_node = graph_nodes.get_node(nodeid)
            planner = Planner(
                ctx=self.ctx, scope_node=scope_node, tracer=tracer, dialect="sqlite"
            )
            planner.encode()

        display_uexpr(tracer.root).write(
            "examples/tests/dot_planner_groupby_having.png", format="png"
        )

    @unittest.skip("skipping for now")
    def test_sclar_query(self):
        tracer = UExprToConstraint()
        sql = """SELECT T1.`sname` FROM satscores AS T1 where T1.cds = (SELECT T2.CDSCode from frpm AS T2 order by T2.CDSCode limit 1)"""
        expr = preprocess_sql(sql, self.instance, dialect="sqlite")

        graph_nodes = build_graph_from_scopes(expr)

        for nodeid in graph_nodes.get_dependency_order():
            scope_node = graph_nodes.get_node(nodeid)
            planner = Planner(
                ctx=self.ctx, scope_node=scope_node, tracer=tracer, dialect="sqlite"
            )
            planner.encode()

        display_uexpr(tracer.root).write(
            "examples/tests/dot_planner_scalar.png", format="png"
        )


if __name__ == "__main__":
    runner = unittest.TextTestRunner(stream=sys.stdout, verbosity=2)
    unittest.main(testRunner=runner, exit=False)
