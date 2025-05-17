CREATE TABLE IF NOT EXISTS "customers" ("CustomerID" INT, "Segment" VARCHAR, "Currency" VARCHAR, PRIMARY KEY ("CustomerID"));

CREATE TABLE IF NOT EXISTS "gasstations" ("GasStationID" INT, "ChainID" INT, "Country" VARCHAR, "Segment" VARCHAR, PRIMARY KEY ("GasStationID"));

CREATE TABLE IF NOT EXISTS "products" ("ProductID" INT, "Description" VARCHAR, PRIMARY KEY ("ProductID"));

CREATE TABLE IF NOT EXISTS "transactions_1k" ("TransactionID" INT, "Date" DATE, "Time" VARCHAR, "CustomerID" INT, "CardID" INT, "GasStationID" INT, "ProductID" INT, "Amount" INT, "Price" FLOAT, PRIMARY KEY ("TransactionID"));

CREATE TABLE IF NOT EXISTS "yearmonth" ("CustomerID" INT, "Date" VARCHAR, "Consumption" FLOAT);

SELECT SUM(Currency = 'CZK') - SUM(Currency = 'EUR') FROM customers WHERE Segment = 'SME';

SELECT (SELECT COUNT(DISTINCT CustomerID) FROM customers WHERE Segment = 'SME' AND Currency = 'Czech koruna') - (SELECT COUNT(DISTINCT CustomerID) FROM customers WHERE Segment = 'SME' AND Currency = 'Euro') AS MoreSMEs