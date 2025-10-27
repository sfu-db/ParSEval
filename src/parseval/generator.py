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
        # self.query = query
        self.dialect = dialect
        self.name = name
        ## label -> List[Constraint], track constraints for each operator
        self.constraints: Dict[str, List[rex.Expression]] = defaultdict(list)

        planner = Planner()
        self.query = planner.explain2(schema, query)

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
        for i in range(2):
            a = instance.create_row(
                "satscores",
                {
                    "cname": "Contra Costa",
                    "dname": "Contra Costa",
                    "sname": "Contra Costa",
                },
            )

        tracer = UExprToConstraint(declare=self.add_constraint, threshold=threshold)

        self.query.schema(instance.catalog)

        for index in range(max_iter):
            encoder = PlanEncoder(instance=instance, trace=tracer)
            encoder.visit(self.query)
            plausible = tracer.next_path()

            if plausible is None:
                # instance.to_db("tests/db")
                break
                return tracer
            pattern = plausible.pattern()
            logging.info(f"plausible.pattern(): {pattern} == {plausible.pattern()}")
            # _decalre_smt_constraints
            tracer._declare_smt_constraints(plausible)

            solver, var_to_columnref, columnref_to_var = self.generate_smt_conditions(
                instance
            )

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
                    plausible.mark_covered()
                    logging.info(concretes)
                    for table_name in instance.catalog.tables:
                        if table_name in concretes:
                            instance.create_row(table_name, concretes[table_name])

            if index < max_iter - 1:
                tracer.reset()
                self.reset()
            # break
        instance.to_db("tests/db")

        return tracer

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
                            pool.datatype = cast.to_type

        ### step 2: declare variables

        ### step 3: add constraints to Solver

        var_to_columnref = {}
        columnref_to_var = {}

        for label, constraints in self.constraints.items():
            for constraint in constraints:
                columnrefs = constraint.find_all(rex.ColumnRef)
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
                            logging.info(unique_constraint)

                if isinstance(constraint, rex.sqlglot_exp.Predicate):
                    ### Encode Coverage to SMT constraints
                    if constraint.find_all(rex.Is_Null):
                        continue
                    condition = ExprEncoder().visit(
                        constraint, context={**columnref_to_var}
                    )
                    solver.add_constraint(condition)

        return solver, var_to_columnref, columnref_to_var

    def generate_smt_constriants(self, instance: Instance):

        # pool = self.get_db_constraints(instance)
        # solver = SpeculativeSolver(pool)

        # for operator_type, constraints in self.constraints.items():
        #     for constraint in constraints:
        #         columnrefs = constraint.find_all(sql_exp.ColumnRef)
        #         variables = set()
        #         for columnref in columnrefs:
        #             table_name = columnref.metadata.get("table")

        #             # index = len(instance.get_rows(table_name))
        #             var_name = f"{columnref.qualified_name}"

        #             var = solver.add_variable(var_name, columnref)
        #             variables.add(var)
        #             if instance.catalog.get_table(table_name).is_unique(columnref.name):
        #                 data = instance.get_column_data(table_name, columnref.name)
        #                 for d in data:
        #                     predicate = sql_exp.Predicate(
        #                         left=columnref,
        #                         op="!=",
        #                         right=sql_exp.Literal(
        #                             value=d.concrete, datatype=columnref.datatype
        #                         ),
        #                     )
        #                     solver.add_constraint(predicate, variables={var})

        #         for cast in constraint.find_all(sql_exp.Cast):
        #             if isinstance(cast.args[0], sql_exp.ColumnRef):
        #                 solver.cast_valuepool_datatype(cast)

        #         if constraint.find_all(sql_exp.AggFunc):
        #             # skip constraints with aggregation functions
        #             continue

        #         if isinstance(constraint, sql_exp.Predicate):
        #             solver.add_constraint(constraint, variables=variables)

        # # logging.info(solver.variables)
        # # return {}

        # assignment = solver.solve()
        # logging.info(f"Generated Assignment: {assignment}")
        # if assignment.status == "SAT":
        #     concretes = {}
        #     var_to_columnref = solver.var_to_columnref
        #     logging.info(f"var_to_columnref: {var_to_columnref}")
        #     for var_name, value in assignment.assignments.items():
        #         columnref = var_to_columnref[var_name]
        #         concretes.setdefault(columnref.metadata["table"], {})[
        #             columnref.name
        #         ] = value

        #     logging.info(f"Concretes: {concretes}")
        #     return concretes

        return {}
