from parseval.main import instantiate_db


def test_reused_aliases_in_scalar_subquery_keep_inner_plan_identity(tmp_path):
    schema = """
    CREATE TABLE event (event_id TEXT PRIMARY KEY);
    CREATE TABLE budget (
      budget_id TEXT PRIMARY KEY,
      link_to_event TEXT,
      FOREIGN KEY (link_to_event) REFERENCES event(event_id)
    );
    CREATE TABLE member (
      member_id TEXT PRIMARY KEY,
      first_name TEXT,
      last_name TEXT,
      phone TEXT
    );
    CREATE TABLE expense (
      expense_id TEXT PRIMARY KEY,
      cost REAL,
      link_to_member TEXT,
      link_to_budget TEXT,
      FOREIGN KEY (link_to_member) REFERENCES member(member_id),
      FOREIGN KEY (link_to_budget) REFERENCES budget(budget_id)
    );
    """
    sql = """
    SELECT DISTINCT T3.first_name, T3.last_name, T3.phone
    FROM expense AS T1
    INNER JOIN budget AS T2 ON T1.link_to_budget = T2.budget_id
    INNER JOIN member AS T3 ON T3.member_id = T1.link_to_member
    WHERE T1.cost > (
      SELECT AVG(T1.cost)
      FROM expense AS T1
      INNER JOIN budget AS T2 ON T1.link_to_budget = T2.budget_id
      INNER JOIN member AS T3 ON T3.member_id = T1.link_to_member
    )
    """

    result = instantiate_db(
        sql,
        schema,
        f"sqlite:///{tmp_path / 'student_club_1457.sqlite'}",
        "sqlite",
        max_iterations=10,
        atom_null=0,
        atom_dup=1,
    )

    assert result.success, result.error_msg
