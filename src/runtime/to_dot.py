

import pydot
from typing import Callable, Dict

SHAPE = {'Constraint': lambda x: 'box', 'PlausibleChild': lambda x: 'box'}
COLOR = {'Constraint': lambda x: "#{:06x}".format(0xEEF7FF), 'PlausibleChild': lambda x: 'none'}
LABEL_COLOR = {'Constraint': lambda x: "#{:06x}".format(0x000000), 'PlausibleChild': lambda x:   "#{:06x}".format(0x4B0082) if str(x.branch_type) == 'PLAUSIBLE' else  "#{:06x}".format(0x2E8B57)}
BORDER_COLOR = {'Constraint': lambda x: "#{:06x}".format(0xEEEEEE), 'PlausibleChild': lambda x:  'none'}
LABEL = {'Constraint': lambda x: clean_symbol(render_cosntraint(x) ), 'PlausibleChild': lambda x: str(x)}

def render_cosntraint(node):
    identifier = node.operator_key
    if node.operator_i:
        identifier += node.operator_i
    # if node.taken is not None:
    #     identifier += f"{int(node.taken)}"
    if node.sql_condition:
        identifier += f"({node.sql_condition})"
    return identifier



def clean_symbol(symbol) -> str:
    mappings = [
        ('>=' , '&ge;'),
        ('<=' , '&le;'),
        ('>' , '&gt;'),
        ('<' , '&lt;'),
        # '=' : '&eq;',
        ('<>' , '&ne;'),
        ('\\n', '</br>')
    ]
    for k, v in mappings:
        symbol = symbol.replace(k, v)
    return symbol

def render_edge(src, dst, node):
    hit = len(node.delta) if hasattr(node, 'delta') else ''
    if hasattr(node, 'delta'):
        hit = len(node.delta)
        edge_label = hit
        edge_color = 'blue' if isinstance(edge_label, int) and edge_label > 0 else 'red'
        return pydot.Edge(src, dst, label = edge_label, color = edge_color)
    else:
        if str(node.branch_type) == 'PLAUSIBLE':
            colour = 'orange'
        elif str(node.branch_type) == 'POSITIVE':
            colour = 'green'
        else:
            colour = 'red'
        return pydot.Edge(src, dst, style = 'dashed', dir = 'none', color = colour, label = f"bit: {node.bit()}")
    
def to_dot(node, graph, parent_node = None,
           counter = [0],
           shape: Dict[str, Callable] = SHAPE, 
           color: Dict[str, Callable] = COLOR, 
           border_color: Dict[str, Callable] = BORDER_COLOR,            
           label: Dict[str, Callable] = LABEL,
           label_color: Dict[str, Callable] = LABEL_COLOR):
    node_id = f"node{counter[0]}"
    counter[0] += 1
    dot_node = pydot.Node(node_id)

    dot_node.obj_dict["attributes"]["label"] = '<<font face="lucida console">{}</font>>'.format(
        clean_symbol(label[node.__class__.__name__](node))
    )
    dot_node.obj_dict["attributes"]["shape"] = shape[node.__class__.__name__](node)
    dot_node.obj_dict["attributes"]["color"] = color[node.__class__.__name__](node)
    dot_node.obj_dict["attributes"]["fillcolor"] = color[node.__class__.__name__](node)
    dot_node.obj_dict["attributes"]["fontcolor"] = label_color[node.__class__.__name__](node)
    graph.add_node(dot_node)
    
    if parent_node:
        dot_edge = render_edge(parent_node, dot_node, node)
        graph.add_edge(dot_edge)
    
    if hasattr(node, 'children'):
        # for child in node.children:
        for bit, child in node.children.items():
            to_dot(child, graph, dot_node, counter, shape, color, border_color, label)


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
    to_dot(root_constraint, dot_graph)
    return dot_graph


from .constant import PathConstraintType




class ConstraintTreeVisitor:
    def visit(self, node):
        if node.constraint_type in {}:
            ...
        

def to_uexpr(node, graph, parent_node = None, counter = [0]):
    node_id = f"node{counter[0]}"
    counter[0] += 1

    # dot_node = pydot.Node(node_id)
    # dot_node.obj_dict["attributes"]["label"] = '<<font face="lucida console">{}</font>>'.format(
    #     clean_symbol(label[node.__class__.__name__](node))
    # )
    # dot_node.obj_dict["attributes"]["shape"] = shape[node.__class__.__name__](node)
    # dot_node.obj_dict["attributes"]["color"] = color[node.__class__.__name__](node)
    # dot_node.obj_dict["attributes"]["fillcolor"] = color[node.__class__.__name__](node)
    # dot_node.obj_dict["attributes"]["fontcolor"] = label_color[node.__class__.__name__](node)
    # graph.add_node(dot_node)

    if node.constraint_type in {PathConstraintType.VALUE, PathConstraintType.PATH}:
        f"|{str(node.sql_condition)}|"
    
    # if parent_node:
    #     dot_edge = render_edge(parent_node, dot_node, node)
    #     graph.add_edge(dot_edge)
    
    # if hasattr(node, 'children'):
    #     # for child in node.children:
    #     for bit, child in node.children.items():
    #         to_dot(child, graph, dot_node, counter, shape, color, border_color, label)
    ...