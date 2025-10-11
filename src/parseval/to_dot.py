import pydot
from typing import Callable, Dict, TYPE_CHECKING
from .plan.step import LogicalOperator
from .uexpr import Constraint, PlausibleBranch
from enum import Enum


class NodeStyle(Enum):
    """Visual styles for different node types."""

    CONSTRAINT = "constraint"
    PLAUSIBLE_UNEXPLORED = "plausible_unexplored"
    PLAUSIBLE_INFEASIBLE = "plausible_infeasible"
    PLAUSIBLE_PENDING = "plausible_pending"
    ROOT = "root"


def _get_default_node_styles(node_type):
    styles = {
        "ROOT": {
            "shape": "oval",
            "style": "filled",
            "fillcolor": "#E8F4F8",
            "color": "#0366d6",
            "penwidth": "2",
        },
        "CONSTRAINT": {
            "shape": "box",
            "style": "filled",
            "fillcolor": "#EEF7FF",
            "color": "#CCCCCC",
            "fontcolor": "#000000",
            "penwidth": "1",
        },
        "PLAUSIBLEBRANCH": {
            "shape": "box",
            "style": "filled,dashed,rounded",
            "fillcolor": "#FFF3CD",
            "color": "#FFC107",
            "penwidth": "2",
        },
        "PLAUSIBLE_INFEASIBLE": {
            "shape": "box",
            "style": "filled,rounded",
            "fillcolor": "#F8D7DA",
            "color": "#DC3545",
            "penwidth": "1",
        },
    }
    return styles[node_type]


def _get_default_edge_styles(edge_type):
    styles = {
        0: {"color": "#DC3545", "label": "FALSE", "style": "solid"},
        1: {"color": "#28A745", "label": "TRUE", "style": "solid"},  # Green for TRUE
        # Purple for OUTER
        2: {
            "color": "#6610F2",
            "label": "OUTER",
            "style": "dashed",
        },
        3: {"color": "#FD7E14", "label": "NULL", "style": "dotted"},  # Orange for NULL
        # Teal for DUPLICATE
        4: {
            "color": "#20C997",
            "label": "DUP",
            "style": "dotted",
        },
    }
    return styles.get(edge_type)


def render_edge(src, dst, node, edge_style: Callable = _get_default_edge_styles):
    labels = []
    bit = int(node.bit())
    style = edge_style(bit)
    # style["label"] = style["label"] + f" ({bit})"
    edge = pydot.Edge(src, dst, **style)  # edge_style(bit)
    return edge


def uexpr_to_dot(
    node,
    graph,
    parent_node=None,
    counter=[0],
    node_style: Callable = _get_default_node_styles,
    edge_style: Callable = _get_default_edge_styles,
):
    node_id = f"node{counter[0]}"
    counter[0] += 1
    dot_node = pydot.Node(node_id)

    label = node.__class__.__name__

    if isinstance(node, Constraint):
        label = (
            f"{node.operator.operator_type}({ str(node.sql_condition)})"
            if node.sql_condition
            else "ROOT"
        )
    elif isinstance(node, PlausibleBranch):
        label = node.plausible_type.value
        label += f": {node.pattern()}"

    dot_node.obj_dict["attributes"]["label"] = label
    for key, value in node_style(node.__class__.__name__.upper()).items():
        dot_node.obj_dict["attributes"][key] = value
    # dot_node.obj_dict["attributes"].update(node_style(node.__class__.__name__.upper()))
    graph.add_node(dot_node)
    if parent_node:
        dot_edge = render_edge(parent_node, dot_node, node, edge_style=edge_style)
        graph.add_edge(dot_edge)

    if hasattr(node, "children"):
        for bit, child in node.children.items():
            uexpr_to_dot(child, graph, dot_node, counter, node_style, edge_style)


def display_uexpr(root_constraint):
    """"""
    dot_graph = pydot.Dot()
    uexpr_to_dot(root_constraint, dot_graph)
    # for bit, child in root_constraint.children.items():
    #     uexpr_to_dot(child, dot_graph)
    return dot_graph
