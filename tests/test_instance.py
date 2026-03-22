import sys
import os
from parseval.instance import Instance
import unittest
from sqlglot import MappingSchema, exp
from sqlglot.optimizer import annotate_types, normalize_identifiers, normalize, qualify
from parseval.query import preprocess_sql
from sqlglot import parse_one
import logging

logger = logging.getLogger("src.test")
schema = """CREATE TABLE frpm (CDSCode TEXT NOT NULL PRIMARY KEY, `Academic Year` TEXT NULL, `County Code` TEXT NULL, `District Code` INT NULL, `School Code` TEXT NULL, `County Name` TEXT NULL, `District Name` TEXT NULL, `School Name` TEXT NULL, `District Type` TEXT NULL, `School Type` TEXT NULL, `Educational Option Type` TEXT NULL, `NSLP Provision Status` TEXT NULL, `Charter School (Y/N)` INT NULL, `Charter School Number` TEXT NULL, `Charter Funding Type` TEXT NULL, IRC INT NULL, `Low Grade` TEXT NULL, `High Grade` TEXT NULL, `Enrollment (K-12)` FLOAT NULL, `Free Meal Count (K-12)` FLOAT NULL, `Percent (%) Eligible Free (K-12)` FLOAT NULL, `FRPM Count (K-12)` FLOAT NULL, `Percent (%) Eligible FRPM (K-12)` FLOAT NULL, `Enrollment (Ages 5-17)` FLOAT NULL, `Free Meal Count (Ages 5-17)` FLOAT NULL, `Percent (%) Eligible Free (Ages 5-17)` FLOAT NULL, `FRPM Count (Ages 5-17)` FLOAT NULL, `Percent (%) Eligible FRPM (Ages 5-17)` FLOAT NULL, `2013-14 CALPADS Fall 1 Certification Status` INT NULL, FOREIGN KEY (CDSCode) REFERENCES schools (CDSCode));CREATE TABLE satscores (cds TEXT NOT NULL PRIMARY KEY, rtype TEXT NOT NULL, sname TEXT NULL, dname TEXT NULL, cname TEXT NULL, enroll12 INT NOT NULL, NumTstTakr INT NOT NULL, AvgScrRead INT NULL, AvgScrMath INT NULL, AvgScrWrite INT NULL, NumGE1500 INT NULL, FOREIGN KEY (cds) REFERENCES schools (CDSCode));CREATE TABLE schools (CDSCode TEXT NOT NULL PRIMARY KEY, NCESDist TEXT NULL, NCESSchool TEXT NULL, StatusType TEXT NOT NULL, County TEXT NOT NULL, District TEXT NOT NULL, School TEXT NULL, Street TEXT NULL, StreetAbr TEXT NULL, City TEXT NULL, Zip TEXT NULL, State TEXT NULL, MailStreet TEXT NULL, MailStrAbr TEXT NULL, MailCity TEXT NULL, MailZip TEXT NULL, MailState TEXT NULL, Phone TEXT NULL, Ext TEXT NULL, Website TEXT NULL, OpenDate DATE NULL, ClosedDate DATE NULL, Charter INT NULL, CharterNum TEXT NULL, FundingType TEXT NULL, DOC TEXT NOT NULL, DOCType TEXT NOT NULL, SOC TEXT NULL, SOCType TEXT NULL, EdOpsCode TEXT NULL, EdOpsName TEXT NULL, EILCode TEXT NULL, EILName TEXT NULL, GSoffered TEXT NULL, GSserved TEXT NULL, Virtual TEXT NULL, Magnet INT NULL, Latitude FLOAT NULL, Longitude FLOAT NULL, AdmFName1 TEXT NULL, AdmLName1 TEXT NULL, AdmEmail1 TEXT NULL, AdmFName2 TEXT NULL, AdmLName2 TEXT NULL, AdmEmail2 TEXT NULL, AdmFName3 TEXT NULL, AdmLName3 TEXT NULL, AdmEmail3 TEXT NULL, LastUpdate DATE NOT NULL);
"""

# sql = """SELECT T1.`District Code`, T2.`NumGE1500`  FROM frpm AS T1 left JOIN satscores AS T2 on T1.CDSCode = T2.cds where T2.`NumGE1500` is NOT NULL and T1.`District Code` > 15 """

# sql = """SELECT T1.`District Code`, T2.`NumGE1500`  FROM frpm AS T1 left JOIN satscores AS T2 on T1.CDSCode = T2.cds where T1.`Academic Year` <> '2023' or T1.`District Code` > 15  """

# sql = """SELECT T1.`District Code`, T2.`NumGE1500`  FROM frpm AS T1 INNER JOIN satscores AS T2 on T1.CDSCode = T2.cds where T1.`Academic Year` <> '2023' or T1.`District Code` > 15 """

logging.basicConfig(
    level=logging.INFO,  # or DEBUG, WARNING, ERROR, etc.
    format="[%(asctime)s] %(levelname)s [%(filename)s:%(lineno)d]: %(message)s",
)
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


class TestInstance(unittest.TestCase):
    @unittest.skip("skipping for now")
    def test_instance(self):
        plan_path = "datasets/bird/plan/california_schools_4_gold.sql"
        instance = Instance(ddls=schema, name="test", dialect="sqlite")
        unique_cols = []

        for table_name, table in instance.catalog.tables.items():
            for col in table.columns:
                if table.is_unique(col.name):
                    unique_cols.append((table_name, col.name))
        self.assertEqual(len(unique_cols), 2)

    @unittest.skip("skipping for now")
    def test_create_row(self):
        instance = Instance(ddls=schema, name="test", dialect="sqlite")
        rows = instance.create_row("frpm", {})
        instance.to_db("tests/db")
        print(rows)

    @unittest.skip("skipping for now")
    def test_domain(self):
        from src.parseval.faker.domain import (
            DomainSpec,
            ValuePool,
            ValueGeneratorFactory,
            ColumnDomainPool,
        )

        column_domains = ColumnDomainPool()
        dtypes = ["INT", "FLOAT", "DATE", "TEXT"]
        for dt in dtypes:
            column_domains.register_domain(
                table="a", column=f"col_{dt}", datatype=dt, unique=False, nullable=True
            )
            column_domains.get_or_create_pool(table="a", column=f"col_{dt}")
        # domains = [DomainSpec(f"a", f"col_{dtype}", datatype=dtype, unique= True, nullable=True) for dtype in dtypes]
        for pool in column_domains.all_pools():
            for _ in range(4):
                value = pool.generate()
                pool.add_generated_value(value)
                print(value, pool.reuse_rate, pool.unique_rate)
            print("-----")

    @unittest.skip("skipping for now")
    def test_create_tables(self):
        # reset_folder("examples/tests")
        from src.parseval.db_manager import DBManager

        instance = Instance(ddls=schema, name="test", dialect="sqlite")

        # with DBManager().get_connection(f"examples/tests", f"{instance.name}.sqlite") as conn:
        #     conn.create_schema(schema, dialect="sqlite")

        logger.info(instance.catalog.constraints)

        logger.info(instance.catalog.is_unique("frpm", "CDSCode"))
        logger.info(instance.catalog.get_column_constraints("frpm", "CDSCode"))
        for domain_name, domain in instance.column_domains._domains.items():
            # logger.info(domain)
            logging.info(f"Domain: {repr(domain)}")
        # for pool in instance.column_domains.all_pools():
        #     logging.info(f"Pool: {pool}")
        instance.to_db2("examples/tests")
        for _ in range(5):
            instance.create_row("frpm")
            instance.create_row("satscores")

        logger.info(instance.get_column_data("frpm", "District Code"))
        instance.to_db2("examples/tests")

    @unittest.skip("skipping for now")
    def test_derived_tables(self):
        from parseval.plan.encoder import PlanEncoder, Context, DerivedTable
        from sqlglot.optimizer.scope import (
            Scope,
            traverse_scope,
            walk_in_scope,
            find_all_in_scope,
            build_scope,
        )

        instance = Instance(ddls=schema, name="test", dialect="sqlite")
        for tbl_name in instance.tables:
            logger.info(f"Table: {tbl_name}, {type(tbl_name)}")
        reset_folder("examples/tests")
        for _ in range(5):
            instance.create_row("frpm")
            instance.create_row("satscores")

        rows = instance.get_rows("frpm")
        tbl = DerivedTable(
            columns=instance.column_names("frpm", dialect="sqlite"), rows=rows
        )

        logger.info(f"Derived table columns: {tbl.columns}, number of rows: {len(tbl)}")

    @unittest.skip("skipping for now")
    def test_uexprs(self):
        from src.parseval.uexpr.uexprs import UExprToConstraint, Constraint
        import random

        random.seed(42)
        tracer = UExprToConstraint()
        ROWS = 22

        for i in range(ROWS):
            tracer.which_path(
                step_type="SCAN",
                step_name="t1",
                sql_conditions=[
                    exp.Column(this="age", table="t1"),
                    exp.Column(this="name", table="t1"),
                ],
                smt_exprs=[f"smt_{i + 1}", f"'Alice_{i}'"],
                takens=[True, True],
                rowids=(f"row{i}",),
                branch=True,
                attach_to=None,
            )

        rowids = []
        for i in range(ROWS):
            takens = [random.choice([True, False]), random.choice([True, False])]
            tracer.which_path(
                step_type="FILTER",
                step_name="t2",
                sql_conditions=[parse_one("t1.age > 15"), parse_one("t1.age < 20")],
                smt_exprs=[f"smt_{i}1", f"smt_{i}2"],
                takens=takens,
                rowids=(f"row{i}",),
                branch=any(takens),
                attach_to=("SCAN", "t1"),
            )
            if any(takens):
                rowids.append(i)

        for i in range(ROWS):
            if i in rowids:
                tracer.which_path(
                    step_type="PROJECT",
                    step_name="t2",
                    sql_conditions=[parse_one("t1.name"), parse_one("t1.age")],
                    smt_exprs=[f"smt_{i}1", f"smt_{i}2"],
                    takens=[True, True],
                    rowids=(f"row{i}",),
                    branch=True,
                    attach_to=("FILTER", "t2"),
                )

        for i in range(ROWS):
            tracer.which_path(
                step_type="SCAN",
                step_name="t3",
                sql_conditions=[
                    exp.Column(this="sname", table="t2"),
                    exp.Column(this="grade", table="t2"),
                ],
                smt_exprs=[f"smt_t2_{i + 1}", f"'Alice_t2_{i}'"],
                takens=[True, True],
                rowids=(f"row_t2_{i}",),
                branch=True,
                attach_to=("PROJECT", "t2"),
            )

        for i in range(ROWS):
            if i in rowids:
                takens = [random.choice([2, 3, 4])]
                tracer.which_path(
                    step_type="JOIN",
                    step_name="t1",
                    sql_conditions=[parse_one("t1.age = t2.grade")],
                    smt_exprs=[f"smt_{i}1"],
                    takens=takens,
                    rowids=(f"row{i}", f"row_t2_{i}"),
                    branch=takens[0] == 2,
                    attach_to=("SCAN", "t3"),
                )

        # for i in range(ROWS):
        #     takens = [random.choice([2, 3, 4])]
        #     tracer.which_path(step_type="JOIN", step_name="t1", sql_conditions=[parse_one("t1.age = t2.grade")], smt_exprs=[f"smt_{i}1"], takens=takens, rowids=(f"row{i}", f"row_{i}2"), branch = takens[0] == 2, attach_to = ("FILTER", "t2"))

        from src.parseval.to_dot import display_uexpr

        display_uexpr(tracer.root).write(
            "examples/tests/dot_coverage_testuexpr.png", format="png"
        )

    @unittest.skip("skipping for now")
    def test_encoder(self):
        import src.parseval.plan.rex
        from sqlglot.optimizer.scope import (
            Scope,
            traverse_scope,
            walk_in_scope,
            find_all_in_scope,
            build_scope,
        )
        from src.parseval.uexpr.uexprs import UExprToConstraint

        instance = Instance(ddls=schema, name="test", dialect="sqlite")
        for tbl_name in instance.tables:
            logger.info(f"Table: {tbl_name}, {type(tbl_name)}")
        reset_folder("examples/tests")
        for _ in range(1):
            instance.create_row("frpm")
            instance.create_row("satscores")
        # instance.create_row("frpm")

        sql = "SELECT T2.sname, COUNT(Distinct sname) FROM frpm AS T1 JOIN satscores AS T2 on T1.CDSCode = T2.cds where NumGE1500 > 100 and NumGE1500 < 880 group by T2.sname"  #
        sql = "SELECT T1.`sname` FROM satscores AS T1 JOIN frpm AS T2 on T1.cds = T2.CDSCode where T1.`NumGE1500` > 100"  # order by `NumGE1500`
        sql = """SELECT NumTstTakr  FROM satscores o  WHERE o.cds = (select d.CDSCode from frpm d order by d.cdscode limit 1)"""
        # sql = """select d.CDSCode from frpm d order by d.cdscode limit 1"""
        from src.parseval.uexpr.checks import Check
        from src.parseval.data_generator import DataGenerator, dbgenerate

        expr = preprocess_sql(sql, instance, dialect="sqlite")

        print(repr(parse_one("upper(sname) = upper('Alice')", dialect="sqlite")))

        # dbgenerate(ddls=schema, query=sql, workspace="examples/tests", dialect="sqlite", random_seed=42)
        # for scope in traverse_scope(expr):
        #     generator = DataGenerator(scope=scope, instance= instance, name="testgen", verbose=True)
        #     generator.generate()
        instance.name = "test_instance"
        instance.to_db("examples/tests")

    @unittest.skip("skipping for now")
    def test_speculative_assigner(self):
        # from src.parseval.query import SpeculativeAssigner
        sql = "SELECT T1.`District Code`, STRFTIME('%Y', CAST(sname AS DATE))  FROM frpm AS T1 left JOIN satscores AS T2 on T1.CDSCode = T2.cds and T1.IRC = T2. NumGE1500 where T2.`NumGE1500` = \"14\" and T1.`District Code` > 15 and STRFTIME('%Y', sname)  > 2000 order by `NumGE1500`"
        sql = """SELECT NumTstTakr  FROM satscores o                WHERE EXISTS (                SELECT 1                FROM frpm c                WHERE c.CDSCode = o.cds  )     union select a.NumTstTakr from satscores a where a.AvgScrMath > 600    """

        sql = """SELECT NumTstTakr  FROM satscores o  WHERE  EXISTS (                SELECT 1                FROM frpm c                WHERE c.CDSCode = o.cds  )  and o.cds = (select d.CDSCode from frpm d order by d.cdscode limit 1)"""

        # sql = """SELECT NumTstTakr  FROM satscores o join frpm c on  c.CDSCode = o.cds WHERE NumTstTakr > 25 group by o.sname having avg(AvgScrMath) > 500 order by avg(AvgScrMath) desc limit 10 offset 5"""

        expr = parse_one(sql, dialect="sqlite")
        # print(repr(expr))

        from sqlglot.optimizer.scope import (
            Scope,
            traverse_scope,
            walk_in_scope,
            find_all_in_scope,
            build_scope,
        )
        from collections import deque

        visited = set()
        root = build_scope(expression=expr)
        queue = deque([root])

        while queue:
            scope = queue.popleft()
            # logger.info(f'visiting scope: {scope}, is_correlated_subquery: {scope.is_correlated_subquery}, scope_type: {scope.scope_type}')
            proceed = True
            correlated_scopes = []
            for sub_scope in scope.subquery_scopes:
                if sub_scope.is_correlated_subquery:
                    correlated_scopes.append(sub_scope)
                    logger.info(scope.parent)
                    # scope.replace(sub_scope, )
                if sub_scope.is_subquery and not sub_scope.is_correlated_subquery:
                    if sub_scope not in visited:
                        logger.info(f"Adding subquery scope to queue: {sub_scope}")
                        queue.append(sub_scope)
                        proceed = False
                        break
                    else:

                        def get_parent(e):
                            if e.parent is None:
                                return None
                            if isinstance(e.parent, (exp.Paren, exp.Subquery)):
                                return get_parent(e.parent)
                            return e.parent

                        logger.info(
                            f"Subquery scope already visited: {sub_scope}, {get_parent(sub_scope.expression)}, {sub_scope.expression.parent.parent.key}"
                        )

            if scope.is_correlated_subquery:
                if scope.parent not in visited:
                    proceed = False
                    queue.append(scope)
                    logger.info(f"Adding correlated scope to queue: {scope}")
            if not proceed:
                queue.append(scope)
                continue
            logger.info(
                f"processing scope: {scope}, outter columns: {scope.derived_tables}"
            )
            logger.info(f"columns: {scope.columns}")
            visited.add(scope)

        # scopes = list(traverse_scope(expr))
        # logger.info(f'Total scopes: {len(scopes)}')
        # for scope in scopes:
        #     logger.info(f'Scope expression:')
        #     logger.info(scope.expression.sql())
        #     logger.info(f'Scope type: {scope.scope_type}, is_correlated_subquery: {scope.is_correlated_subquery}')
        #     logger.info(f' Sources: {scope.sources}')
        #     logger.info(f' references: {scope.references}')
        #     logger.info(f' Derived : {scope.derived_tables}')
        #     logger.info(f' dependencies: {list(scope.find_all(Scope))}')
        #     logger.info(f' scope.derived_table_scopes: {scope.derived_table_scopes}')
        #     logger.info(f' scope.subquery_scopes: {scope.subquery_scopes}')

        #     if scope.is_correlated_subquery:
        #         logger.info(f' parent: {scope.parent}')

        #         logger.info(f'  Outer columns: {scope.external_columns}')

        # logger.info(scopes[-1].scope_type)
        # for scope in scopes:
        #     logger.info(scope.scope_type)

        # for scope in traverse_scope(expr):
        #     logger.info(f'Scope expression:')
        #     logger.info(scope.expression.sql())
        #     logger.info(f'Scope type: {scope.scope_type}, is_correlated_subquery:')

        # coverage_constraints = CoverageConstraints(context= {}, scope= Scope(expr), table_alias= [], dialect="sqlite")

        # coverage_constraints._build2()

        # logger.info("->".join([str(j) for j in coverage_constraints.scans]))
        # logger.info("->>".join(str(j) for j in coverage_constraints.joins))
        # logger.info("->>>".join(str(j) for j in coverage_constraints.projections))
        # logger.info("->>>>".join(str(j) for j in coverage_constraints.table_predicates))
        # logger.info("Group BY" + "->>>>>".join(str(j) for j in coverage_constraints.group_by['by']))
        # logger.info("->>>>>".join(str(j) for j in coverage_constraints.having))

        from sqlglot.planner import Plan

        # for scope in scopes:
        #     p = Plan(scope.expression)
        #     for k, v in p.dag.items():
        #         logger.info(f'Node: {k}, depends on: {v}')

        # logger.info(repr(p))

        # def get_priority(scope: Scope):
        #     if scope.is_correlated_subquery:
        #         return 5
        #     if scope.is_subquery or scope.is_derived_table:
        #         return 1
        #     elif scope.is_cte:
        #         return 2
        #     elif scope.is_union:
        #         return 3
        #     elif scope.is_root:
        #         return 4
        #     else:
        #         raise ValueError("Unknown scope type")
        # scopes = list(traverse_scope(expr))

        # scopes.sort(key= get_priority)
        # for s in scopes:
        #     logger.info(f'processing scope:')
        #     logger.info(s.expression.sql())
        #     # logger.info(f"Scope type: {s.scope_type}, is_correlated_subquery: {s.is_correlated_subquery}, is_root: {s.is_root}")

        # coverage_constraints._build()
        # # for p in parse_one("a and b and (c or d)", dialect="sqlite").flatten():
        # #     logger.info(p)

        # for scope in traverse_scope(expr):
        #     # logger.info(f"Scope: {scope}, {scope.is_correlated_subquery}, {scope.scope_type}")
        #     # logger.info(scope.tables)
        #     # scope.is_root

        #     logger.info(scope.expression.sql())
        #     logger.info(scope.parent)

        #     # scope.external_columns
        #     logger.info(scope.tables)

        #     logger.info(", ".join(scope.sources))
        #     logger.info(",".join([f"{col}" for col in scope.columns]))
        #     logger.info(scope.outer_columns)

        #     # logger.info(scope.sources)
        #     # logger.info(scope.external_columns)
        #     # logger.info(f'Predicate :')
        #     # for p in find_all_in_scope(scope.expression, exp.Predicate):
        #     #     logger.info(f' Predicate: {p}, type: {type(p)}')
        #     # for node in walk_in_scope(scope.expression):
        #     #     logger.info(f" Node: {node}, type: {type(node)}")
        #     logger.info('====================')
        # for alias, table in scope.tables.items():
        #     logger.info(f"  Table alias: {alias}, table: {table}")
        # for col in scope.columns:
        #     logger.info(f"  Column: {col}, type: {col.type}")
        # logger.info(repr(expr))
        # instance = Instance(ddls=schema, name="test", dialect="sqlite")
        # assigner = SpeculativeAssigner(sql, instance.catalog, dialect="sqlite")
        # expr = assigner.expr
        # dtypes = infer_datatypes(expr, MappingSchema.from_catalog(instance.catalog, dialect="sqlite"), dialect="sqlite")
        # print(dtypes)

    @unittest.skip("skipping for now")
    def test_sqlglot_schema(self):
        instance = Instance(ddls=schema, name="test", dialect="sqlite")
        mapping = {instance.name: {}}

        for table_name, table in instance.catalog.tables.items():
            for columnref in table.columns:
                table_mapping = mapping[instance.name].setdefault(table_name, {})
                table_mapping[columnref.name] = columnref.datatype

                # [columnref.name] = columnref.args.get("kind")

        from sqlglot import MappingSchema, exp
        from sqlglot.optimizer import (
            annotate_types,
            normalize_identifiers,
            normalize,
            qualify,
        )
        from src.parseval.query import preprocess_sql, infer_datatypes

        scm = MappingSchema(schema=mapping, dialect="sqlite")

        #

        # def custom_annotate_column(annotator: annotate_types.TypeAnnotator, column):
        #     if not annotator.schema.has_column(column.table, column=column):
        #         return annotator._annotate_literal(column)
        #     annotator._set_type(
        #         column,
        #         annotator.schema.get_column_type(
        #             column.table,
        #             column=column,
        #         ),
        #     )
        #     # self._set_type(col, self.schema.get_column_type(source, col))
        # annotators[exp.Column] = lambda self, e: custom_annotate_column(self, e)

        # logger.info(scm.find(scm._normalize_table('frpm')))
        # print(scm.column_names('frpm'))
        # print(scm.get_column_type('frpm', 'District Code', normalize=False))
        sql = "SELECT T1.`District Code`, STRFTIME('%Y', CAST(sname AS DATE))  FROM frpm AS T1 left JOIN satscores AS T2 on T1.CDSCode = T2.cds where T2.`NumGE1500` = \"14\" and T1.`District Code` > 15 and STRFTIME('%Y', sname)  > 2000 order by `NumGE1500`"
        from sqlglot import parse_one

        # exp.TimeToStr

        e = preprocess_sql(
            sql,
            mappingschema=scm,
            dialect="sqlite",
        )
        dtypes = infer_datatypes(e, scm, "sqlite")

        print(dtypes)
        # e = qualify.qualify(e, schema=scm, dialect="sqlite")
        # e = annotate_types.annotate_types(
        #     preprocess_sql(
        #         "SELECT T1.`District Code`, T2.`NumGE1500`  FROM frpm AS T1 left JOIN satscores AS T2 on T1.CDSCode = T2.cds where T2.`NumGE1500` = \"14\" and T1.`District Code` > 15 ",
        #         mappingschema= scm,
        #         dialect="sqlite",
        #     ),
        #     schema=scm,
        # )

        print(repr(e))

        # print(e.sql())

    def test_instance_to_context(self):
        from parseval.plan import build_context_from_instance

        instance = Instance(ddls=schema, name="test", dialect="sqlite")
        for tbl_name in instance.tables:
            instance.create_row(tbl_name)

        ctx = build_context_from_instance(instance)

        logger.info(ctx.table)

        for table_name, dc in ctx.tables.items():
            for column in dc.columns:
                logger.info(
                    f"Table: {table_name}, Column: {column}, Type: {dc.get_column_type(column)}, Unique: {dc.is_unique(column)}, Nullable: {dc.nullable(column)}"
                )


if __name__ == "__main__":

    runner = unittest.TextTestRunner(stream=sys.stdout, verbosity=2)
    unittest.main(testRunner=runner, exit=False)
