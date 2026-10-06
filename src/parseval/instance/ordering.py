"""Concrete SQL ordering and window functions over present row occurrences.

Positions in an ordered result depend on the stored values, so ORDER BY,
LIMIT/OFFSET and window functions execute concretely over the rows that are
present. Their outputs re-enter execution as Terms.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from functools import cmp_to_key

from parseval.terms.terms import (
    Direction,
    NullPlacement,
    OrderKeySpec,
    WindowBoundaryKind,
    WindowCallLayout,
    WindowFrameExclusion,
    WindowFrameMode,
    WindowFunctionKind,
)


def compare_values(left, right, spec: OrderKeySpec) -> int:
    if left is None or right is None:
        if left is right:
            return 0
        return -1 if (left is None) == (spec.nulls is NullPlacement.FIRST) else 1
    result = (left > right) - (left < right)
    return -result if spec.direction is Direction.DESC else result


def compare_keys(left: Sequence, right: Sequence, specs: Sequence[OrderKeySpec]) -> int:
    for a, b, spec in zip(left, right, specs, strict=True):
        result = compare_values(a, b, spec)
        if result:
            return result
    return 0


def order(keys: Sequence[Sequence], specs: Sequence[OrderKeySpec]) -> list[int]:
    """Stable ordering of positions by their key tuples."""
    return sorted(range(len(keys)), key=cmp_to_key(lambda a, b: compare_keys(keys[a], keys[b], specs)))


def window(
    call: WindowCallLayout,
    count: int,
    apply: Callable[[int, int], object],
    aggregate: Callable[[list], object],
) -> list:
    """Values of one window call for ``count`` row occurrences.

    ``apply(child, index)`` evaluates a call child for an occurrence, and
    ``aggregate(values)`` folds the frame's argument values.
    """
    partitions: dict[tuple, list[int]] = {}
    for index in range(count):
        key = tuple(apply(child, index) for child in call.partition_children)
        partitions.setdefault(key, []).append(index)
    keys = [tuple(apply(child, index) for child in call.order_children) for index in range(count)]
    values: list = [None] * count
    for members in partitions.values():
        ordered = [members[position] for position in order([keys[i] for i in members], call.order)]
        peer: list[int] = []
        starts: list[int] = []
        for position, index in enumerate(ordered):
            if not position or compare_keys(keys[ordered[position - 1]], keys[index], call.order):
                starts.append(position)
            peer.append(len(starts) - 1)
        for position, index in enumerate(ordered):
            kind = call.kind
            if kind is WindowFunctionKind.ROW_NUMBER:
                values[index] = position + 1
            elif kind is WindowFunctionKind.RANK:
                values[index] = starts[peer[position]] + 1
            elif kind is WindowFunctionKind.DENSE_RANK:
                values[index] = peer[position] + 1
            elif kind in (WindowFunctionKind.LAG, WindowFunctionKind.LEAD):
                arguments = [apply(child, index) for child in call.argument_children]
                offset = arguments[1] if len(arguments) > 1 else 1
                default = arguments[2] if len(arguments) > 2 else None
                target = None if offset is None else position + (offset if kind is WindowFunctionKind.LEAD else -offset)
                values[index] = (
                    apply(call.argument_children[0], ordered[target])
                    if target is not None and 0 <= target < len(ordered)
                    else default
                )
            else:
                frame = _frame(call, position, ordered, peer, keys)
                if call.filter_child is not None:
                    frame = [i for i in frame if apply(call.filter_child, i) is True]
                arguments = [apply(call.argument_children[0], i) if call.argument_children else 1 for i in frame]
                arguments = [value for value in arguments if value is not None]
                if call.distinct:
                    arguments = list(dict.fromkeys(arguments))
                values[index] = aggregate(arguments)
    return values


def _frame(call, position, ordered, peer, keys) -> list[int]:
    frame = call.frame

    def bound(candidate, boundary, start) -> bool:
        kind = boundary.kind
        if kind is WindowBoundaryKind.UNBOUNDED_PRECEDING:
            return start
        if kind is WindowBoundaryKind.UNBOUNDED_FOLLOWING:
            return not start
        offset = 0 if kind is WindowBoundaryKind.CURRENT_ROW else boundary.offset
        if kind is WindowBoundaryKind.OFFSET_PRECEDING:
            offset = -offset
        if frame.mode is WindowFrameMode.ROWS:
            target, actual = position + offset, candidate
        elif frame.mode is WindowFrameMode.GROUPS or kind is WindowBoundaryKind.CURRENT_ROW:
            target, actual = peer[position] + offset, peer[candidate]
        else:
            current, actual = keys[ordered[position]][0], keys[ordered[candidate]][0]
            if current is None or actual is None:
                return peer[candidate] >= peer[position] if start else peer[candidate] <= peer[position]
            if call.order[0].direction is Direction.DESC:
                target = current - offset
                return actual <= target if start else actual >= target
            target = current + offset
        return actual >= target if start else actual <= target

    selected = []
    for candidate, index in enumerate(ordered):
        if not (bound(candidate, frame.start, True) and bound(candidate, frame.end, False)):
            continue
        same_peer = peer[candidate] == peer[position]
        exclusion = frame.exclusion
        if exclusion is WindowFrameExclusion.CURRENT_ROW and candidate == position:
            continue
        if exclusion is WindowFrameExclusion.GROUP and same_peer:
            continue
        if exclusion is WindowFrameExclusion.TIES and same_peer and candidate != position:
            continue
        selected.append(index)
    return selected


__all__ = ["compare_keys", "compare_values", "order", "window"]
