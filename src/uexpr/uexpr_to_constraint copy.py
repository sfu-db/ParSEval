
import logging
from sqlglot import exp
from collections import OrderedDict
from typing import List, Tuple
from ..symbols._typing import Symbols, SymbolAndMultiplicity
from .rex import Pred, Summation
from .uexpr_path import UExprPath
from .constraint import Constraint
from .predicate import Predicate
from .cluster import Cluster
import z3

logger = logging.getLogger(__name__)

class UExprToConstraint:
    def __init__(self, add):
        self.constraints = []
        self.clusters = {}
        self.nodes = {}
        
        self.root_constraint = Constraint(None, 'ROOT', delta= None, info = {'num': 0})        
        self.root_constraint.tree = self
        self.nodes['ROOT'] = self.root_constraint

        self.current_constraint = self.root_constraint

        self.no_bit, self.yes_bit = '0', '1'
        self.positive_path = None
        self.add = add

    # def get_cluster(self, cluster_id) -> Cluster:
    #     if cluster_id not in self.clusters:
    #         assert cluster_id not in self.clusters, f'Cluster {cluster_id} already exist'
    #         parent = self.last_cluster.current_node
    #         self.clusters[cluster_id] = Cluster(cluster_id= cluster_id, parent= parent)
    #     return self.clusters[cluster_id]

    def which_branch(self, operator_key, operator_i, symbolic_exprs: List[Symbols], conditions: List[exp.Condition], branch = None):
        assert len(symbolic_exprs) == len(conditions), f'the length of symbolic expressions should be equal to conditions'
        
        # identifier = f'{operator_key}_{operator_i}_{condition_str}_{branch}'

        node = self.current_constraint

        for smt_expr, condition_str in zip(symbolic_exprs, conditions):
            identifier = f'{operator_key}_{operator_i}_{condition_str}_{branch}'

            logger.info(identifier)
            # if node.find_child():
            #     ...
            # Constraint(parent = parent, identifier= identifier, delta = [], info= {'num': 0})



        logger.info([str(c) for c in conditions])
        logger.info(symbolic_exprs)

        # self.add_child(operator_key, operator_i, condition_str, symbolic_expr, branch, process_neg= True)
        # logger.info(self.render_graph_graphviz())
        
    def reset_pointer(self, operator_key, operator_i):
        ...

    def reset(self):
        # self.current_cluster = 
        ...

    def add_child(self, operator_key, operator_i, condition_str, symbolic_expr, branch, process_neg = True):
        identifier = f'{operator_key}_{operator_i}_{condition_str}_{branch}'
        if identifier not in self.nodes:
            sibiling = f'{operator_key}_{operator_i}_{condition_str}_{not branch}'
            parent = self.nodes[sibiling].parent if sibiling in self.nodes else self.current_constraint
            child_node = Constraint(parent = parent, identifier= identifier, delta = [], info= {'num': 0})
            child_node.tree = self
            self.current_constraint = child_node
            self.nodes[identifier] = child_node
        else:
            child_node = self.nodes[identifier]
        p = Predicate(symbolic_expr, symbolic_expr.value)
        child_node.delta.append(p)        
        return child_node
    

    def advance(self):
        '''
            move the current path forward by one step
        '''

        

    def __str__(self):
        return f"Trace(Constraint = {len(self.nodes)}, current = {self.current_constraint})"

    
    def render_graph_graphviz(self):

        import pydot

        """
        Render the graphviz graph structure.

        Example to create a png:

        .. code-block::

            with open('somefile.png', 'wb') as file:
                file.write(session.render_graph_graphviz().create_png())

        :returns: Pydot object representing entire graph
        :rtype: pydot.Dot
        """
        dot_graph = pydot.Dot()

        for node in list(self.nodes.values()):
            dot_graph.add_node(node.render_node_graphviz())

        # for edge in list(self.edges.values()):
        #     dot_graph.add_edge(edge.render_edge_graphviz())

        return dot_graph
    


# class UExprToConstraint:
#     def __init__(self, add):
#         self.constraints = []
#         self.clusters = {}
#         # self.last_cluster = Cluster('ROOT', None)
#         # self.current_cluster = self.get_cluster('root')
#         self.root_constraint = Constraint(None, 'ROOT', delta= None, info = {'num': 0})
#         # Constraint(None, cluster = self.last_cluster, identifier= 'ROOT', delta = [])
        
#         self.root_constraint.tree = self
#         self.current_constraint = self.root_constraint
        
#         self.positive_path = None
#         self.add = add
    
#     # def create_cluster(self, cluster_id, parent) -> Cluster:
#     #     assert cluster_id not in self.clusters, f'Cluster {cluster_id} already exist'
#     #     self.clusters[cluster_id] = Cluster(cluster_id= cluster_id, parent= parent)
#     #     return self.clusters[cluster_id]

#     def get_cluster(self, cluster_id) -> Cluster:
#         if cluster_id not in self.clusters:
#             assert cluster_id not in self.clusters, f'Cluster {cluster_id} already exist'
#             parent = self.last_cluster.current_node
#             self.clusters[cluster_id] = Cluster(cluster_id= cluster_id, parent= parent)
#         return self.clusters[cluster_id]

#     def which_branch(self, operator_key, operator_i, condition_str, symbolic_expr, branch = None):
#         cluster_id = f'{operator_key}_{operator_i}_{condition_str}_{branch}'
#         cluster = self.get_cluster(cluster_id= cluster_id)
#         parent_node = cluster.parent
#         p = Predicate(symbolic_expr, symbolic_expr.value)

#         c = parent_node.find_child(p)
#         pneg = p.negate()
#         cneg = parent_node.find_child(p)

#         logger.info(cluster)
#         logger.info(p)
#         logger.info(c)
#         logger.info(cneg)

#         if c is None and cneg is None:
#             self.add_child(operator_key, operator_i, condition_str, symbolic_expr, branch, process_neg= True)
            

#             ...

#     def reset_pointer(self, operator_key, operator_i):
#         ...

#     def reset(self):
#         # self.current_cluster = 
#         ...


#     def add_child(self, operator_key, operator_i, condition_str, symbolic_expr, branch = None, process_neg = True):
#         cluster_id = f'{operator_key}_{operator_i}_{condition_str}_{branch}'
#         cluster = self.get_cluster(cluster_id)
#         if cluster_id != self.last_cluster.cluster_id:
#             self.last_cluster = cluster
#         p = Predicate(symbolic_expr, symbolic_expr.value)
#         c = Constraint(parent= cluster.current_node, cluster= cluster, identifier= str(condition_str), delta = [p])
#         c.tree = self
#         cluster.add_node(c)
#         cluster.current_node.children.append(c)
#         return c



        
    

    




class UExprToConstraint2:
    '''
        Trace all execution paths.
    '''
    def __init__(self, add):
        self.constraints = []
        self.root_constraint = UExprPath(None, None, None, None)
        self.current_constraint = self.root_constraint
        self.expected_path = None
        self.add = add

        self.processed = False

    def reset(self, expected):
        self.current_constraint = self.root_constraint
        c = [self.root_constraint]
        while c:
            op = c.pop()
            for child in op.children:
                child.upreds.clear()
                c.append(child)
        if expected is None:
            self.expected_path = None
        else:
            self.expected_path = []
            tmp = expected
            while tmp.predicate is not None:
                self.expected_path.append(tmp.predicate)
                tmp = tmp.parent
    
    def update_branch(self, operator, smt_exprs: List[SymbolAndMultiplicity]):
        '''
            Given different symbol constraints, update uexpr constraints
        '''
        if not smt_exprs:
            return
        
        c = self.root_constraint.find_child(operator.i())
        if c is None:
            c = self.current_constraint.add_child(operator.i(), [], operator.key)
        
        if operator.key == 'join':
            preds = []
            for smt_expr in smt_exprs:
                preds.append(Pred(this = smt_expr[0] * smt_expr[1], result = smt_expr[0].value, t = smt_expr[1]))
            c.upreds.append(sum(preds))
        elif operator.key == 'filter':
            for smt_expr in smt_exprs:
                c.upreds.append(Pred(this = smt_expr[0], result = smt_expr[0].value, t = smt_expr[1]))

        self.current_constraint = c

        self.next_constraints()

    def next_constraints(self):

        visit = set()
        for child in self.current_constraint.parent.children:
            if child._type == 'filter' or child._type == 'join':
                predicate = sum(child.upreds)
                expr = predicate.this > 0

                logger.info(predicate)
                self.add(expr.expr)


                pp = child.get_uexpr()

                logger.info(pp)
                logger.info(pp.t)
        # if self.parent is None:
        #     return []
        # from itertools import chain, zip_longest
        # asserts = self.parent.get_uexpr()
        # queries = []

        # for c, p in zip_longest(self.upreds, asserts,  fillvalue=None):
        #     print(f'p: {p} -- >{self.parent.index}')
        #     print(f'c: {c} --> {self.index}')
        #     print('**' * 10)
        #     if p is None:
        #         queries.append(c.to_smt())
        #     else:
        #         print(f'will mul {p} --> {c.to_smt()}')
        #         queries.append(p * c.to_smt())
        # return queries
        ...

    def which_branch(self, operator, smt_exprs: List):
        '''
            We have different strategy to process different operator.
        '''
        if not smt_exprs:
            return

        c = self.root_constraint.find_child(operator.i())

        if c is None:
            c = self.current_constraint.add_child(operator.i(), [])

        flag = False
        for smt_expr in smt_exprs:
            if smt_expr.this > 0:
                flag = True
            c.upreds.append(smt_expr)

        if not flag:
            ...
            # logger.info(operator.key)
            # for e in c.get_uexpr():
            #     logger.info(e)
                # self.add(e)
        self.current_constraint = c  

    def find_constraint(self, id):
        return self._find_constraint(self.root_constraint, id)

    def _find_constraint(self, constraint, id):
        if constraint.id == id:
            return constraint
        else:
            for child in constraint.children:
                found = self._find_constraint(child, id)
                if found is not None:
                    return found
        return None


