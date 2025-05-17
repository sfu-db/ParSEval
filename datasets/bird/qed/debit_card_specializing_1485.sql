CREATE TABLE IF NOT EXISTS "customers" ("CustomerID" INT, "Segment" VARCHAR, "Currency" VARCHAR, PRIMARY KEY ("CustomerID"));

CREATE TABLE IF NOT EXISTS "gasstations" ("GasStationID" INT, "ChainID" INT, "Country" VARCHAR, "Segment" VARCHAR, PRIMARY KEY ("GasStationID"));

CREATE TABLE IF NOT EXISTS "products" ("ProductID" INT, "Description" VARCHAR, PRIMARY KEY ("ProductID"));

CREATE TABLE IF NOT EXISTS "transactions_1k" ("TransactionID" INT, "Date" DATE, "Time" VARCHAR, "CustomerID" INT, "CardID" INT, "GasStationID" INT, "ProductID" INT, "Amount" INT, "Price" FLOAT, PRIMARY KEY ("TransactionID"));

CREATE TABLE IF NOT EXISTS "yearmonth" ("CustomerID" INT, "Date" VARCHAR, "Consumption" FLOAT);

SELECT SUM(CASE WHEN Country = 'CZE' THEN 1 ELSE 0 END) - SUM(CASE WHEN Country = 'SVK' THEN 1 ELSE 0 END) FROM gasstations WHERE Segment = 'Discount';

SELECT (SELECT COUNT(GasStationID) FROM gasstations WHERE Country = 'Czech Republic' AND Segment = 'discount') - (SELECT COUNT(GasStationID) FROM gasstations WHERE Country = 'Slovakia' AND Segment = 'discount') AS More_Discount_Gas_Stations