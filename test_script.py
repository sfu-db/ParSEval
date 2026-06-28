from parseval import disprove

result = disprove(
    sql1="SELECT name FROM users WHERE age > 25",
    sql2="SELECT name FROM users WHERE age > 26",
    schema="CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT, age INTEGER)",
    connection_string="sqlite:///./tmp/test.db",
    dialect="sqlite",
    semantics="bag",  # or "set"
)
print(result.verdict)  # Verdict.EQ or Verdict.NEQ