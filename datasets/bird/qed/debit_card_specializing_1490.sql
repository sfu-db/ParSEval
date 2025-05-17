CREATE TABLE IF NOT EXISTS "customers" ("CustomerID" INT, "Segment" VARCHAR, "Currency" VARCHAR, PRIMARY KEY ("CustomerID"));

CREATE TABLE IF NOT EXISTS "gasstations" ("GasStationID" INT, "ChainID" INT, "Country" VARCHAR, "Segment" VARCHAR, PRIMARY KEY ("GasStationID"));

CREATE TABLE IF NOT EXISTS "products" ("ProductID" INT, "Description" VARCHAR, PRIMARY KEY ("ProductID"));

CREATE TABLE IF NOT EXISTS "transactions_1k" ("TransactionID" INT, "Date" DATE, "Time" VARCHAR, "CustomerID" INT, "CardID" INT, "GasStationID" INT, "ProductID" INT, "Amount" INT, "Price" FLOAT, PRIMARY KEY ("TransactionID"));

CREATE TABLE IF NOT EXISTS "yearmonth" ("CustomerID" INT, "Date" VARCHAR, "Consumption" FLOAT);

SELECT SUM(T2.Consumption) FROM customers AS T1 INNER JOIN yearmonth AS T2 ON T1.CustomerID = T2.CustomerID WHERE T2.Date = '201305' AND T1.Segment = 'KAM';

SELECT SUM(Consumption) FROM yearmonth WHERE Date = '201305' AND CustomerID IN (SELECT CustomerID FROM customers WHERE Segment = 'KAM')