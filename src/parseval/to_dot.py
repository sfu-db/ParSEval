"""Utilities to render the internal UExpr constraint tree to Graphviz DOT via pydot.

This module provides helpers to convert `Constraint` / `PlausibleBranch` trees
into a `pydot.Dot` graph for visualization. Styles are defined for nodes and
edges and are applied without mutating module-level style templates.
"""

from __future__ import annotations

import copy
from typing import Callable, Dict, TYPE_CHECKING, Iterable, Any, Optional

import pydot

from enum import Enum

from parseval.uexpr.uexprs import Constraint, PlausibleBranch
from parseval.constants import PBit

NODE_STYLES: Dict[str, Dict[str, str]] = {}

EDGE_STYLES = {
    PBit.FALSE: {
        "color": "#DC3545",
        "label": "FALSE",
        "style": "solid",
        "fontsize": "12",
    },
    PBit.TRUE: {
        "color": "#28A745",
        "label": "TRUE",
        "style": "solid",
        "fontsize": "12",
    },
    PBit.JOIN_TRUE: {
        "color": "#28A745",
        "label": "JOIN_TRUE",
        "style": "solid",
        "fontsize": "12",
    },
    PBit.JOIN_LEFT: {
        "color": "#DC3545",
        "label": "JOIN_LEFT",
        "style": "solid",
        "fontsize": "12",
    },
    PBit.JOIN_RIGHT: {
        "color": "#DC3545",
        "label": "JOIN_RIGHT",
        "style": "solid",
        "fontsize": "12",
    },
    PBit.GROUP_COUNT: {
        "color": "#20C997",
        "label": "GROUP_COUNT",
        "style": "dotted",
        "fontsize": "12",
    },
    PBit.GROUP_SIZE: {
        "color": "#20C997",
        "label": "GROUP_SIZE",
        "style": "dotted",
        "fontsize": "12",
    },
    PBit.DUPLICATE: {
        "color": "#20C997",
        "label": "DUP",
        "style": "dotted",
        "fontsize": "12",
    },
    PBit.NULL: {
        "color": "#FD7E14",
        "label": "NULL",
        "style": "dotted",
        "fontsize": "12",
    },
    PBit.MAX: {
        "color": "#20C997",
        "label": "MAX",
        "style": "dotted",
        "fontsize": "12",
    },
    PBit.MIN: {
        "color": "#20C997",
        "label": "MIN",
        "style": "dotted",
        "fontsize": "12",
    },
    PBit.HAVING_TRUE: {
        "color": "#20C997",
        "label": "TRUE",
        "style": "dotted",
        "fontsize": "12",
    },
    PBit.HAVING_FALSE: {
        "color": "#DC3545",
        "label": "FALSE",
        "style": "dotted",
        "fontsize": "12",
    },
    PBit.GROUP_NULL: {
        "color": "#FD7E14",
        "label": "NULL",
        "style": "dotted",
        "fontsize": "12",
    },
    PBit.GROUP_DUPLICATE: {
        "color": "#20C997",
        "label": "DUP",
        "style": "dotted",
        "fontsize": "12",
    },
    PBit.AGGREGATE_SIZE: {
        "color": "#20C997",
        "label": "AGGREGATE_SIZE",
        "style": "dotted",
        "fontsize": "12",
    },
    PBit.PROJECT: {
        "color": "#17A2B8",
        "label": "PROJECT",
        "style": "dashed",
        "fontsize": "12",
    },
}


class NodeStyle(Enum):
    """Visual styles for different node types."""

    CONSTRAINT = "constraint"
    PLAUSIBLE_UNEXPLORED = "plausible_unexplored"
    PLAUSIBLE_INFEASIBLE = "plausible_infeasible"
    PLAUSIBLE_PENDING = "plausible_pending"
    ROOT = "root"


def _get_default_node_styles(node):
    """Return a style mapping for a given node.

    The function looks up a dictionary of predefined styles keyed by the
    node class name or the plausible branch type. It returns a mapping of
    Graphviz attributes to string values.

    Args:
        node: A `Constraint` or `PlausibleBranch` instance.

    Returns:
        A dict of Graphviz attribute names to their string values.
    """

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
    # Prefer specific plausible-type styles for PlausibleBranch instances.
    key = node.__class__.__name__.upper()
    # If node is a PlausibleBranch, try to use the PlausibleType name first.
    if isinstance(node, PlausibleBranch):
        p_key = node.plausible_type.name
        if p_key in styles:
            return copy.deepcopy(styles[p_key])

    # Fall back to class-name-based style; if missing, return a safe default.
    return copy.deepcopy(styles.get(key, {"shape": "box", "style": "filled"}))


def to_string(value: Any) -> str:
    """Convert a value to a readable string for node labels.

    - Strings are returned unchanged.
    - Iterables (lists/tuples) are joined with commas and wrapped in brackets.
    - None becomes the literal "None".

    Args:
        value: Any value to stringify.

    Returns:
        A human-friendly string representation.
    """
    if value is None:
        return "None"
    if isinstance(value, str):
        return value
    if isinstance(value, Iterable):
        return "[" + ", ".join(str(v) for v in value) + "]"
    return str(value)


def render_edge(
    src: pydot.Node, dst: pydot.Node, node: Constraint | PlausibleBranch
) -> pydot.Edge:
    """Create a pydot.Edge between two nodes with styling based on branch bit.

    This function does not mutate global style templates; it deep-copies a
    style template before updating it for the specific edge (for example,
    appending the hit count to the label).

    Args:
        src: Source `pydot.Node`.
        dst: Destination `pydot.Node`.
        node: The child `Constraint` or `PlausibleBranch` whose bit determines
            the edge style.

    Returns:
        A configured `pydot.Edge` instance.
    """
    # Safely copy a style template so module-level dicts are not mutated.
    style_template = EDGE_STYLES.get(node.bit(), {})
    style = copy.deepcopy(style_template)
    hit = node.hit()
    # Append hit information to the label without altering the template.
    if style.get("label"):
        style["label"] = f"{style['label']}\nhit: {hit}"
    else:
        style["label"] = f"hit: {hit}"

    edge = pydot.Edge(src, dst, **style)
    return edge


def uexpr_to_dot(
    node: Constraint | PlausibleBranch,
    graph: pydot.Dot,
    parent_node: Optional[pydot.Node] = None,
    counter: Optional[list[int]] = None,
    node_style: Callable[[Any], Dict[str, str]] = _get_default_node_styles,
) -> None:
    """Recursively add nodes and edges for a constraint tree to a DOT graph.

    Args:
        node: The current `Constraint` or `PlausibleBranch` to render.
        graph: A `pydot.Dot` graph instance to which nodes/edges are added.
        parent_node: Optional parent `pydot.Node` (used to create an edge).
        counter: Optional single-element list used to generate unique node ids.
        node_style: Callable that returns node attribute mapping for a node.
    """
    if counter is None:
        counter = [0]
    node_id = f"node{counter[0]}"
    counter[0] += 1
    dot_node = pydot.Node(node_id)

    # Build a human-friendly label for the node.
    if isinstance(node, Constraint):
        if node.sql_condition:
            label = f"{node.step_type.value.capitalize()}({str(node.sql_condition)}) \n {to_string(node.pattern())}"
        else:
            label = "ROOT"
    elif isinstance(node, PlausibleBranch):
        label = (
            f"{node.plausible_type.value}: {to_string(node.pattern())} \n {node.branch}"
        )
    else:
        label = node.__class__.__name__

    # Set node label and styles.
    attrs = {"label": label}
    attrs.update(node_style(node))
    for key, value in attrs.items():
        dot_node.obj_dict.setdefault("attributes", {})[key] = value

    graph.add_node(dot_node)
    if parent_node is not None:
        dot_edge = render_edge(parent_node, dot_node, node)
        graph.add_edge(dot_edge)

    # Recurse on children if present.
    if hasattr(node, "children"):
        for bit, child in node.children.items():
            uexpr_to_dot(child, graph, dot_node, counter, node_style)


def display_uexpr(root_constraint: Constraint) -> pydot.Dot:
    """Create a `pydot.Dot` graph for the given root constraint.

    The function renders all immediate children of `root_constraint` into a
    new graph and returns the graph object (caller may write or display it).

    Args:
        root_constraint: The root `Constraint` to visualize.

    Returns:
        A configured `pydot.Dot` instance containing the graph.
    """
    dot_graph = pydot.Dot(graph_type="digraph")
    for bit, child in root_constraint.children.items():
        uexpr_to_dot(child, dot_graph)
    return dot_graph
