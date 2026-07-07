from types import SimpleNamespace

from parseval.disprover import Disprover
from parseval.states import ExecutionResult, Verdict


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
