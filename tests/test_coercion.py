from __future__ import annotations

from datetime import date, datetime

import pytest

from parseval.coercion import CoercionError, coerce_literal_value, coerce_value
from parseval.dtype import DataType


def test_strict_coerce_value_uses_type_adapters():
    assert coerce_value("42", DataType.build("INT")) == 42


def test_literal_coercion_rejects_unparseable_numeric():
    with pytest.raises(CoercionError):
        coerce_literal_value("not-a-number", DataType.build("INT"))


def test_literal_coercion_parses_temporal_strings():
    value = coerce_literal_value("2024-06-07", DataType.build("DATE"))

    assert value == date(2024, 6, 7)


def test_literal_coercion_normalizes_iso_datetime_offsets_to_naive_utc():
    value = coerce_literal_value("2024-06-07T12:30:00+02:00", DataType.build("DATETIME"))

    assert value == datetime(2024, 6, 7, 10, 30, 0)


def test_literal_coercion_text_from_numeric():
    assert coerce_literal_value(613360, DataType.build("TEXT")) == "613360"
