



class Cluster:
    def __init__(self, cluster_id=None):
        self.cluster_id = cluster_id
        self.nodes = []
    def add_node(self, node):
        self.nodes.append(node)
    
    def __repr__(self):
        return f'cluster(ID = {self.cluster_id}, nodes = {len(self.nodes)})'
