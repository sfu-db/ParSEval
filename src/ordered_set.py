from __future__ import annotations

from collections.abc import Iterable, Iterator, MutableSet
from typing import Generic, TypeVar


T = TypeVar("T")


class OrderedSet(MutableSet[T], Generic[T]):
    def __init__(self, iterable: Iterable[T] | None = None):
        self._items: dict[T, None] = {}
        if iterable is not None:
            self.update(iterable)

    def __contains__(self, value: object) -> bool:
        return value in self._items

    def __iter__(self) -> Iterator[T]:
        return iter(self._items.keys())

    def __reversed__(self) -> Iterator[T]:
        return reversed(tuple(self._items.keys()))

    def __len__(self) -> int:
        return len(self._items)

    def add(self, value: T) -> None:
        self._items[value] = None

    def discard(self, value: T) -> None:
        self._items.pop(value, None)

    def pop(self, last: bool = True) -> T:
        if not self._items:
            raise KeyError("set is empty")
        key = next(reversed(self._items)) if last else next(iter(self._items))
        self._items.pop(key)
        return key

    def update(self, *iterables: Iterable[T]) -> None:
        for iterable in iterables:
            for value in iterable:
                self.add(value)

    def issubset(self, other: Iterable[T]) -> bool:
        other_values = set(other)
        return all(value in other_values for value in self)

    def union(self, *others: Iterable[T]) -> "OrderedSet[T]":
        result = OrderedSet(self)
        for other in others:
            result.update(other)
        return result

    def intersection(self, *others: Iterable[T]) -> "OrderedSet[T]":
        other_sets = [set(other) for other in others]
        return OrderedSet(
            value for value in self if all(value in other_set for other_set in other_sets)
        )

    def difference(self, *others: Iterable[T]) -> "OrderedSet[T]":
        excluded = set()
        for other in others:
            excluded.update(other)
        return OrderedSet(value for value in self if value not in excluded)

    def __repr__(self) -> str:
        return f"OrderedSet({list(self._items)})"
