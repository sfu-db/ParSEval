from __future__ import annotations

from collections import defaultdict
from src.instance.instance import Instance

from .uexpr_to_constraint import UExprToConstraint
from .executor import Executor
from .constant import Action
from src.solver.solver import Solver
import logging


class Generator:
    def __init__(self, workspace, schema, query, dialect = 'sqlite', **kwargs):
        self.workspace = workspace
        self.schema = schema
        self.query = query
        self.dialect = dialect

        self.name = kwargs.get('name', 'public')

        self.plan = self.parser(query, schema)
        

        self.constraints = defaultdict(list)

        

    def parser(self, query, schema):
        from src.expression.query import parser
        parse = parser.QParser()
        self.plan = parse.explain(query, schema)
        return self.plan

    def add_constraint(self, constraint, identity, label):
        self.constraints[label].append(constraint)
    

    def update_instance(self, instance):
        ...
                

    def generate(self, size = 5, **kwargs):
        ...

    def reset(self):
        self.path.reset()
        self.constraints.clear()

    def _one_execution(self, max_iter = 5, **kwargs):

        size = 1
        instance =Instance.create(schema= self.schema, name = self.name, dialect = self.dialect)
        for _ in range(size):
            for tbl in instance._tables:
                instance.create_row(tbl, {})

        for _ in range(max_iter):
            self.path = UExprToConstraint(lambda constraint, identity, label: self.add_constraint(constraint, identity, label))
            self.executor = Executor(self.path)
            self.reset()
            st = self.executor(self.plan, instance = instance)
            
            next_action = self.path.next_branch(instance)
            # logging.info(f'next action: {next_action}')
            if next_action in [Action.UPDATE, Action.APPEND] and self.constraints:
                solver = Solver()
                for label, constraints in self.constraints.items():
                    solver.add(constraints)
                if solver.check():
                    concretes = solver.model()
                    # logging.info(f'constraints: {self.constraints}')
                    # logging.info(f'concretes: {concretes}')
                    instance.update_values(concretes)
            # elif next_action == Action.APPEND:
            #     for tbl in instance._tables:
            #         instance.create_row(tbl, {})
            elif next_action == Action.DONE:
                break

        instance.to_db(self.workspace, database = self.name + '.sqlite')
        from .to_dot import display_constraints
        print(display_constraints(self.path.root_constraint))
        return st



        

