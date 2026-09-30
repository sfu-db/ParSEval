"""Deterministic work queue for discovered coverage targets."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass

from parseval.coverage import CoverageTarget
from parseval.instance import Instance


@dataclass(frozen=True, slots=True)
class GenerationTask:
    target: CoverageTarget
    seed: Instance


class CoverageFrontier:
    """FIFO frontier keyed by stable target identity.

    Rediscovery updates the concrete seed without changing queue order.  A
    nearby seed generally retains useful unrelated cells when the SMT solver
    moves to the neighboring path.
    """

    def __init__(self) -> None:
        self._tasks: OrderedDict[str, GenerationTask] = OrderedDict()

    def add(self, target: CoverageTarget, seed: Instance) -> None:
        self._tasks[target.id] = GenerationTask(target, seed)

    def discard(self, identity: str) -> None:
        self._tasks.pop(identity, None)

    def pop(self) -> GenerationTask:
        _, task = self._tasks.popitem(last=False)
        return task

    def __bool__(self) -> bool:
        return bool(self._tasks)

    def __len__(self) -> int:
        return len(self._tasks)


__all__ = ["CoverageFrontier", "GenerationTask"]
