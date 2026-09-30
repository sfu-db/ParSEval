"""A shared deadline and work limit for encoding, solving, and materialization."""

from dataclasses import dataclass
from time import monotonic


class BudgetExceeded(RuntimeError):
    pass


@dataclass
class Budget:
    deadline: float | None = None
    max_steps: int = 100_000
    steps: int = 0

    def tick(self, amount: int = 1) -> None:
        self.steps += amount
        if self.steps > self.max_steps:
            raise BudgetExceeded("encoding work limit exceeded")
        if self.deadline is not None and monotonic() >= self.deadline:
            raise BudgetExceeded("end-to-end deadline exceeded")

    def remaining_ms(self) -> int | None:
        self.tick(0)
        return None if self.deadline is None else max(1, int((self.deadline - monotonic()) * 1000))
