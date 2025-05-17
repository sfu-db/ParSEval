CREATE TABLE IF NOT EXISTS "event" ("event_id" VARCHAR, "event_name" VARCHAR, "event_date" VARCHAR, "type" VARCHAR, "notes" VARCHAR, "location" VARCHAR, "status" VARCHAR, PRIMARY KEY ("event_id"));

CREATE TABLE IF NOT EXISTS "major" ("major_id" VARCHAR, "major_name" VARCHAR, "department" VARCHAR, "college" VARCHAR, PRIMARY KEY ("major_id"));

CREATE TABLE IF NOT EXISTS "zip_code" ("zip_code" INT, "type" VARCHAR, "city" VARCHAR, "county" VARCHAR, "state" VARCHAR, "short_state" VARCHAR, PRIMARY KEY ("zip_code"));

CREATE TABLE IF NOT EXISTS "attendance" ("link_to_event" VARCHAR, "link_to_member" VARCHAR);

CREATE TABLE IF NOT EXISTS "budget" ("budget_id" VARCHAR, "category" VARCHAR, "spent" FLOAT, "remaining" FLOAT, "amount" INT, "event_status" VARCHAR, "link_to_event" VARCHAR, PRIMARY KEY ("budget_id"));

CREATE TABLE IF NOT EXISTS "expense" ("expense_id" VARCHAR, "expense_description" VARCHAR, "expense_date" VARCHAR, "cost" FLOAT, "approved" VARCHAR, "link_to_member" VARCHAR, "link_to_budget" VARCHAR, PRIMARY KEY ("expense_id"));

CREATE TABLE IF NOT EXISTS "income" ("income_id" VARCHAR, "date_received" VARCHAR, "amount" INT, "source" VARCHAR, "notes" VARCHAR, "link_to_member" VARCHAR, PRIMARY KEY ("income_id"));

CREATE TABLE IF NOT EXISTS "member" ("member_id" VARCHAR, "first_name" VARCHAR, "last_name" VARCHAR, "email" VARCHAR, "position" VARCHAR, "t_shirt_size" VARCHAR, "phone" VARCHAR, "zip" INT, "link_to_major" VARCHAR, PRIMARY KEY ("member_id"));

SELECT AVG(T2.cost) FROM member AS T1 INNER JOIN expense AS T2 ON T1.member_id = T2.link_to_member WHERE T1.last_name = 'Allen' AND T1.first_name = 'Elijah' AND (SUBSTR(T2.expense_date, 6, 2) = '09' OR SUBSTR(T2.expense_date, 6, 2) = '10');

SELECT AVG(cost) FROM expense WHERE link_to_member = (SELECT member_id FROM member WHERE first_name = 'Elijah' AND last_name = 'Allen') AND (DATE_FORMAT(CAST(expense_date AS DATETIME), '%m') = '09' OR DATE_FORMAT(CAST(expense_date AS DATETIME), '%m') = '10')