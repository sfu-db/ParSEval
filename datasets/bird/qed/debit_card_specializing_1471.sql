CREATE TABLE IF NOT EXISTS "customers" ("CustomerID" INT, "Segment" VARCHAR, "Currency" VARCHAR, PRIMARY KEY ("CustomerID"));

CREATE TABLE IF NOT EXISTS "gasstations" ("GasStationID" INT, "ChainID" INT, "Country" VARCHAR, "Segment" VARCHAR, PRIMARY KEY ("GasStationID"));

CREATE TABLE IF NOT EXISTS "products" ("ProductID" INT, "Description" VARCHAR, PRIMARY KEY ("ProductID"));

CREATE TABLE IF NOT EXISTS "transactions_1k" ("TransactionID" INT, "Date" DATE, "Time" VARCHAR, "CustomerID" INT, "CardID" INT, "GasStationID" INT, "ProductID" INT, "Amount" INT, "Price" FLOAT, PRIMARY KEY ("TransactionID"));

CREATE TABLE IF NOT EXISTS "yearmonth" ("CustomerID" INT, "Date" VARCHAR, "Consumption" FLOAT);

SELECT COUNT(GasStationID) FROM gasstations WHERE Country = 'CZE' AND Segment = 'Premium';

SELECT COUNT(GasStationID) FROM gasstations WHERE Country = 'CZE' AND Segment = 'Premium'