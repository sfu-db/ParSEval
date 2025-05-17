CREATE TABLE IF NOT EXISTS "customers" ("CustomerID" INT, "Segment" VARCHAR, "Currency" VARCHAR, PRIMARY KEY ("CustomerID"));

CREATE TABLE IF NOT EXISTS "gasstations" ("GasStationID" INT, "ChainID" INT, "Country" VARCHAR, "Segment" VARCHAR, PRIMARY KEY ("GasStationID"));

CREATE TABLE IF NOT EXISTS "products" ("ProductID" INT, "Description" VARCHAR, PRIMARY KEY ("ProductID"));

CREATE TABLE IF NOT EXISTS "transactions_1k" ("TransactionID" INT, "Date" DATE, "Time" VARCHAR, "CustomerID" INT, "CardID" INT, "GasStationID" INT, "ProductID" INT, "Amount" INT, "Price" FLOAT, PRIMARY KEY ("TransactionID"));

CREATE TABLE IF NOT EXISTS "yearmonth" ("CustomerID" INT, "Date" VARCHAR, "Consumption" FLOAT);

SELECT T1.CustomerID, T2.Date, T2.Consumption FROM transactions_1k AS T1 INNER JOIN yearmonth AS T2 ON T1.CustomerID = T2.CustomerID WHERE T1.Date = '2012-08-24' AND T1.Price = 124.05 AND T2.Date = '201201';

SELECT T2.Date, T2.Consumption FROM transactions_1k AS T1 INNER JOIN yearmonth AS T2 ON T1.CustomerID = T2.CustomerID WHERE T1.Date = '2012-08-24' AND T1.Price = 124.05 AND DATE_FORMAT(CAST(T2.Date AS DATETIME), '%Y-%m') = '2012-01'