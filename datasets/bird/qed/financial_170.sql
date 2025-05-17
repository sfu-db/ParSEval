CREATE TABLE IF NOT EXISTS "account" ("account_id" INT, "district_id" INT, "frequency" VARCHAR, "date" DATE, PRIMARY KEY ("account_id"));

CREATE TABLE IF NOT EXISTS "card" ("card_id" INT, "disp_id" INT, "type" VARCHAR, "issued" DATE, PRIMARY KEY ("card_id"));

CREATE TABLE IF NOT EXISTS "client" ("client_id" INT, "gender" VARCHAR, "birth_date" DATE, "district_id" INT, PRIMARY KEY ("client_id"));

CREATE TABLE IF NOT EXISTS "disp" ("disp_id" INT, "client_id" INT, "account_id" INT, "type" VARCHAR, PRIMARY KEY ("disp_id"));

CREATE TABLE IF NOT EXISTS "district" ("district_id" INT, "A2" VARCHAR, "A3" VARCHAR, "A4" VARCHAR, "A5" VARCHAR, "A6" VARCHAR, "A7" VARCHAR, "A8" INT, "A9" INT, "A10" FLOAT, "A11" INT, "A12" FLOAT, "A13" FLOAT, "A14" INT, "A15" INT, "A16" INT, PRIMARY KEY ("district_id"));

CREATE TABLE IF NOT EXISTS "loan" ("loan_id" INT, "account_id" INT, "date" DATE, "amount" INT, "duration" INT, "payments" FLOAT, "status" VARCHAR, PRIMARY KEY ("loan_id"));

CREATE TABLE IF NOT EXISTS "order" ("order_id" INT, "account_id" INT, "bank_to" VARCHAR, "account_to" INT, "amount" FLOAT, "k_symbol" VARCHAR, PRIMARY KEY ("order_id"));

CREATE TABLE IF NOT EXISTS "trans" ("trans_id" INT, "account_id" INT, "date" DATE, "type" VARCHAR, "operation" VARCHAR, "amount" INT, "balance" INT, "k_symbol" VARCHAR, "bank" VARCHAR, "account" INT, PRIMARY KEY ("trans_id"));

SELECT CAST((SUM(CASE WHEN DATE_FORMAT(CAST(T1.date AS DATETIME), '%Y') = '1997' THEN T1.amount ELSE 0 END) - SUM(CASE WHEN DATE_FORMAT(CAST(T1.date AS DATETIME), '%Y') = '1996' THEN T1.amount ELSE 0 END)) AS FLOAT) * 100 / SUM(CASE WHEN DATE_FORMAT(CAST(T1.date AS DATETIME), '%Y') = '1996' THEN T1.amount ELSE 0 END) FROM loan AS T1 INNER JOIN account AS T2 ON T1.account_id = T2.account_id INNER JOIN disp AS T3 ON T3.account_id = T2.account_id INNER JOIN client AS T4 ON T4.client_id = T3.client_id WHERE T4.gender = 'M' AND T3.type = 'OWNER';

SELECT 100.0 * (SUM(CASE WHEN DATE_FORMAT(CAST(T2.date AS DATETIME), '%Y') = '1997' THEN T2.amount ELSE 0 END) - SUM(CASE WHEN DATE_FORMAT(CAST(T2.date AS DATETIME), '%Y') = '1996' THEN T2.amount ELSE 0 END)) / SUM(CASE WHEN DATE_FORMAT(CAST(T2.date AS DATETIME), '%Y') = '1996' THEN T2.amount ELSE 0 END) FROM client AS T1 INNER JOIN loan AS T2 ON T1.client_id = T2.client_id WHERE T1.gender = 'M'