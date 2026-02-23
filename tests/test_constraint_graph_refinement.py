
import unittest
from src.parseval.uexprs import CoverageConstraints
from src.parseval.instance import Instance
from src.parseval.query import preprocess_sql
from sqlglot import parse_one, exp
from sqlglot.optimizer.scope import build_scope

class TestConstraintGraphRefinement(unittest.TestCase):
    def setUp(self):
        self.ddl = """
        CREATE TABLE t1 (c1 INT, c2 INT);
        CREATE TABLE t2 (c1 INT, c2 INT);
        """
        self.instance = Instance(ddls=self.ddl, name="test_instance", dialect="sqlite")

    def test_exists_subquery(self):
        sql = "SELECT * FROM t1 WHERE EXISTS (SELECT 1 FROM t2 WHERE t1.c1 = t2.c1)"
        expression = preprocess_sql(sql, self.instance.catalog, dialect="sqlite")
        scope = build_scope(expression)
        
        # We need to mock context and table_alias as they are required by CoverageConstraints
        context = {}
        table_aliases = {t.alias_or_name: t.name for t in expression.find_all(exp.Table)}
        
        constraints = CoverageConstraints(context=context, scope=scope, table_alias=table_aliases, dialect="sqlite")
        constraints._build2()
        
        # Identify if EXISTS predicate is captured
        exists_predicates = [p for p in constraints.quantified_predicates if isinstance(p, exp.SubqueryPredicate)]
        self.assertTrue(len(exists_predicates) > 0, "Should satisfy EXISTS predicate")
        print(f"Captured EXISTS predicates: {exists_predicates}")

    def test_scalar_subquery(self):
        sql = "SELECT (SELECT MAX(c1) FROM t2) FROM t1"
        expression = preprocess_sql(sql, self.instance.catalog, dialect="sqlite")
        scope = build_scope(expression)
        
        context = {}
        table_aliases = {t.alias_or_name: t.name for t in expression.find_all(exp.Table)}
        
        constraints = CoverageConstraints(context=context, scope=scope, table_alias=table_aliases, dialect="sqlite")
        constraints._build2()
        # Check if scalar subquery is handled - current implementation might not capture it in projections directly essentially
        # We'll check if we can find it in constraints.projections or similar
        print(f"Projections: {constraints.projections}")

    def test_case_when(self):
        sql = "SELECT CASE WHEN c1 > 10 THEN 'high' ELSE 'low' END FROM t1"
        expression = preprocess_sql(sql, self.instance.catalog, dialect="sqlite")
        scope = build_scope(expression)

        context = {}
        table_aliases = {t.alias_or_name: t.name for t in expression.find_all(exp.Table)}
        
        constraints = CoverageConstraints(context=context, scope=scope, table_alias=table_aliases, dialect="sqlite")
        constraints._build2()
        print(f"Projections with CASE: {constraints.projections}")

if __name__ == '__main__':
    unittest.main()
