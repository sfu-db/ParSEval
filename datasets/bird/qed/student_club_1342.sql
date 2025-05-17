CREATE TABLE IF NOT EXISTS "event" ("event_id" VARCHAR, "event_name" VARCHAR, "event_date" VARCHAR, "type" VARCHAR, "notes" VARCHAR, "location" VARCHAR, "status" VARCHAR, PRIMARY KEY ("event_id"));

CREATE TABLE IF NOT EXISTS "major" ("major_id" VARCHAR, "major_name" VARCHAR, "department" VARCHAR, "college" VARCHAR, PRIMARY KEY ("major_id"));

CREATE TABLE IF NOT EXISTS "zip_code" ("zip_code" INT, "type" VARCHAR, "city" VARCHAR, "county" VARCHAR, "state" VARCHAR, "short_state" VARCHAR, PRIMARY KEY ("zip_code"));

CREATE TABLE IF NOT EXISTS "attendance" ("link_to_event" VARCHAR, "link_to_member" VARCHAR);

CREATE TABLE IF NOT EXISTS "budget" ("budget_id" VARCHAR, "category" VARCHAR, "spent" FLOAT, "remaining" FLOAT, "amount" INT, "event_status" VARCHAR, "link_to_event" VARCHAR, PRIMARY KEY ("budget_id"));

CREATE TABLE IF NOT EXISTS "expense" ("expense_id" VARCHAR, "expense_description" VARCHAR, "expense_date" VARCHAR, "cost" FLOAT, "approved" VARCHAR, "link_to_member" VARCHAR, "link_to_budget" VARCHAR, PRIMARY KEY ("expense_id"));

CREATE TABLE IF NOT EXISTS "income" ("income_id" VARCHAR, "date_received" VARCHAR, "amount" INT, "source" VARCHAR, "notes" VARCHAR, "link_to_member" VARCHAR, PRIMARY KEY ("income_id"));

CREATE TABLE IF NOT EXISTS "member" ("member_id" VARCHAR, "first_name" VARCHAR, "last_name" VARCHAR, "email" VARCHAR, "position" VARCHAR, "t_shirt_size" VARCHAR, "phone" VARCHAR, "zip" INT, "link_to_major" VARCHAR, PRIMARY KEY ("member_id"));

SELECT location FROM event WHERE event_name = 'Spring Budget Review';

SELECT location FROM event WHERE event_name = 'Spring Budget Review'