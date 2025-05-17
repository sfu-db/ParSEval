CREATE TABLE IF NOT EXISTS "account" ("account_id" INT, "district_id" INT, "frequency" VARCHAR, "date" DATE, PRIMARY KEY ("account_id"));

CREATE TABLE IF NOT EXISTS "card" ("card_id" INT, "disp_id" INT, "type" VARCHAR, "issued" DATE, PRIMARY KEY ("card_id"));

CREATE TABLE IF NOT EXISTS "client" ("client_id" INT, "gender" VARCHAR, "birth_date" DATE, "district_id" INT, PRIMARY KEY ("client_id"));

CREATE TABLE IF NOT EXISTS "disp" ("disp_id" INT, "client_id" INT, "account_id" INT, "type" VARCHAR, PRIMARY KEY ("disp_id"));

CREATE TABLE IF NOT EXISTS "district" ("district_id" INT, "A2" VARCHAR, "A3" VARCHAR, "A4" VARCHAR, "A5" VARCHAR, "A6" VARCHAR, "A7" VARCHAR, "A8" INT, "A9" INT, "A10" FLOAT, "A11" INT, "A12" FLOAT, "A13" FLOAT, "A14" INT, "A15" INT, "A16" INT, PRIMARY KEY ("district_id"));

CREATE TABLE IF NOT EXISTS "loan" ("loan_id" INT, "account_id" INT, "date" DATE, "amount" INT, "duration" INT, "payments" FLOAT, "status" VARCHAR, PRIMARY KEY ("loan_id"));

CREATE TABLE IF NOT EXISTS "order" ("order_id" INT, "account_id" INT, "bank_to" VARCHAR, "account_to" INT, "amount" FLOAT, "k_symbol" VARCHAR, PRIMARY KEY ("order_id"));

CREATE TABLE IF NOT EXISTS "trans" ("trans_id" INT, "account_id" INT, "date" DATE, "type" VARCHAR, "operation" VARCHAR, "amount" INT, "balance" INT, "k_symbol" VARCHAR, "bank" VARCHAR, "account" INT, PRIMARY KEY ("trans_id"));

SELECT COUNT(T1.client_id) FROM client AS T1 INNER JOIN district AS T2 ON T1.district_id = T2.district_id WHERE DATE_FORMAT(CAST(T1.birth_date AS DATETIME), '%Y') = '1920' AND T2.A3 = 'east Bohemia';

SELECT COUNT(DISTINCT T1.client_id) FROM client AS T1 INNER JOIN district AS T2 ON T1.district_id = T2.district_id WHERE DATE_FORMAT(CAST(T1.birth_date AS DATETIME), '%Y') = '1920' AND T2.A3 = 'east Bohemia'