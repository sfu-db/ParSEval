from __future__ import annotations

import unittest

from sqlglot import exp

from parseval.generator.schema_constraints import (
    SchemaConstraintLoweringError,
    batch_unique_constraints_for_solver_rows,
    schema_constraints_for_solver_row,
)
from parseval.instance import Instance
from parseval.solver.types import SolverVar


class TestSchemaConstraintLowering(unittest.TestCase):
    def test_check_constraint_with_non_query_column_is_lowered(self):
        instance = Instance(
            """
            CREATE TABLE customer (
                id INT,
                referee_id INT,
                CHECK (referee_id <> id)
            );
            """,
            name="checks",
            dialect="sqlite",
        )
        table = instance.resolve_table("customer")
        sv_map = {
            "id": SolverVar(key="customer.id"),
            "referee_id": SolverVar(key="customer.referee_id"),
        }

        constraints = schema_constraints_for_solver_row(
            instance,
            table,
            sv_map,
        )

        self.assertTrue(
            any(
                isinstance(constraint, exp.NEQ)
                and sv_map["id"] in set(constraint.find_all(SolverVar))
                and sv_map["referee_id"] in set(constraint.find_all(SolverVar))
                for constraint in constraints
            )
        )

    def test_check_constraint_missing_solver_var_fails_closed(self):
        instance = Instance(
            """
            CREATE TABLE customer (
                id INT,
                referee_id INT,
                CHECK (referee_id <> id)
            );
            """,
            name="missing_check_var",
            dialect="sqlite",
        )
        table = instance.resolve_table("customer")
        sv_map = {"referee_id": SolverVar(key="customer.referee_id")}

        with self.assertRaises(SchemaConstraintLoweringError) as raised:
            schema_constraints_for_solver_row(instance, table, sv_map)

        self.assertEqual(
            "unlowerable_check_constraint:customer:id",
            raised.exception.reason,
        )

    def test_unsupported_check_constraint_fails_closed(self):
        instance = Instance(
            """
            CREATE TABLE other (id INT);
            CREATE TABLE customer (
                id INT,
                CHECK (id > (SELECT MAX(id) FROM other))
            );
            """,
            name="unsupported_check",
            dialect="sqlite",
        )
        table = instance.resolve_table("customer")
        sv_map = {"id": SolverVar(key="customer.id")}

        with self.assertRaises(SchemaConstraintLoweringError) as raised:
            schema_constraints_for_solver_row(instance, table, sv_map)

        self.assertEqual(
            "unsupported_check_constraint:customer:subquery",
            raised.exception.reason,
        )

    def test_existing_uniqueness_constraint_is_lowered(self):
        instance = Instance(
            "CREATE TABLE users (id INT PRIMARY KEY, age INT);",
            name="unique_existing",
            dialect="sqlite",
        )
        table = instance.resolve_table("users")
        instance.create_rows({table: [{"id": 1, "age": 30}]})
        sv_map = {
            "id": SolverVar(key="users.id"),
            "age": SolverVar(key="users.age"),
        }

        constraints = schema_constraints_for_solver_row(instance, table, sv_map)

        self.assertTrue(
            any(
                isinstance(constraint, exp.NEQ)
                and sv_map["id"] in set(constraint.find_all(SolverVar))
                for constraint in constraints
            )
        )

    def test_batch_composite_uniqueness_uses_or_of_neq_atoms(self):
        instance = Instance(
            "CREATE TABLE pairs (a INT, b INT, PRIMARY KEY (a, b));",
            name="composite_unique",
            dialect="sqlite",
        )
        table = instance.resolve_table("pairs")
        left = {"a": SolverVar(key="left.a"), "b": SolverVar(key="left.b")}
        right = {"a": SolverVar(key="right.a"), "b": SolverVar(key="right.b")}

        constraints = batch_unique_constraints_for_solver_rows(
            instance,
            table,
            (left, right),
        )

        self.assertEqual(1, len(constraints))
        constraint = constraints[0]
        self.assertIsInstance(constraint, exp.Or)
        self.assertEqual(2, len(list(constraint.find_all(exp.NEQ))))

    def test_foreign_key_parent_values_are_lowered(self):
        instance = Instance(
            """
            CREATE TABLE parent (id INT PRIMARY KEY);
            CREATE TABLE child (
                id INT PRIMARY KEY,
                parent_id INT,
                FOREIGN KEY(parent_id) REFERENCES parent(id)
            );
            """,
            name="fk_parent_values",
            dialect="sqlite",
        )
        instance.create_rows({"parent": [{"id": 7}]})
        table = instance.resolve_table("child")
        sv_map = {
            "id": SolverVar(key="child.id"),
            "parent_id": SolverVar(key="child.parent_id"),
        }

        constraints = schema_constraints_for_solver_row(instance, table, sv_map)

        self.assertTrue(
            any(
                isinstance(constraint, exp.In)
                and constraint.this == sv_map["parent_id"]
                for constraint in constraints
            )
        )


if __name__ == "__main__":
    unittest.main()
