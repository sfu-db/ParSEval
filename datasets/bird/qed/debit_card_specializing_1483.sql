CREATE TABLE IF NOT EXISTS "customers" ("CustomerID" INT, "Segment" VARCHAR, "Currency" VARCHAR, PRIMARY KEY ("CustomerID"));

CREATE TABLE IF NOT EXISTS "gasstations" ("GasStationID" INT, "ChainID" INT, "Country" VARCHAR, "Segment" VARCHAR, PRIMARY KEY ("GasStationID"));

CREATE TABLE IF NOT EXISTS "products" ("ProductID" INT, "Description" VARCHAR, PRIMARY KEY ("ProductID"));

CREATE TABLE IF NOT EXISTS "transactions_1k" ("TransactionID" INT, "Date" DATE, "Time" VARCHAR, "CustomerID" INT, "CardID" INT, "GasStationID" INT, "ProductID" INT, "Amount" INT, "Price" FLOAT, PRIMARY KEY ("TransactionID"));

CREATE TABLE IF NOT EXISTS "yearmonth" ("CustomerID" INT, "Date" VARCHAR, "Consumption" FLOAT);

SELECT CAST((SUM(CASE WHEN T1.Segment = 'SME' AND T2.Date LIKE '2013%' THEN T2.Consumption ELSE 0 END) - SUM(CASE WHEN T1.Segment = 'SME' AND T2.Date LIKE '2012%' THEN T2.Consumption ELSE 0 END)) AS FLOAT) * 100 / SUM(CASE WHEN T1.Segment = 'SME' AND T2.Date LIKE '2012%' THEN T2.Consumption ELSE 0 END), CAST(SUM(CASE WHEN T1.Segment = 'LAM' AND T2.Date LIKE '2013%' THEN T2.Consumption ELSE 0 END) - SUM(CASE WHEN T1.Segment = 'LAM' AND T2.Date LIKE '2012%' THEN T2.Consumption ELSE 0 END) AS FLOAT) * 100 / SUM(CASE WHEN T1.Segment = 'LAM' AND T2.Date LIKE '2012%' THEN T2.Consumption ELSE 0 END), CAST(SUM(CASE WHEN T1.Segment = 'KAM' AND T2.Date LIKE '2013%' THEN T2.Consumption ELSE 0 END) - SUM(CASE WHEN T1.Segment = 'KAM' AND T2.Date LIKE '2012%' THEN T2.Consumption ELSE 0 END) AS FLOAT) * 100 / SUM(CASE WHEN T1.Segment = 'KAM' AND T2.Date LIKE '2012%' THEN T2.Consumption ELSE 0 END) FROM customers AS T1 INNER JOIN yearmonth AS T2 ON T1.CustomerID = T2.CustomerID;

SELECT Segment, MAX((Consumption_2013 - Consumption_2012) / Consumption_2012 * 100) AS Max_Percentage_Increase, MIN((Consumption_2013 - Consumption_2012) / Consumption_2012 * 100) AS Min_Percentage_Increase FROM (SELECT c.Segment, SUM(CASE WHEN SUBSTR(y.Date, 1, 4) = '2012' THEN y.Consumption ELSE 0 END) AS Consumption_2012, SUM(CASE WHEN SUBSTR(y.Date, 1, 4) = '2013' THEN y.Consumption ELSE 0 END) AS Consumption_2013 FROM customers AS c JOIN yearmonth AS y ON c.CustomerID = y.CustomerID WHERE c.Currency = 'EUR' AND c.Segment IN ('SME', 'LAM', 'KAM') AND SUBSTR(y.Date, 1, 4) IN ('2012', '2013') GROUP BY c.Segment) AS t GROUP BY Segment