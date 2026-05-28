"""SMT solver module — re-export layer for backward compatibility.

All implementations live in submodules:
- ``smt_types``  — type system, option types, encoding
- ``smt_translate`` — SQL expression to Z3 translation
- ``smt_solver`` — SMTSolver class
"""

from .smt_types import (  # noqa: F401
    LogicalTypeRegistry,
    OptionTypeRegistry,
    SMTTypeInfo,
    SMTValue,
    SpecialFunctionModel,
    UnsupportedSMTError,
    _VarRef,
    _SPECIAL_FUNCTION_MODELS,
    _date_to_epoch_day,
    _datetime_to_epoch_second,
    _from_epoch_day,
    _from_epoch_second,
    _from_seconds,
    _infer_temporal_dtype,
    _is_temporal_string,
    encode_literal,
    infer,
    is_option_expr,
    make_option_type,
    normalize_dtype,
    option_of,
    register_special_function,
    unwrap_option,
)
from .smt_translate import (  # noqa: F401
    _bool_value,
    _coerce_pair,
    _coerce_numeric_sort,
    _null_value,
    _return_int,
    _return_same_type,
    _return_text,
    _to_z3_sort,
    _to_z3val,
    _translate_abs,
    _translate_instr,
    _translate_length,
    _translate_strftime,
    _translate_substr,
    _value_null,
    _value_payload,
    _value_some,
    _ymd_hms_from_temporal,
    _zfill2,
    declare_column,
    like_to_z3,
)
from .smt_solver import SMTSolver, checkpoint  # noqa: F401
