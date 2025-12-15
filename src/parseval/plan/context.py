from __future__ import annotations
from collections import UserDict
from contextlib import contextmanager
from typing import Any, Dict, Tuple
from .rex import Expression


class Context(UserDict):
    """
    Cache for evaluation results to avoid duplicate computation.
    Uses (expression, context) as key.
    """

    DATETIME_FMT = ["%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d", "%Y-%m"]
    EXPR_CACHE = "_expr_cache"
    DEFAULT_LIST_KEYS = ["sql_conditions", "smt_conditions"]

    def __call__(self, is_branch: bool = False):
        """
        Allow: with ctx(is_branch) as track:
        """
        return self.predicate_scope(is_branch)

    def __getitem__(self, key):
        if key in self.DEFAULT_LIST_KEYS and key not in self.data:
            self.data[key] = []
        return super().__getitem__(key)

    def in_predicates(self):
        return bool(self.data.get("_predicate_stack", []))

    @contextmanager
    def predicate_scope(self, is_branch: bool):
        """Context manager to track predicates.  If `is_branch` is False, tracking is a no-op."""
        if is_branch:
            self.data.setdefault("_predicate_stack", []).append(True)
            try:

                def track(expr, smt_expr):
                    self.data.setdefault("sql_conditions", []).append(expr)
                    self.data.setdefault("smt_conditions", []).append(smt_expr)
                    return smt_expr

                yield track
            finally:
                self.data["_predicate_stack"].pop()
        else:

            def track(expr, smt_expr):
                return smt_expr

            yield track

    # def get(self, key: Tuple[Expression, Any]) -> Any:
    #     self._hit_count += 1
    #     return self._cache[key]

    # def put(self, key: Tuple[Expression, Any], value: Any):
    #     if key not in self._cache:
    #         self._miss_count += 1
    #     self._cache[key] = value

    # def __contains__(self, key: Tuple[Expression, Any]) -> bool:
    #     return key in self._cache

    # def clear(self):
    #     self._cache.clear()
    #     self._hit_count = 0
    #     self._miss_count = 0

    # def stats(self) -> Dict[str, int]:
    #     return {
    #         "cache_size": len(self._cache),
    #         "hits": self._hit_count,
    #         "misses": self._miss_count,
    #         "hit_rate": (
    #             self._hit_count / (self._hit_count + self._miss_count)
    #             if (self._hit_count + self._miss_count) > 0
    #             else 0
    #         ),
    #     }
