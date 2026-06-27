from __future__ import annotations

from datetime import date, datetime

from parseval.coercion import coerce_literal_value, coerce_value
from parseval.dtype import DataType


def test_strict_coerce_value_uses_type_adapters():
    assert coerce_value("42", DataType.build("INT")) == 42


def test_literal_coercion_is_best_effort_for_solver_predicates():
    value = coerce_literal_value("not-a-number", DataType.build("INT"))

    assert value == "not-a-number"


def test_literal_coercion_parses_temporal_strings():
    value = coerce_literal_value("2024-06-07", DataType.build("DATE"))

    assert value == date(2024, 6, 7)


def test_literal_coercion_normalizes_iso_datetime_offsets_to_naive_utc():
    value = coerce_literal_value("2024-06-07T12:30:00+02:00", DataType.build("DATETIME"))

    assert value == datetime(2024, 6, 7, 10, 30, 0)
