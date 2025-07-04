

from .node import PNode
from .cluster import Cluster

class PTree:
    def __init__(self, add):
        self.constraints = []
        self.root_constraint = PNode(None, None, None, None)
        self.current_constraint = self.root_constraint
        self.expected_path = None
        self.add = add
        
        self.processed = False
        self.clusters = {}

    
    def create_cluster(self, cluster_id ):
        if cluster_id not in self.clusters:
            self.clusters[cluster_id] = Cluster(cluster_id= cluster_id)
        return self.clusters[cluster_id]
    def get_cluster(self, cluster_id):
        return self.clusters.get(cluster_id)
    

    def which_branch(self, operator_key, operator_i, condition,  symbolic_expr, branch):
        
        ...
                     

