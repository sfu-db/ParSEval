from __future__ import annotations
from typing import Callable, List, TypeVar, Dict, TYPE_CHECKING
import re

if TYPE_CHECKING:
    from .symbol import Symbol


def normalize_name(name) -> str:
    pattern = r"[^a-zA-Z0-9_]"
    cleaned_str = re.sub(pattern, "", name)
    return cleaned_str.lower()


def like_to_pattern(pattern: str) -> re.Pattern:
    """
    Convert SQL LIKE pattern to regex pattern.
    """
    regex = ""
    for ch in pattern:
        if ch == "%":
            regex += ".*"
        elif ch == "_":
            regex += "."
        else:
            regex += re.escape(ch)
    return re.compile(f"^{regex}$")


def group_by_concrete(
    items: List[Symbol],
    key_func: Callable = lambda x: x.concrete,
    ignore_null: bool = True,
) -> Dict:
    groups = {}
    for item in items:
        key = key_func(item)
        if ignore_null and key is None:
            continue
        groups.setdefault(key, []).append(item)
    return groups


def sort_by_concrete(
    items: List[Symbol],
    key_func: Callable = lambda x: x.concrete,
    reverse: bool = False,
    null_first: bool = False,
) -> List[Symbol]:
    null_values = [item for item in items if item.concrete is None]
    values = sorted(
        [item for item in items if item.concrete is not None],
        key=key_func,
        reverse=reverse,
    )
    return null_values + values if null_first else values + null_values
