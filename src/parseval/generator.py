from __future__ import annotations
from typing import Any, Dict, List, Optional, Union
from collections import defaultdict
from src.parseval.plan.planner import Planner, PlanEncoder
from src.parseval.instance import Instance
from src.parseval.uexpr import UExprToConstraint
import src.parseval.plan.expression as sql_exp
import src.parseval.symbol as sym
import logging
from src.parseval.solver.solver import CSPConstraint, SpeculativeSolver


class Generator:
    """
    Class for generating database for sql queries
    """

    def __init__(self, schema, query, dialect="sqlite", name="default"):
        self.schema = schema
        # self.query = query
        self.dialect = dialect
        self.name = name
        ## label -> List[Constraint], track constraints for each operator
        self.constraints: Dict[str, List[Any]] = defaultdict(list)

        planner = Planner()
        self.query = planner.explain2(schema, query)

    def add_constraint(self, label, constraints):
        if not isinstance(constraints, list):
            constraints = [constraints]
        if label not in self.constraints:
            self.constraints[label] = []
        self.constraints[label].extend(constraints)

    def reset(self):
        self.constraints.clear()

    def get_db_constraints(self, instance: Instance):
        from src.parseval.solver.solver import ColumnDomainPool, DomainSpec

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
        for i in range(2):
            a = instance.create_row(
                "frpm", {"County Name": "Los Angeles", "Free Meal Count (K-12)": 550}
            )
        for i in range(2):
            instance.create_row("schools", {"County": "Amador"})
        for i in range(2):
            instance.create_row("frpm", {"Low Grade": 9, "High Grade": 12})

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
            logging.info(plausible.pattern())
            tracer._append_tuple(instance, plausible=plausible)
            concretes = self.generate_smt_constriants(instance)
            if not concretes:
                plausible.mark_infeasible()
            else:
                plausible.mark_covered()
            for table_name in instance.catalog.tables.keys():
                if table_name in concretes:
                    instance.create_row(table_name, concretes[table_name])
                    logging.info(
                        f"create row for table {table_name}: {concretes[table_name]}"
                    )

            if index < max_iter - 1:
                tracer.reset()
                self.reset()
            # break
        instance.to_db("tests/db")

        return tracer

    def generate_smt_constriants(self, instance: Instance):

        pool = self.get_db_constraints(instance)
        solver = SpeculativeSolver(pool)

        for operator_type, constraints in self.constraints.items():
            for constraint in constraints:
                columnrefs = constraint.find_all(sql_exp.ColumnRef)
                variables = set()
                for columnref in columnrefs:
                    table_name = columnref.metadata.get("table")

                    # index = len(instance.get_rows(table_name))
                    var_name = f"{columnref.qualified_name}"
                    # logging.info(
                    #     f"Processing columnref: {columnref} from table {table_name}, var name: {var_name}"
                    # )

                    var = solver.add_variable(var_name, columnref)
                    variables.add(var)
                    if instance.catalog.get_table(table_name).is_unique(columnref.name):
                        data = instance.get_column_data(table_name, columnref.name)
                        for d in data:
                            predicate = sql_exp.Predicate(
                                left=columnref,
                                op="!=",
                                right=sql_exp.Literal(
                                    value=d.concrete, datatype=columnref.datatype
                                ),
                            )
                            solver.add_constraint(predicate, variables={var})

                for cast in constraint.find_all(sql_exp.Cast):
                    solver.cast_valuepool_datatype(cast)

                if isinstance(constraint, sql_exp.Predicate):
                    solver.add_constraint(constraint, variables=variables)

        # logging.info(solver.variables)

        # return {}

        assignment = solver.solve()
        logging.info(f"Generated Assignment: {assignment}")
        if assignment.status == "SAT":
            concretes = {}
            var_to_columnref = solver.var_to_columnref
            logging.info(f"var_to_columnref: {var_to_columnref}")
            for var_name, value in assignment.assignments.items():
                columnref = var_to_columnref[var_name]
                concretes.setdefault(columnref.metadata["table"], {})[
                    columnref.name
                ] = value

            logging.info(f"Concretes: {concretes}")
            return concretes

        return {}
