from collections import defaultdict
from src.expr import QParser
from src.instance import Instance, create_instance
from ..uexpr_to_constraint import UExprToConstraint

# from src.symbols.solver import Solver
# from src.uexpr.planner import Plan
from .executeor import HybridExecutor
from typing import Dict, List, Any
from sqlglot import exp
import logging

from ..to_dot import display_constraints

from src.context import Context
logger = logging.getLogger('src.hybrid')



class UExprGenerator:
    def __init__(self, workspace, schema, query, initial_values, dialect = 'sqlite') -> None:
        self.workspace = workspace
        self.dialect = dialect
        self.schema = schema
        self.querr = query
        qparser = QParser()

        self.plan = qparser.explain(query, schema, dialect = dialect)        
        self.initial_values = initial_values
        self.cases = []
        
        self.context: Context = Context()
        self.new_constraints = defaultdict(list)
        self.positives = self.context.get('positive_branch')
        self.negatives = self.context.get('negative_branch')
        self.unvisited = set()
        self.path = UExprToConstraint(self.context, lambda c, i, l: self.add_constraint(c, i, l))
        self.executor = HybridExecutor(self.context, self.path)
        # self.solver = Solver()

    def add_constraint(self, c, identity , label = 'positive'):
        if label == 'negative':
            if identity not in self.negatives:
                self.negatives[identity] = []
            self.negatives[identity].append(c)
        elif label == 'positive':
            if identity not in self.positives:
                self.positives[identity] = []
            self.positives[identity].append(c)
        else:
            if isinstance(identity, str):
                self.new_constraints[identity] = c
            else:
                self.new_constraints[identity].append(c)

    def _update_concrete(self, values: Dict, instance: Instance) -> Dict[str, List[Dict[str, Any]]]:
        concretes = defaultdict(list)
        tables = defaultdict(set)
        for key, value in values.items():
            if key in self.context.get('symbols'):
                self.context.get('symbols', key).value = value
            if key in self.context.get('symbol_to_table'):
                table, column, column_index = self.context.get('symbol_to_table', key)
                tables[table].add(column)
        for table_name, columns in tables.items():
            tbl = instance.get_table(table_name= table_name)
            for row in tbl:
                v = {'multiplicity' : row.multiplicity.value}
                for column in columns:
                    index = tbl.get_column_index(column)
                    v[column] = row[index].value
                concretes[table_name].append(v)
        return concretes


    def _update_instance(self, values: Dict, instance: Instance):
        concretes = defaultdict(list)
        tables = defaultdict(set)
        for key, value in values.items():
            if key in self.context[0]:
                self.context[0][key].value = value
            if key in self.context[2]:
                table, column = self.context[2][key]
                tables[table].add(column)
        for table_name, columns in tables.items():
            tbl = instance.get_table(table_name= table_name)
            for row in tbl:
                v = {'multiplicity' : row.multiplicity.value}
                for column in columns:
                    index = tbl.get_column_index(column)
                    v[column] = row[index].value
                concretes[table_name].append(v)
        
        for table_name in instance._tables:
            concrete_values = concretes.get(table_name, [])
            row_size = len(concrete_values)
            for index in range(row_size):
                initials = concrete_values[index] if index < len(concrete_values) else {}
                multiplicity = initials.pop('multiplicity', 1)
                instance.append_tuple2(table_name, context= self.context, initial_values = initials, multiplicity= multiplicity)
        return instance
    
    def reset(self):
        self.context.reset()
        self.path.reset()


    def get_integrity_constraints(self):
        additional = []
        for c in self.positives.get('integrity', []):
            if hasattr(c, 'dtype'):
                additional.append(c.expr)
        return additional

    def get_positive_constraints(self):
        additional = []
        for key, constraints in self.positives.items():
            if key.startswith('CASE'):
                additional.append(sum(constraints).expr < len(constraints))
                additional.append(sum(constraints).expr > 0)
            elif key != 'integrity':
                for c in constraints:
                    if hasattr(c, 'dtype'):
                        additional.append(c.expr)
                    elif hasattr(c, 'key'):
                        additional.append(c.to_smt().expr > 0)
        return additional

    def get_negative_constraints(self):
        smt_exprs = {}
        for identity, constraints in self.negatives.items():
            cond = []
            for c in constraints:
                if hasattr(c, 'dtype'):
                    cond.append(c.expr)
                elif hasattr(c, 'key'):
                    cond.append(c.to_smt().expr)
                else:
                    cond.append(c)
            smt_exprs[identity] = sum(cond) > 0 if len(cond) > 1 else cond[0]
        return smt_exprs

    def generate(self, max_iterations, initial_values, max_size = 20, timeout = None):
        size = 1
        ...


    def normalize(self, instance, *cov_constraints):
        smt_exprs = defaultdict(list)
        integrity_cosntraints = self.get_integrity_constraints()
        db_constraints = instance.get_db_constraints()

        ### if we should use solver to solve pk and foreign key constraints
        if self.context.get('pk_fk_symbols').intersection(self.context.get('used_symbols')):
            smt_exprs['DB'].extend(db_constraints['FK'])
            smt_exprs['DB'].extend(db_constraints['PK'])
            for tbl in instance._tables.values():
                for row in tbl:
                    smt_exprs['DB'].append(row.multiplicity.expr == 1)
        ### add db size constraitns
        # smt_exprs['DB'].extend(instance.get_size_constraints())
        ### add integrity constraints
        smt_exprs['DB'].extend(integrity_cosntraints)
        ### add query U-expr
        smt_exprs['POSITIVE'].extend(self.get_positive_constraints())

        #     #### process Negative
        smt_exprs['NEGATIVE']= self.get_negative_constraints()

        return smt_exprs

    def update_concrete(self, values):
        concretes = defaultdict(list)
        for key, value in values.items():
            if key in self.context.get('symbols'):
                self.context.get('symbols', key).value = value
        return concretes

    def generate(self, initial_values, size = 1, max_iterations = 3):

        ...
    
    def _one_execution(self, initial_values = {}, size = 1, max_iterations = 3):
        '''
            Model positive path of the input query. 
            Return Concrete Values which will satisfy the query positive branch
        '''
        self.reset()
        instance = create_instance(self.context, self.schema, initial_values, dialect= self.dialect, size= size)
        self.path.instance = instance
        attempt = 0
        while attempt < max_iterations:
            logger.info(f'attempt: {attempt}')
            self.path.reset()
            output = self.executor(self.plan, instance)
            logger.info(output)

            self.path.encode_constraint()
            break
            # if self.has_uncoverd_path():
            #     self.path.encode_constraint()
            #     smt_exprs = self.normalize(instance)
            #     sat, solutions, unsolved = self.solver._find_model2(smt_exprs)
            #     if sat in {'Gave up', 'No Solutions'}:
            #         # size += 1
            #         ...
            #     elif sat == 'sat':
            #         concretes = self.update_concrete(solutions)
            #         concretes = self._update_concrete(solutions, instance)
            #         initial_values = concretes
            #         instance.to_db(self.workspace, f'{instance.name}_{size}_{attempt}.sqlite')
            #         # break
            #     # size += 1
            # else:
            #     break
            attempt += 1

        # self.reset()
        self.path.reset()
        output = self.executor(self.plan, instance)
        # logger.info(self.path.render_graph_graphviz())

        logger.info(display_constraints(self.path.root_constraint))

        for tbl, tb in instance._tables.items():
            logger.info(f'{tbl}: {len(tb.tuples)}')
        instance.to_db(self.workspace, f'{instance.name}_{size}_.sqlite')
        return initial_values

