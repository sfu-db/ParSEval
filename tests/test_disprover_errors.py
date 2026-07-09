from types import SimpleNamespace

from parseval.disprover import Disprover
from parseval.states import DisproveResult, ExecutionResult, GenerationResult, SyntaxException, Verdict


def _disprover():
    return Disprover(
        "SELECT id FROM t",
        "SELECT id FROM t WHERE id > 0",
        "CREATE TABLE t (id INT)",
        dialect="sqlite",
        connection_string="sqlite:///:memory:",
    )


def test_generation_failure_before_instance_assignment_returns_original_error(
    monkeypatch,
):
    monkeypatch.setattr(
        "parseval.disprover.Instance",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("bad ddl")),
    )

    result = _disprover()._try_generate_and_compare("SELECT id FROM t", 0.0)

    assert result.verdict in {Verdict.UNKNOWN, Verdict.SYNTAX_ERROR}
    assert "bad ddl" in result.error_msg
    assert "instance" not in result.error_msg.lower()


def test_generation_syntax_exception_returns_syntax_error(monkeypatch):
    monkeypatch.setattr(
        "parseval.disprover.SymbolicEngine",
        lambda *args, **kwargs: SimpleNamespace(
            generate=lambda **_kwargs: (_ for _ in ()).throw(
                SyntaxException("Error tokenizing query")
            )
        ),
    )

    result = _disprover()._try_generate_and_compare("SELECT id FROM t", 0.0)

    assert result.verdict == Verdict.SYNTAX_ERROR
    assert result.error_msg == "Error tokenizing query"
    assert result.generation.error_msg == "Error tokenizing query"


def test_generation_unresolved_column_value_error_returns_syntax_error(monkeypatch):
    monkeypatch.setattr(
        "parseval.disprover.SymbolicEngine",
        lambda *args, **kwargs: SimpleNamespace(
            generate=lambda **_kwargs: (_ for _ in ()).throw(
                ValueError('Unresolved column: "t1"."missing"')
            )
        ),
    )

    result = _disprover()._try_generate_and_compare("SELECT id FROM t", 0.0)

    assert result.verdict == Verdict.SYNTAX_ERROR
    assert result.error_msg == 'Unresolved column: "t1"."missing"'


def test_pick_best_prefers_syntax_error_over_unknown():
    unknown = DisproveResult(
        verdict=Verdict.UNKNOWN,
        semantics="bag",
        q1_result=ExecutionResult(query="SELECT id FROM t"),
        q2_result=ExecutionResult(query="SELECT id FROM t WHERE id > 0"),
        generation=GenerationResult(success=False),
    )
    syntax_error = DisproveResult(
        verdict=Verdict.SYNTAX_ERROR,
        semantics="bag",
        q1_result=ExecutionResult(query="SELECT id FROM t"),
        q2_result=ExecutionResult(query="SELECT id FROM t WHERE id > 0"),
        generation=GenerationResult(success=False),
        error_msg="bad query",
    )

    result = Disprover._pick_best(unknown, syntax_error)

    assert result is syntax_error


def test_empty_results_remain_unknown(monkeypatch):
    monkeypatch.setattr(
        "parseval.disprover.SymbolicEngine",
        lambda *args, **kwargs: SimpleNamespace(
            generate=lambda **_kwargs: SimpleNamespace(
                rows_generated=0,
                coverage=0.0,
            )
        ),
    )
    monkeypatch.setattr("parseval.disprover.to_db", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        "parseval.disprover.execute_query",
        lambda query, *_args: ExecutionResult(query=query, rows=[]),
    )

    result = _disprover()._try_generate_and_compare("SELECT id FROM t", 0.0)

    assert result.verdict == Verdict.UNKNOWN
    assert result.error_msg == "Both queries returned empty results - cannot prove equivalence"


def test_generation_result_keeps_public_generation_schema_small():
    generation = GenerationResult(
        success=True,
        rows_generated=2,
        coverage=0.5,
    )

    assert generation.to_dict() == {
        "success": True,
        "rows_generated": 2,
        "coverage": 0.5,
        "error_msg": "",
        "elapsed_time": 0.0,
        "generation_coverage": 0.5,
    }


def test_syntax_error_execution_message_is_copied_to_top_level(
    monkeypatch,
):
    monkeypatch.setattr(
        "parseval.disprover.SymbolicEngine",
        lambda *args, **kwargs: SimpleNamespace(
            generate=lambda **_kwargs: SimpleNamespace(
                rows_generated=1,
                coverage=1.0,
            )
        ),
    )
    monkeypatch.setattr("parseval.disprover.to_db", lambda *args, **kwargs: None)

    q1 = ExecutionResult(
        query="SELECT id FROM t",
        error_msg="near FROM: syntax error",
    )
    q2 = ExecutionResult(query="SELECT id FROM t WHERE id > 0", rows=[])
    monkeypatch.setattr(
        "parseval.disprover.execute_query",
        lambda query, *_args: q1 if "WHERE" not in query else q2,
    )

    result = _disprover()._try_generate_and_compare("SELECT id FROM t", 0.0)

    assert result.verdict == Verdict.SYNTAX_ERROR
    assert result.error_msg == "near FROM: syntax error"


def test_runtime_execution_error_remains_unknown_not_syntax_error(monkeypatch):
    monkeypatch.setattr(
        "parseval.disprover.SymbolicEngine",
        lambda *args, **kwargs: SimpleNamespace(
            generate=lambda **_kwargs: SimpleNamespace(
                rows_generated=2,
                coverage=1.0,
            )
        ),
    )
    monkeypatch.setattr("parseval.disprover.to_db", lambda *args, **kwargs: None)

    q1 = ExecutionResult(
        query="SELECT id FROM t",
        error_msg="Subquery returns more than 1 row",
    )
    q2 = ExecutionResult(query="SELECT id FROM t WHERE id > 0", rows=[])
    monkeypatch.setattr(
        "parseval.disprover.execute_query",
        lambda query, *_args: q1 if "WHERE" not in query else q2,
    )

    result = _disprover()._try_generate_and_compare("SELECT id FROM t", 0.0)

    assert result.verdict == Verdict.UNKNOWN
    assert result.error_msg == "Subquery returns more than 1 row"
