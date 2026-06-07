from datetime import date, datetime, time

from parseval.dtype import (
    DataType,
    TypeFamily,
    TypeProfile,
    TypeService,
    infer_type_from_value,
    parse_date,
    parse_datetime,
    parse_time,
    type_family,
)


def test_type_family_maps_core_datatypes():
    assert type_family(DataType.build("INT")) == TypeFamily.INTEGER
    assert type_family(DataType.build("REAL")) == TypeFamily.DECIMAL
    assert type_family(DataType.build("TEXT")) == TypeFamily.TEXT
    assert type_family(DataType.build("BOOLEAN")) == TypeFamily.BOOLEAN
    assert type_family(DataType.build("DATE")) == TypeFamily.DATE
    assert type_family(DataType.build("TIME")) == TypeFamily.TIME
    assert type_family(DataType.build("TIMESTAMP")) == TypeFamily.DATETIME


def test_type_service_profiles_live_in_dtype_module():
    profile = TypeService().profile_datatype(DataType.build("INT"))

    assert isinstance(profile, TypeProfile)
    assert profile.family == TypeFamily.INTEGER


def test_parse_temporal_values():
    assert parse_date("2024-01-02") == date(2024, 1, 2)
    assert parse_time("03:04:05") == time(3, 4, 5)
    assert parse_datetime("2024-01-02 03:04:05") == datetime(2024, 1, 2, 3, 4, 5)


def test_infer_type_from_value():
    assert infer_type_from_value(True).is_type(DataType.Type.BOOLEAN)
    assert infer_type_from_value(1).is_type(*DataType.INTEGER_TYPES)
    assert infer_type_from_value(1.5).is_type(*DataType.REAL_TYPES)
    assert infer_type_from_value("x").is_type(*DataType.TEXT_TYPES)
    assert infer_type_from_value(date(2024, 1, 2)).is_type(DataType.Type.DATE)
    assert infer_type_from_value(datetime(2024, 1, 2, 3, 4, 5)).is_type(DataType.Type.DATETIME)
    assert infer_type_from_value(time(3, 4, 5)).is_type(DataType.Type.TIME)
