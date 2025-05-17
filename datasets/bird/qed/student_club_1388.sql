CREATE TABLE IF NOT EXISTS "event" ("event_id" VARCHAR, "event_name" VARCHAR, "event_date" VARCHAR, "type" VARCHAR, "notes" VARCHAR, "location" VARCHAR, "status" VARCHAR, PRIMARY KEY ("event_id"));

CREATE TABLE IF NOT EXISTS "major" ("major_id" VARCHAR, "major_name" VARCHAR, "department" VARCHAR, "college" VARCHAR, PRIMARY KEY ("major_id"));

CREATE TABLE IF NOT EXISTS "zip_code" ("zip_code" INT, "type" VARCHAR, "city" VARCHAR, "county" VARCHAR, "state" VARCHAR, "short_state" VARCHAR, PRIMARY KEY ("zip_code"));

CREATE TABLE IF NOT EXISTS "attendance" ("link_to_event" VARCHAR, "link_to_member" VARCHAR);

CREATE TABLE IF NOT EXISTS "budget" ("budget_id" VARCHAR, "category" VARCHAR, "spent" FLOAT, "remaining" FLOAT, "amount" INT, "event_status" VARCHAR, "link_to_event" VARCHAR, PRIMARY KEY ("budget_id"));

CREATE TABLE IF NOT EXISTS "expense" ("expense_id" VARCHAR, "expense_description" VARCHAR, "expense_date" VARCHAR, "cost" FLOAT, "approved" VARCHAR, "link_to_member" VARCHAR, "link_to_budget" VARCHAR, PRIMARY KEY ("expense_id"));

CREATE TABLE IF NOT EXISTS "income" ("income_id" VARCHAR, "date_received" VARCHAR, "amount" INT, "source" VARCHAR, "notes" VARCHAR, "link_to_member" VARCHAR, PRIMARY KEY ("income_id"));

CREATE TABLE IF NOT EXISTS "member" ("member_id" VARCHAR, "first_name" VARCHAR, "last_name" VARCHAR, "email" VARCHAR, "position" VARCHAR, "t_shirt_size" VARCHAR, "phone" VARCHAR, "zip" INT, "link_to_major" VARCHAR, PRIMARY KEY ("member_id"));

SELECT T4.first_name, T4.last_name FROM event AS T1 INNER JOIN budget AS T2 ON T1.event_id = T2.link_to_event INNER JOIN expense AS T3 ON T2.budget_id = T3.link_to_budget INNER JOIN member AS T4 ON T3.link_to_member = T4.member_id WHERE T1.event_name = 'Yearly Kickoff';

SELECT T2.first_name, T2.last_name FROM budget AS T1 INNER JOIN member AS T2 ON T1.link_to_member = T2.member_id INNER JOIN event AS T3 ON T1.link_to_event = T3.event_id WHERE T3.event_name = 'Yearly Kickoff'