import unittest

from parseval.domain.compiler import ColumnDomainPlan, ConstraintConflict
class TestPlans(unittest.TestCase):
    def test_column_domain_plan_defaults(self):
        plan = ColumnDomainPlan()
        self.assertTrue(plan.nullable)
        self.assertFalse(plan.unique)
        self.assertIsNone(plan.allowed_values)
        self.assertEqual(plan.excluded_values, ())
        self.assertIsNone(plan.minimum)
        self.assertIsNone(plan.maximum)
        self.assertTrue(plan.minimum_inclusive)
        self.assertTrue(plan.maximum_inclusive)
        self.assertIsNone(plan.minimum_length)
        self.assertIsNone(plan.maximum_length)
        self.assertIsNone(plan.pattern)
        self.assertEqual(plan.residual_predicates, ())

    def test_constraint_conflict_inheritance(self):
        from parseval.domain.exceptions import DomainError
        self.assertTrue(issubclass(ConstraintConflict, DomainError))

if __name__ == "__main__":
    unittest.main()
