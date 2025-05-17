from __future__ import annotations
from typing import Union
from collections import defaultdict, OrderedDict
from src.instance.instance import Instance
from .uexpr_to_constraint import UExprToConstraint
# from .executor import Executor
from .encoder import Encoder
from .constant import Action
from src.solver.solver import Solver
import logging

logger = logging.getLogger('src.parseval.generator')

class Generator:
    def __init__(self, workspace, schema, query, dialect = 'sqlite', **kwargs):
        self.workspace = workspace
        self.schema = schema
        self.query = query
        self.dialect = dialect
        self.name = kwargs.get('name', 'public')
        self.plan = self.parser(query, schema)
        self.constraints = OrderedDict()
        # defaultdict(list)
        self.variables = set()

    def parser(self, query, schema):
        from src.expression.query import parser
        parse = parser.QParser()
        self.plan = parse.explain(query, schema)
        return self.plan

    def add_constraint(self, constraints, label):
        if not isinstance(constraints, list):
            constraints = [constraints]
        if label not in self.constraints:
            self.constraints[label] = []
        self.constraints[label].extend(constraints)

    def reset(self):
        self.path.reset()
        self.constraints.clear()


    

    def get_coverage(self, instance, **kwargs):
        if instance is None:
            instance =Instance.create(schema= self.schema, name = self.name, dialect = self.dialect)
            values = {
                'frpm': [
                    {'Academic Year': "2024", "District Code": 16, 'CDSCode': "CDSCode1"},
                    {'Academic Year': "2024", "District Code": 15, 'CDSCode': "CDSCode1"},
                    {'Academic Year': "2023", "District Code": 16},
                    {'Academic Year': "2023", "District Code": 15}
                ],
                'satscores': [
                    {'cds': "CDSCode1"}
                ]
            }
            for tbl, value in values.items():
                for val in value:
                    instance.create_row(tbl, val)
        
        self.path = UExprToConstraint(lambda constraint, label: self.add_constraint(constraint, label))
        self.encoder = Encoder(self.path)
        self.reset()
        st = self.encoder(self.plan, instance = instance)
        from .to_dot import display_constraints
        print(display_constraints(self.path.root_constraint))
        print(st)
        instance.to_db(self.workspace, database = self.name + '.sqlite')

    def generate(self, max_iter = 8, **kwargs):
        skips = set()
        size = 1
        instance =Instance.create(schema= self.schema, name = self.name, dialect = self.dialect)
        for tbl in instance._tables:
            instance.create_row(tbl, {})


        for _ in range(max_iter):
            self.path = UExprToConstraint(lambda constraint, label: self.add_constraint(constraint, label))
            self.encoder = Encoder(self.path)
            self.reset()
            st = self.encoder(self.plan, instance = instance)
            pattern = self.path.next_branch(instance)

            logger.info(pattern)
            target_vars = self.constraints.pop('variable', [])

            solver = Solver(target_vars)
            for label, constraints in self.constraints.items():
                solver.append(constraints)
            db_constraints = instance.get_db_constraints()
            solver.appendleft(db_constraints.pop('SIZE'))
            for label, constraints in db_constraints.items():
                if constraints:
                    flag = solver.add_conditional(constraints)

            concretes = None
            if solver.check():
                logging.info(f'solved : {pattern}')
            else:
                skips.add(pattern)
                logging.info(f'unsat: {pattern}')
            concretes = solver.model()
            instance.update_values(concretes)
            logging.info(self.constraints)
            logger.info(concretes)
            
            


        self.get_coverage(instance)



        



    def _one_execution(self, max_iter = 8, **kwargs):
        skips = set()

        size = 1
        instance =Instance.create(schema= self.schema, name = self.name, dialect = self.dialect)
        values = {
            'frpm': [
                {'Academic Year': "2023", "District Code`": 16},
                {'Academic Year': "2023", "District Code`": 15},
                {'Academic Year': "2024", "District Code`": 16},
                {'Academic Year': "2024", "District Code`": 15}
            ]
        }
        for tbl, value in values.items():
            for val in value:
                instance.create_row(tbl, val)
            # for tbl in instance._tables:
                # instance.create_row(tbl, {})

        for _ in range(max_iter):
            self.path = UExprToConstraint(lambda constraint, label: self.add_constraint(constraint, label))
            self.executor = Encoder(self.path)
            self.reset()
            st = self.executor(self.plan, instance = instance)
            pattern = self.path.next_branch(instance)
            target_vars = self.constraints.pop('variable', [])
            solver = Solver(target_vars)
            for label, constraints in self.constraints.items():
                solver.append(constraints)
                # solver.add(constraints)
                logging.info(constraints)
                # if pattern == '111':
                #     logging.info(constraints)
            db_constraints = instance.get_db_constraints()
            solver.appendleft(db_constraints.pop('SIZE'))
            # solver.add(db_constraints.pop('SIZE'))
            for label, constraints in db_constraints.items():
                if constraints:
                    flag = solver.add_conditional(constraints)
                    # logging.info(f'DB Constraints: {label} --> {flag}')
            if solver.check():
                concretes = solver.model()
                instance.update_values(concretes)
                logging.info(f'solved : {pattern}, {concretes}')
            else:
                skips.add(pattern)
                concretes = solver.model()
                instance.update_values(concretes)
                logging.info(self.constraints)
                logging.info(f'unsat: {pattern}')
            logging.info('**' * 30)

            
        
        from .to_dot import display_constraints
        # print(self.path.pprint())
        print(display_constraints(self.path.root_constraint))

        print('leaves: ',self.path.leaves)

        instance.to_db(self.workspace, database = self.name + '.sqlite')

        # print(self.path.pprint())
        return st



        

