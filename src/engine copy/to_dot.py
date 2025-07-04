

import pydot
from typing import Callable, Dict
from collections import deque

SHAPE = {    'Constraint': lambda x: 'box'}
COLOR = {'Constraint': lambda x: 0xEEF7FF}
BORDER_COLOR = {'Constraint': lambda x: 0xEEEEEE}
LABEL = {'Constraint': lambda x: x.identifier + f":(positive = {x.branch_type})"}


def to_dot(node, 
           shape: Dict[str, Callable] = SHAPE, 
           color: Dict[str, Callable] = COLOR, 
           border_color: Dict[str, Callable] = BORDER_COLOR, 
           label: Dict[str, Callable] = LABEL):
    
    """
    Render a node suitable for use in a Pydot graph using the set internal attributes.

    @rtype:  pydot.Node
    @return: Pydot object representing node
    """

    import pydot
    # + str(self.id) +
    dot_node = pydot.Node(node.unique_id)
    
    dot_node.obj_dict["attributes"]["label"] = '<<font face="lucida console">{}</font>>'.format(
        label[node.__class__.__name__](node)
    )
    dot_node.obj_dict["attributes"]["label"] = dot_node.obj_dict["attributes"]["label"].replace("\\n", "<br/>")
    dot_node.obj_dict["attributes"]["shape"] = shape[node.__class__.__name__](node)
    dot_node.obj_dict["attributes"]["color"] = "#{:06x}".format(color[node.__class__.__name__](node))
    dot_node.obj_dict["attributes"]["fillcolor"] = "#{:06x}".format(color[node.__class__.__name__](node))

    return dot_node


def display_constraints(root_constraint):
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
    q = deque([root_constraint])
    edges = []
    while  q:
        node = q.popleft()
        dot_node = to_dot(node)
        dot_graph.add_node(dot_node)
        for bit, child in node.children.items():
            q.append(child)
            edges.append((node.unique_id, child.unique_id, len(child.delta)))

    for edge in edges:
        src = edge[0]
        dst = edge[1]
        label = edge[2]
        color = 'blue' if label > 0 else 'red'
        dot_edge = pydot.Edge(src, dst, label = label, color = color)            
        dot_graph.add_edge(dot_edge)

    return dot_graph
