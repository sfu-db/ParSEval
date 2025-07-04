


from collections import defaultdict

class ConstraintGroup:
    def __init__(self):
        self.groups = defaultdict(set)
    def add_constraint(self, identifier, expr):
        self.groups[identifier].add(expr)
    
    def get_factorized_constraints(self):
        """Returns merged constraints in generalized form."""
        return [f"{identifier} for {identifier}" for identifier, condition in self.groups.items()]
    def __repr__(self):
        return str(self.get_factorized_constraints())

class PNode:
    cnt = 0
    def __init__(self, parent, predicate):
        self.predicate = predicate
        self.processed = False
        self.parent = parent
        self.constraint_group = ConstraintGroup()
        self.node_id = self.__class__.cnt
        self.__class__.cnt += 1
        self.tree = None
    
    # def no(self): return self.children.get(self.tree.no_bit)
    # def yes(self): return self.children.get(self.tree.yes_bit)
    # def get_children(self): return (self.no(), self.yes())

    def add_child(self, cluster, constraints):
        """Adds a child node, linking it to a cluster."""
        child = PNode(parent= self, )
        cluster.add_node(child)
        self.children.append(child)
        return child

class PlausibleChild:
    def __init__(self, parent, cond, tree):
        self.parent = parent
        self.cond = cond
        self.tree = tree
        self._smt_val = None

    def __repr__(self):
        return 'PlausibleChild[%s]' % (self.parent.pattern() + ':' + self.cond)

