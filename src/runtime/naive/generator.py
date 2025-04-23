from collections import defaultdict
from src.uexpr.instance import Instance
from src.uexpr.uexpr_to_constraint import UExprToConstraint
from src.symbols.solver import Solver
from src.uexpr.planner import Plan
from .executor import NaiveExecutor
from typing import Dict, List, Any
from sqlglot import exp
from src.symbols.ssa_factory import ssa_factory
from src.uexpr.helper import smt_complexity
import logging, z3, time

logger = logging.getLogger('src.naive')

class UExprGenerator:
    def __init__(self, workspace, schema, query, initial_values, db_id, question_id, dialect = 'sqlite') -> None:
        self.db_id = db_id
        self.question_id = question_id
        self.workspace = workspace
        self.dialect = dialect
        self.schema = schema
        self.querr = query
        self.plan = Plan(schema= schema, query= query, dialect= dialect)
        self.initial_values = initial_values
        self.cases = []
        self.context = ({}, [], {}, set(), set(), defaultdict(list), defaultdict(list), {}, defaultdict(list))
        # self.context = ({}, [], {}, set(), set(), defaultdict(list), defaultdict(list)) # (symbolic variables, paths, symbol: (table, column), pk_fk_symbols, related_columns, positives, negatives)
        self.new_constraints = []
        self.positives = self.context[5]
        self.negatives = self.context[6]
        self.unvisited = set()
        self.path = UExprToConstraint(lambda c: self.add_constraint(c))
        self.executor = NaiveExecutor(self.context, lambda c, k, l: self.add_constraint(c, k, l))
        self.solver = Solver()
    
    def add_constraint(self, c, identity = None, label = 'positive'):
        if label == 'negative':
            self.negatives[identity].append(c)
        else:
            self.positives[identity].append(c)
            self.new_constraints.append(c)
    
    def get_integrity_constraints(self, instance: Instance):
        additional = []
        for key, constraints in self.positives.items():
            if key in ['integrity']:
                for c in constraints:
                    if hasattr(c, 'dtype'):
                        additional.append(c.expr)
            elif key.startswith('CASE'):
                additional.append(sum(constraints).expr < len(constraints))
                additional.append(sum(constraints).expr > 0)
            else:
                if key.startswith('row_cnt'):
                    logger.info(constraints)
                for c in constraints:
                    if hasattr(c, 'dtype'):
                        additional.append(c.expr)
                    elif hasattr(c, 'key'):
                        additional.append(c.to_smt().expr > 0)
        return additional

    def get_negative_constraints(self, strategy = 'complete'):
        smt_exprs = []
        for identity, constraints in self.negatives.items():
            tmp_ = []
            for c in constraints:
                if hasattr(c, 'dtype'):
                    smt_exprs.append(c.expr)
                else:
                    tmp_.append(c.to_smt().expr)
            if tmp_:
                neg = sum(tmp_)
                smt_exprs.append(neg > 0)
        return smt_exprs

    def translate_negative_to_smt(self):
        smt_exprs = {}
        for identity, constraints in self.negatives.items():
            cond = []
            for c in constraints:
                if hasattr(c, 'dtype'):
                    smt_exprs[identity] = c.expr
                elif hasattr(c, 'key'):
                    cond.append(c.to_smt().expr)
            if cond:
                smt_exprs[identity] = sum(cond) > 0

            
        return smt_exprs

    def subset(self, values, skip = None):
        if skip is None:
            skip = set()
        else:
            skip = set(skip)

        from itertools import combinations
        subsets = []
        values = [v for v in values if v not in skip]
        # Iterate over sizes from len(lst) to 0
        for size in range(len(values), -1, -1):
            subsets.extend(combinations(values, size))  # Generate subsets of the current size
        return subsets #[subset if subset else 'POSITIVE_ONLY' for subset in subsets]
        

    def generate(self, max_iterations, max_size = 20, timeout = None):
        attempt, size = 0, 4
        self.reset()
        concretes = {}
        instance = Instance.initialize(self.context, self.schema, concretes, dialect= self.dialect, size = size, name= f'public_size{size}')
        pos, neg = self._one_translation(instance)

        branch_stats = {'total' : len(neg) + 1, 'negatives': list(neg.keys())}
        self.unvisited.update(set(neg.keys()))
        processed = set()
        skip = set()
        while attempt < max_iterations and self.unvisited:
            while size < max_size and self.unvisited:
                for combination in self.subset(self.unvisited, skip):
                    processing = set()
                    smt = []
                    # identifier = "" if len(combination) > 0 else 'positive'
                    processing.add('positive')
                    for u in combination:
                        if str(u) not in processed:
                            processing.add(u)
                            smt.append(neg[u])
                    if smt or 'positive' not in processed:
                        sat, solutions = self.solver._find_model(smt, pos)
                        if sat == 'sat':
                            self.unvisited.difference_update(processing)
                            processed.update(processing)
                            concretes = self._update_concrete(solutions, instance)
                            identifier = '#'.join(processing)
                            complexity = smt_complexity(*smt, *pos)

                            logger.info({ "qidx": self.question_id, 'db_id': self.db_id, 'smt': [*smt, *pos], 'complexity': complexity}, extra = {'to': 'smt'})

                            # logger.info(self.workspace)
                            # logger.info(f'{instance.name}_{identifier}.sqlite')
                            import re
                            def clean_string(s):
                                return re.sub(r'[^a-zA-Z0-9#]+', ' ', s).replace(' ','').strip()
                            instance.to_db(self.workspace, clean_string(f'{instance.name}_{identifier}') + '.sqlite')
                            branch_stats = {'total' : len(neg) + 1, 'negatives': list(neg.keys())}
                        elif len(processing) == 2 and 'positive' in processing and 'row_cnt' not in processing:
                            processing.discard('positive')
                            self.unvisited.difference_update(processing)
                            skip.update(processing)
                if self.unvisited:
                    size += 2
                    self.reset()
                    instance = Instance.initialize(self.context, self.schema, concretes, dialect= self.dialect, size = size, name= f'public_size{size}')
                    pos, neg = self._one_translation(instance)
                    skip.clear()
            attempt += 1
            size = 2
            logger.info(f'attempt : {attempt}')
        
        
        branch_stats['covered'] = len(processed)
        branch_stats['processed'] = processed
        # logger.info(branch_stats)
        logger.info({ "qidx": self.question_id, 'db_id': self.db_id, 'db_integrity': 2, **branch_stats}, extra = {'to': 'branch'})

    def _update_concrete(self, values: Dict, instance: Instance) -> Dict[str, List[Dict[str, Any]]]:
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
        return concretes

    def reset(self):
        for key in self.negatives:
            self.negatives[key].clear()
        for key in self.positives:
            self.positives[key].clear()

        self.negatives.clear()
        self.positives.clear()
        for v in self.context:
            v.clear()
        # self.context = ({}, [], {}, {})
        
    def normalize(self, branch, instance: Instance):
        pos, neg = [], {}
        # expr = sum(branch.expr)
        additional = self.get_integrity_constraints(instance)
        ### if we should use solver to solve pk and foreign key constraints
        if self.context[3].intersection(self.context[4]):
            pos.extend(instance.get_db_constraints())
            for tbl in instance._tables.values():
                for row in tbl:
                    pos.append(row.multiplicity.expr == 1)

        ### add db size constraitns
        pos.extend(instance.get_size_constraints())
        ### add integrity constraints
        pos.extend(additional)
        ### add query U-expr
        if branch.expr:
            e_ = []
            for e in branch.expr:
                e_.append(e.to_smt() > 0)            
            pos.append(ssa_factory.sany(*e_).expr)
        #### process Negative
        neg = self.translate_negative_to_smt()
        
        return pos, neg



    def _one_translation(self, instance: Instance):
        pos, neg = None, None
        branches = self.executor(self.plan.root, instance, strategy = 'complete')
        pos, neg = self.normalize(branches, instance)
        return pos, neg




    def _one_execution(self, initial_values = {}, timeout = 30, max_tries = 5):
        tries, size = 0, 2
        concretes = {}
        while tries < max_tries:
            self.reset()
            instance = Instance.initialize(self.context, self.schema, initial_values, dialect= self.dialect, size= 2)
            branches = self.executor(self.plan.root, instance)
            expr = sum(branches.expr)
            additional = self.get_integrity_constraints(instance)

            self.normalize(branches, instance)
            
            logger.info(additional)
            additional.extend(instance.get_db_constraints())
            negs = self.get_negative_constraints()
            smt = []
            if expr:
                smt.append(expr.to_smt().expr > 0)
            logger.info(expr.to_smt().expr)
            sat, solutions = self.solver._find_model(smt , additional)
            # size += size * 2
            size += 1
            if sat == 'Gave up':
                logger.info('Gave up')
            elif sat == 'No Solutions':
                logger.info('no solution')
                ...
            elif sat == 'sat':
                concretes = self._update_concrete(solutions, instance)
                logger.info(solutions)
                instance.to_db(self.workspace, f'{instance.name}.sqlite')
                
                break
            tries += 1
        return concretes