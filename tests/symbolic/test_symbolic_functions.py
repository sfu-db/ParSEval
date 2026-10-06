"""Callbacks execute identically at observation time and under new assignments."""

from typing import ClassVar

import pytest

from parseval.errors import IRValidationError
from parseval.symbolic import Runtime, Semantics, strict
from parseval.terms import terms as nodes
from parseval.terms.context import ScalarFunctionSpec
from parseval.terms.names import FunctionId
from parseval.terms.sorts import INTEGER, STRING, ScalarSort, ScalarType, TypeKind


class CustomRuntime(Runtime):
    SCALAR_FUNCTIONS: ClassVar = {
        **Runtime.SCALAR_FUNCTIONS,
        "twice": strict(lambda args: args[0] * 2),
        "join_words": strict(lambda args: " ".join(args)),
        "null_label": lambda args: "(null)" if args[0] is None else str(args[0]),
        "answer": lambda args: 42,
    }


def test_custom_callback_retains_typed_call_and_input_dependencies():
    runtime = CustomRuntime()
    x = runtime.input("x", 4)
    value = runtime.call("twice", x, result=ScalarSort(INTEGER)) + 1
    assert value.concrete == value.evaluate() == 9
    assert value.evaluate({"x": 10}) == 21
    assert value.expression.inputs == x.expression.inputs
    call = runtime.arena[runtime.arena[value.expression.root].children[0]]
    assert isinstance(call, nodes.ScalarCall)
    assert runtime.arena.context.function(call.payload.function).operator == "twice"
    assert "twice" not in Runtime.SCALAR_FUNCTIONS


def test_variadic_and_zero_argument_callbacks():
    runtime = CustomRuntime()
    text = runtime.input("text", "world")
    value = runtime.call("join_words", "hello", text, "!", result=ScalarSort(STRING))
    assert value.concrete == "hello world !"
    assert value.evaluate({"text": "there"}) == "hello there !"
    assert runtime.call("answer", result=ScalarSort(INTEGER)).evaluate() == 42


def test_strict_and_non_strict_null_handling():
    runtime = CustomRuntime()
    x = runtime.input("x", None, ScalarSort(INTEGER, True))
    twice = runtime.call("twice", x, result=ScalarSort(INTEGER, True))
    label = runtime.call("null_label", x, result=ScalarSort(STRING))
    assert twice.concrete is None and twice.evaluate() is None
    assert twice.evaluate({"x": 3}) == 6
    assert label.concrete == label.evaluate() == "(null)"
    assert label.evaluate({"x": 3}) == "3"


def test_function_id_overrides_operator_name_and_checks_signature():
    runtime = CustomRuntime()
    identity = runtime.arena.context.allocate_id(FunctionId)
    runtime.arena.context.register_function(
        identity,
        ScalarFunctionSpec(
            (ScalarSort(INTEGER),),
            ScalarSort(INTEGER),
            operator="twice",
        ),
    )

    class IdentifiedRuntime(CustomRuntime):
        SCALAR_FUNCTIONS: ClassVar = {
            **CustomRuntime.SCALAR_FUNCTIONS,
            identity: lambda args: args[0] * 3,
        }

    runtime = IdentifiedRuntime(runtime.arena)
    x = runtime.input("x", 5)
    value = runtime.call(identity, x)
    assert value.concrete == value.evaluate() == 15
    assert value.evaluate({"x": 7}) == 21
    with pytest.raises(IRValidationError):
        runtime.call(identity, "wrong type")
    with pytest.raises(IRValidationError):
        runtime.call(identity)
    with pytest.raises(TypeError, match="registered result"):
        runtime.call(identity, x, result=ScalarSort(INTEGER))


def test_existing_terms_call_registered_callbacks():
    runtime = CustomRuntime()
    x = runtime.input("x", 7)
    term = runtime.builder.finish(
        runtime.builder.apply(
            "twice",
            (x.expression.root,),
            ScalarSort(INTEGER),
        )
    )
    assert runtime.observe(term).concrete == 14
    assert runtime.evaluate(term, {"x": 9}) == 18


def test_builtin_callback_override_affects_methods_and_replay():
    class AsciiRuntime(Runtime):
        SCALAR_FUNCTIONS: ClassVar = {
            **Runtime.SCALAR_FUNCTIONS,
            "lower": strict(
                lambda args: "".join(
                    chr(ord(char) + 32) if "A" <= char <= "Z" else char
                    for char in args[0]
                )
            ),
        }

    runtime = AsciiRuntime()
    text = runtime.input("text", "ÄABC")
    assert text.lower().concrete == text.lower().evaluate() == "Äabc"
    assert text.lower().evaluate({"text": "ÖXYZ"}) == "Öxyz"
    assert Runtime().literal("ÄABC").lower().evaluate() == "äabc"


def test_custom_result_type_and_nullability_checked_immediately_and_on_replay():
    class InvalidRuntime(Runtime):
        SCALAR_FUNCTIONS: ClassVar = {
            **Runtime.SCALAR_FUNCTIONS,
            "sometimes_int": lambda args: args[0] if args[0] > 0 else "invalid",
            "sometimes_null": lambda args: args[0] if args[0] > 0 else None,
        }

    runtime = InvalidRuntime()
    x = runtime.input("x", 1)
    value = runtime.call("sometimes_int", x, result=ScalarSort(INTEGER))
    with pytest.raises(TypeError):
        value.evaluate({"x": -1})
    with pytest.raises(TypeError):
        runtime.call("sometimes_int", -1, result=ScalarSort(INTEGER))
    value = runtime.call("sometimes_null", x, result=ScalarSort(INTEGER))
    with pytest.raises(ValueError):
        value.evaluate({"x": -1})
    with pytest.raises(ValueError):
        runtime.call("sometimes_null", -1, result=ScalarSort(INTEGER))


def test_callback_exceptions_propagate():
    class FailingRuntime(Runtime):
        SCALAR_FUNCTIONS: ClassVar = {
            **Runtime.SCALAR_FUNCTIONS,
            "reciprocal": lambda args: 1.0 / args[0],
        }

    from parseval.terms.sorts import FLOAT

    runtime = FailingRuntime()
    x = runtime.input("x", 2)
    value = runtime.call("reciprocal", x, result=ScalarSort(FLOAT))
    with pytest.raises(ZeroDivisionError):
        value.evaluate({"x": 0})
    with pytest.raises(ZeroDivisionError):
        runtime.call("reciprocal", 0, result=ScalarSort(FLOAT))


def test_named_functions_need_an_explicit_result_sort():
    runtime = CustomRuntime()
    with pytest.raises(TypeError, match="explicit result ScalarSort"):
        runtime.call("twice", 2)
    with pytest.raises(NotImplementedError):
        runtime.call("unregistered", 2, result=ScalarSort(INTEGER))
    with pytest.raises(NotImplementedError):
        runtime.call(
            "unregistered",
            runtime.literal(None, ScalarSort(INTEGER, True)),
            result=ScalarSort(INTEGER, True),
        )


def test_shared_scalar_call_executes_once_per_reevaluation():
    invocations = []

    def observe(arguments):
        invocations.append(arguments[0])
        return arguments[0] * 2

    class CountingRuntime(Runtime):
        SCALAR_FUNCTIONS: ClassVar = {**Runtime.SCALAR_FUNCTIONS, "observe": observe}

    runtime = CountingRuntime()
    x = runtime.input("x", 3)
    shared = runtime.call("observe", x, result=ScalarSort(INTEGER))
    result = shared * shared + shared
    assert result.concrete == 42
    assert invocations == [3]
    invocations.clear()
    assert result.evaluate({"x": 5}) == 110
    assert invocations == [5]
