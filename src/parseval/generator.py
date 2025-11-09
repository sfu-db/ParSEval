from __future__ import annotations
from typing import Any, Dict, List, Optional, Union
from collections import defaultdict
from src.parseval.plan.planner import PlanEncoder, Planner, ExpressionEncoder
from src.parseval.plan import rex

from src.parseval.instance import Instance
from src.parseval.uexpr import UExprToConstraint
from src.parseval import symbol as sym
from src.parseval.smt.solver import Solver, ColumnDomainPool, DomainSpec
import logging, operator

from itertools import product


def product_of_columns(table_data: dict[str, list]):
    """
    Given a table's column data as {col_name: [values]},
    return all combinations of their values (Cartesian product).
    """
    columns = list(table_data.keys())
    value_lists = [table_data[col] for col in columns]
    return [dict(zip(columns, row)) for row in product(*value_lists)]


def product_of_table(tables: dict[str, dict[str, list]]):
    """
    Given multiple tables, each as {col_name: [values]},
    compute the Cartesian product across all tables.
    """
    table_names = list(tables.keys())
    table_products = [product_of_columns(tables[t]) for t in table_names]

    combined_rows = []
    for combo in product(*table_products):
        merged_row = {}
        for tname, row in zip(table_names, combo):
            for col, val in row.items():
                merged_row[f"{tname}.{col}"] = val
        combined_rows.append(merged_row)

    return combined_rows


def get_domainpool(instance: Instance) -> ColumnDomainPool:
    pool = ColumnDomainPool()
    for table_name, table in instance.catalog.tables.items():
        for column in table.columns:
            domain = DomainSpec(
                table_name=table_name,
                column_name=column.name,
                datatype=column.datatype,
                unique=table.is_unique(column.name),
                nullable=column.datatype.nullable,
                generated=[
                    v.concrete
                    for v in instance.get_column_data(table_name, column.name)
                ],
            )
            pool.register_domain(domain)
    return pool


def coerce_datatype(): ...


class ExprEncoder(ExpressionEncoder):
    def __init__(self):
        super().__init__(None, None, True)

    def visit_columnref(self, expr, parent_stack=None, context=None):
        smt_expr = context[expr]
        return smt_expr


class Generator:
    """
    Class for generating database for sql queries. Specifically, it takes as input a database schema and a SQL query, and generates database instances that satisfy certain constraints derived from the query plan.
    It can convert the Coverage constraints into SMT constraints and solve them to generate concrete data values.
    It uses a constraint solver to ensure that the generated data meets the specified conditions.
    """

    def __init__(self, schema, query, dialect="sqlite", name="default"):
        self.schema = schema
        self.dialect = dialect
        self.name = name
        self.constraints: Dict[str, List[rex.Expression]] = defaultdict(list)

        planner = Planner()
        self.query = planner.explain(schema, query)

    def add_constraint(
        self, label, constraints: Union[rex.Expression, List[rex.Expression]]
    ):
        if not isinstance(constraints, list):
            constraints = [constraints]
        self.constraints.setdefault(label, []).extend(constraints)

    def reset(self):
        self.constraints.clear()

    def generate(self, max_iter, threshold=1):
        instance = Instance(ddls=self.schema, name=self.name, dialect=self.dialect)
        for table_name in instance.catalog.tables:
            instance.create_row(table_name, {})
        tracer = UExprToConstraint(declare=self.add_constraint, threshold=threshold)
        self.query.schema(instance.catalog)
        for index in range(max_iter):
            encoder = PlanEncoder(instance=instance, trace=tracer)
            encoder.visit(self.query)
            plausible = tracer.next_path()
            if plausible is None:
                break
            pattern = plausible.pattern()
            logging.info(f"Selecting leaf: ========================= {pattern}")
            tracer._declare_smt_constraints(plausible)

            solver, var_to_columnref, columnref_to_var = self.generate_smt_conditions(
                instance
            )

            with open("tests/db/constraints.txt", "a") as f:
                f.write(f"=== Iteration {index} ===\n")
                for label, constraints in self.constraints.items():
                    f.write(f"-- Operator: {label} --\n")
                    for constraint in constraints:
                        f.write(str(constraint) + "\n")

            solver_result = solver.solve()
            if solver_result.status != "sat":
                logging.info("No satisfying assignment found.")
                plausible.mark_infeasible()
            else:
                concretes = {}
                for assignment in solver_result.assignments:
                    var_name = assignment.column
                    columnref = var_to_columnref[var_name]
                    table_name = columnref.table
                    concretes.setdefault(table_name, {})[
                        columnref.name
                    ] = assignment.value
                if concretes:
                    # plausible.mark_covered()
                    plausible.update_mark()
                    logging.info(concretes)
                    for table_name in instance.catalog.tables:
                        if table_name in concretes:
                            instance.create_row(table_name, concretes[table_name])

            if index < max_iter - 1:
                tracer.reset()
                self.reset()
            # break
        from src.parseval.to_dot import display_uexpr

        tracer.reset()
        self.reset()
        encoder = PlanEncoder(instance=instance, trace=tracer)
        encoder.visit(self.query)
        display_uexpr(tracer.root_constraint, use_ref_condition_flag=False).write(
            "tests/db/dot_coverage" + instance.name + ".png", format="png"
        )
        return instance

    def generate_smt_conditions(self, instance: Instance):

        column_pool = get_domainpool(instance)
        solver = Solver(column_pool)

        ### step 1: unify datetype casts
        for _, constraints in self.constraints.items():
            for constraint in constraints:
                casts = constraint.find_all(rex.sqlglot_exp.Cast)
                for cast in casts:
                    if isinstance(cast.this, rex.ColumnRef):
                        alias = cast.this.qualified_name
                        pool = column_pool.get_or_create_pool(
                            alias,
                            table_name=cast.this.table,
                            column_name=cast.this.name,
                        )
                        if pool:
                            pool.datatype = cast.to

        ### step 2: declare variables

        ### step 3: add constraints to Solver

        var_to_columnref = {}
        columnref_to_var = {}

        for label, constraints in self.constraints.items():
            for constraint in constraints:
                columnrefs = set(constraint.find_all(rex.ColumnRef))
                if not columnrefs:
                    continue
                for columnref in columnrefs:
                    var_name = f"{columnref.qualified_name}"
                    if var_name not in var_to_columnref:
                        domain = column_pool.get_or_create_pool(
                            var_name,
                            table_name=columnref.table,
                            column_name=columnref.name,
                        )
                        var = sym.Variable(var_name, dtype=domain.datatype)
                        var_to_columnref[var_name] = columnref
                        columnref_to_var[columnref] = var
                        if domain.unique:
                            data = domain.domain.generated
                            values = [sym.Const(d, dtype=domain.datatype) for d in data]
                            unique_constraint = sym.Distinct(var, *values, dtype="bool")
                            solver.add_constraint(unique_constraint)

                # if label == "Join":
                #     table_columns = (
                #         {}
                #     )  # Given multiple tables, each as {col_name: [values]},
                #     for columnref in columnrefs:
                #         table_columns.setdefault(columnref.table, {})[
                #             columnref.name
                #         ] = instance.get_column_data(
                #             table_name=columnref.table, column_name=columnref.name
                #         )

                #     for columnref in columnrefs:
                #         table_columns.setdefault(columnref.table, {})[
                #             columnref.name
                #         ].append(columnref_to_var[columnref])

                #     for row in product_of_table(table_columns):
                #         ctx = {}
                #         for columnref in columnrefs:
                #             ctx[columnref] = row[f"{columnref.table}.{columnref.name}"]

                #         condition = ExprEncoder().visit(constraint, context={**ctx})

                #         # logging.info(condition)

                if isinstance(constraint, rex.sqlglot_exp.Predicate):
                    ### Encode Coverage to SMT constraints
                    condition = ExprEncoder().visit(
                        constraint, context={**columnref_to_var}
                    )
                    # if condition is not None:
                    # logging.info(f"Encoded constraint: {repr(condition)}")
                    # logging.info(f"From original: {constraint}")
                    solver.add_constraint(condition)

        return solver, var_to_columnref, columnref_to_var
