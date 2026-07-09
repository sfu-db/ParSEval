import unittest
from datetime import datetime

from parseval.dtype import DataType, StorageLiteral
from parseval.domain.compiler import ColumnDomainPlan, ConstraintValidator
from parseval.domain.exceptions import ConstraintViolationError

class TestValidator(unittest.TestCase):
    def setUp(self):
        self.validator = ConstraintValidator()

    def test_validate_nullable(self):
        plan = ColumnDomainPlan(nullable=False)
        with self.assertRaises(ConstraintViolationError):
            self.validator.validate(plan, None)
        
        plan_ok = ColumnDomainPlan(nullable=True)
        self.validator.validate(plan_ok, None)

    def test_validate_allowed_values(self):
        plan = ColumnDomainPlan(allowed_values=("A", "B"))
        self.validator.validate(plan, "A")
        with self.assertRaises(ConstraintViolationError):
            self.validator.validate(plan, "C")

    def test_validate_range(self):
        plan = ColumnDomainPlan(minimum=10, maximum=20)
        self.validator.validate(plan, 10)
        self.validator.validate(plan, 15)
        self.validator.validate(plan, 20)
        with self.assertRaises(ConstraintViolationError):
            self.validator.validate(plan, 5)
        with self.assertRaises(ConstraintViolationError):
            self.validator.validate(plan, 25)

    def test_validate_range_exclusive(self):
        plan = ColumnDomainPlan(minimum=10, maximum=20, minimum_inclusive=False, maximum_inclusive=False)
        self.validator.validate(plan, 15)
        with self.assertRaises(ConstraintViolationError):
            self.validator.validate(plan, 10)
        with self.assertRaises(ConstraintViolationError):
            self.validator.validate(plan, 20)

    def test_validate_temporal_storage_literal_against_python_bounds(self):
        plan = ColumnDomainPlan(
            datatype=DataType.build("DATETIME"),
            minimum=datetime(2010, 1, 1),
            maximum=datetime(2010, 12, 31, 23, 59, 59),
        )

        self.validator.validate(plan, StorageLiteral("2010-07-19 19:39:08.0"))

    def test_validate_length(self):
        plan = ColumnDomainPlan(minimum_length=3, maximum_length=5)
        self.validator.validate(plan, "abc")
        self.validator.validate(plan, "abcde")
        with self.assertRaises(ConstraintViolationError):
            self.validator.validate(plan, "ab")
        with self.assertRaises(ConstraintViolationError):
            self.validator.validate(plan, "abcdef")

    def test_validate_pattern(self):
        plan = ColumnDomainPlan(pattern=r"^A.*$")
        self.validator.validate(plan, "Apple")
        with self.assertRaises(ConstraintViolationError):
            self.validator.validate(plan, "Banana")

    def test_validate_residual_predicates(self):
        plan = ColumnDomainPlan(residual_predicates=(lambda x: x % 2 == 0,))
        self.validator.validate(plan, 2)
        with self.assertRaises(ConstraintViolationError):
            self.validator.validate(plan, 3)

if __name__ == "__main__":
    unittest.main()
