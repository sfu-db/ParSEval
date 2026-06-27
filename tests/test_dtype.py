from datetime import date, datetime, time

from parseval.dtype import (
    DataType,
    TypeFamily,
    TypeProfile,
    TypeService,
    infer_type_from_value,
    infer_semantic_datatype_from_literal,
    merge_semantic_datatypes,
    parse_date,
    parse_datetime,
    parse_time,
    semantic_cast_datatype,
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


def test_infer_semantic_datatype_from_literal():
    assert infer_semantic_datatype_from_literal("50").is_type(*DataType.INTEGER_TYPES)
    assert infer_semantic_datatype_from_literal("50.5").is_type(*DataType.REAL_TYPES)
    assert infer_semantic_datatype_from_literal("2024-01-02").is_type(DataType.Type.DATE)
    assert infer_semantic_datatype_from_literal("2024-01-02 03:04:05").is_type(
        DataType.Type.DATETIME
    )
    assert infer_semantic_datatype_from_literal("abc") is None


def test_merge_semantic_datatypes_preserves_compatible_widening():
    assert merge_semantic_datatypes((DataType.build("INT"), DataType.build("BIGINT"))).is_type(
        *DataType.INTEGER_TYPES
    )
    assert merge_semantic_datatypes((DataType.build("INT"), DataType.build("REAL"))).is_type(
        *DataType.REAL_TYPES
    )
    assert merge_semantic_datatypes((DataType.build("DATE"), DataType.build("DATETIME"))).is_type(
        DataType.Type.DATETIME
    )
    assert merge_semantic_datatypes((DataType.build("INT"), DataType.build("DATE"))) is None


def test_semantic_cast_datatype_uses_type_family_normalization():
    assert semantic_cast_datatype(DataType.build("BIGINT")).is_type(*DataType.INTEGER_TYPES)
    assert semantic_cast_datatype(DataType.build("DECIMAL(10,2)")).is_type(*DataType.REAL_TYPES)
    assert semantic_cast_datatype(DataType.build("DATE")).is_type(DataType.Type.DATE)
    assert semantic_cast_datatype(DataType.build("TIMESTAMP")).is_type(DataType.Type.DATETIME)
    assert semantic_cast_datatype(DataType.build("TEXT")) is None
