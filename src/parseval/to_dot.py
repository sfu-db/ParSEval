import pydot
from typing import Callable, Dict, TYPE_CHECKING, Iterable

from .plan.rex import LogicalOperator
from .uexpr import Constraint, PlausibleBranch
from enum import Enum
import logging


class NodeStyle(Enum):
    """Visual styles for different node types."""

    CONSTRAINT = "constraint"
    PLAUSIBLE_UNEXPLORED = "plausible_unexplored"
    PLAUSIBLE_INFEASIBLE = "plausible_infeasible"
    PLAUSIBLE_PENDING = "plausible_pending"
    ROOT = "root"


def _get_default_node_styles(node):
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
        "COVERED": {
            "fillcolor": "#FFD7D7",
            "fontcolor": "#004400",
            "color": "#66CC66",
            "style": "filled",
            "shape": "box",
        },
        "POSITIVE": {
            "fillcolor": "#C8FACC",
            "fontcolor": "#004400",
            "color": "#66CC66",
            "style": "filled",
            "shape": "box",
        },
        "NEGATIVE": {
            "fillcolor": "#FFD7D7",
            "fontcolor": "#660000",
            "color": "#CC6666",
            "style": "filled",
            "shape": "box",
        },
        "UNEXPLORED": {
            "fillcolor": "#EEEEEE",
            "fontcolor": "#666666",
            "color": "#AAAAAA",
            "style": "dashed",
            "shape": "box",
        },
        "INFEASIBLE": {
            "fillcolor": "#E5E5FF",
            "fontcolor": "#333399",
            "color": "#9999FF",
            "style": "filled",
            "shape": "box",
        },
        "PENDING": {
            "fillcolor": "#FFF8D9",
            "fontcolor": "#665500",
            "color": "#FFCC00",
            "style": "filled",
            "shape": "box",
        },
        "TIMEOUT": {
            "fillcolor": "#FFF0E0",
            "fontcolor": "#664400",
            "color": "#CC9966",
            "style": "filled",
            "shape": "box",
        },
        "ERROR": {
            "fillcolor": "#F5B7B1",
            "fontcolor": "#5B0000",
            "color": "#AA0000",
            "style": "filled,bold",
            "shape": "octagon",
        },
    }
    style = styles[node.__class__.__name__.upper()]
    if isinstance(node, PlausibleBranch):
        style = styles[node.plausible_type.name]
    return style


def to_string(value):
    if isinstance(value, Iterable):
        return "".join(str(v) for v in value)
    return str(value)


def _get_default_edge_styles(edge_type):
    styles = {
        0: {"color": "#DC3545", "label": "FALSE", "style": "solid", "fontsize": "12"},
        1: {
            "color": "#28A745",
            "label": "TRUE",
            "style": "solid",
            "fontsize": "12",
        },  # Green for TRUE
        # Purple for OUTER
        2: {
            "color": "#6610F2",
            "label": "OUTER",
            "style": "dashed",
            "fontsize": "12",
        },
        3: {
            "color": "#FD7E14",
            "label": "NULL",
            "style": "dotted",
            "fontsize": "12",
        },  # Orange for NULL
        # Teal for DUPLICATE
        4: {"color": "#20C997", "label": "DUP", "style": "dotted", "fontsize": "12"},
        5: {"color": "#20C997", "label": "MAX", "style": "dotted", "fontsize": "12"},
        6: {"color": "#20C997", "label": "MIN", "style": "dotted", "fontsize": "12"},
        7: {
            "color": "#20C997",
            "label": "GROUP_COUNT",
            "style": "dotted",
            "fontsize": "12",
        },
        8: {
            "color": "#20C997",
            "label": "GROUP_SIZE",
            "style": "dotted",
            "fontsize": "12",
        },
    }
    return styles.get(edge_type)


def render_edge(src, dst, node, edge_style: Callable = _get_default_edge_styles):
    labels = []
    bit = int(node.bit())
    style = edge_style(bit)

    # logging.info(f"plausible branch: {label} 1: {len(node.parent.delta[str(1)])}")
    # logging.info(f"plausible branch: {label} 0: {len(node.parent.delta[str(0)])}")

    hit = node.hit()
    style["label"] = style["label"] + f"\nhit: {hit}"

    edge = pydot.Edge(src, dst, **style)  # edge_style(bit)
    return edge


def uexpr_to_dot(
    node,
    graph,
    parent_node=None,
    counter=None,
    node_style: Callable = _get_default_node_styles,
    edge_style: Callable = _get_default_edge_styles,
):
    if counter is None:
        counter = [0]
    node_id = f"node{counter[0]}"
    counter[0] += 1
    dot_node = pydot.Node(node_id)

    label = node.__class__.__name__

    if isinstance(node, Constraint):
        label = (
            f"{node.operator.operator_type}({ str(node.sql_condition)}) \n {to_string(node.pattern())}"
            if node.sql_condition
            else "ROOT"
        )
    elif isinstance(node, PlausibleBranch):
        label = node.plausible_type.value
        label += f": {to_string(node.pattern())}"
    dot_node.obj_dict["attributes"]["label"] = label
    for key, value in node_style(node).items():
        dot_node.obj_dict["attributes"][key] = value

    graph.add_node(dot_node)
    if parent_node:
        dot_edge = render_edge(parent_node, dot_node, node, edge_style=edge_style)
        graph.add_edge(dot_edge)

    if hasattr(node, "children"):
        for bit, child in node.children.items():
            uexpr_to_dot(
                child,
                graph,
                dot_node,
                counter,
                node_style,
                edge_style,
            )


def display_uexpr(root_constraint):
    """"""
    dot_graph = pydot.Dot()
    # uexpr_to_dot(root_constraint, dot_graph)
    for bit, child in root_constraint.children.items():
        uexpr_to_dot(child, dot_graph)
    return dot_graph
