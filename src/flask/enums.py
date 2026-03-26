"""
enums.py — shared enum definitions for the QueryLens equivalence checking system.

Design notes
------------
DBLevel and QueryLevel form two independent orderings.  Their Cartesian product
(DBLevel × QueryLevel) defines the 8 EquivalenceLevel combinations used as
labels in EvalRecord.  Storing them as separate columns (db_level, query_level)
rather than a single string makes filtering and aggregation efficient.

State machine for async jobs:
  PENDING → RUNNING → DONE
                   ↘ ERROR
"""

import enum


class DBLevel(str, enum.Enum):
    """
    How many database integrity constraints are enforced when checking equivalence.
    Ordered from least to most restrictive.
    """

    NONE = "NONE"  # no constraints — any database instance
    PK_FK = "PK_FK"  # PK + foreign key referential integrity
    PK_FK_NULL = "PK_FK_NULL"  # PK + FK + NOT NULL
    FULL = "FULL"  # all schema constraints (CHECK, UNIQUE, etc.)

    @classmethod
    def ordered(cls) -> list["DBLevel"]:
        return [cls.NONE, cls.PK_FK, cls.PK_FK_NULL, cls.FULL]


class QueryLevel(str, enum.Enum):
    """
    How strictly SQL query semantics are interpreted when checking equivalence.
    Ordered from least to most restrictive.
    """

    SET = "SET"  # + set semantics (UNION, INTERSECT, EXCEPT)
    BAG = "BAG"  # + bag/multiset semantics (UNION ALL)

    @classmethod
    def ordered(cls) -> list["QueryLevel"]:
        return [cls.SET, cls.BAG]


class MetricType(str, enum.Enum):
    """Scalar accuracy metrics stored per ModelRun."""

    EXEC_ACC = "EXEC_ACC"  # execution accuracy
    EXACT_MATCH = "EXACT_MATCH"  # exact string match


class RunStatus(str, enum.Enum):
    """Lifecycle state for async evaluation jobs."""

    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    ERROR = "error"


class CounterExampleState(str, enum.Enum):
    """
    State of a single counterexample search for one (question, equivalence_level) pair.
    WITNESSED  — a concrete witness database was found (queries disagree on it)
    EQUIVALENT — no counterexample found; queries appear equivalent at this level
    ERROR      — search failed (e.g. SQL parse error, timeout)
    SKIPPED    — level was not checked (e.g. superseded by a coarser level)
    """

    PENDING = "pending"
    RUNNING = "running"
    WITNESSED = "witnessed"
    EQUIVALENT = "equivalent"
    ERROR = "error"
    SKIPPED = "skipped"


def make_equivalence_level(db: DBLevel, query: QueryLevel) -> str:
    """Canonical string key for a (db_level, query_level) pair."""
    return f"{db.value}_{query.value}"
