"""Budgets and growth limits for concolic database generation."""

from __future__ import annotations

from dataclasses import dataclass

from parseval.instance.domain import Provider, sequential


@dataclass(frozen=True, slots=True)
class GenerationConfig:
    """Generation settings.

    ``timeout_ms`` bounds one solver call; ``time_limit_s`` bounds the whole
    generation: when it passes, execution stops and the latest accepted
    database is returned (``None`` runs until done). Generated non-NULL
    strings have at least ``min_string_length`` characters. ``speculate`` samples rows from the
    query before solving (``parseval.speculate``), reproducibly for a
    ``seed``; without it generation starts from an empty database.
    ``provider`` supplies the values of cells no constraint decides, in
    speculated rows and candidate rows alike.

    Each uncovered outcome is solved at most once per database version, and
    a version only follows a solve that covers a new outcome, so generation
    terminates without row or attempt limits.
    """

    timeout_ms: int = 5_000
    time_limit_s: float | None = None
    min_string_length: int = 1
    speculate: bool = True
    seed: int = 0
    provider: Provider = sequential


__all__ = ["GenerationConfig"]
