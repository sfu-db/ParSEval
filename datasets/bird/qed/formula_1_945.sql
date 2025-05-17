CREATE TABLE IF NOT EXISTS "circuits" ("circuitId" INT, "circuitRef" VARCHAR, "name" VARCHAR, "location" VARCHAR, "country" VARCHAR, "lat" FLOAT, "lng" FLOAT, "alt" INT, "url" VARCHAR, PRIMARY KEY ("circuitId"));

CREATE TABLE IF NOT EXISTS "constructors" ("constructorId" INT, "constructorRef" VARCHAR, "name" VARCHAR, "nationality" VARCHAR, "url" VARCHAR, PRIMARY KEY ("constructorId"));

CREATE TABLE IF NOT EXISTS "drivers" ("driverId" INT, "driverRef" VARCHAR, "number" INT, "code" VARCHAR, "forename" VARCHAR, "surname" VARCHAR, "dob" DATE, "nationality" VARCHAR, "url" VARCHAR, PRIMARY KEY ("driverId"));

CREATE TABLE IF NOT EXISTS "seasons" ("year" INT, "url" VARCHAR, PRIMARY KEY ("year"));

CREATE TABLE IF NOT EXISTS "races" ("raceId" INT, "year" INT, "round" INT, "circuitId" INT, "name" VARCHAR, "date" DATE, "time" VARCHAR, "url" VARCHAR, PRIMARY KEY ("raceId"));

CREATE TABLE IF NOT EXISTS "constructorResults" ("constructorResultsId" INT, "raceId" INT, "constructorId" INT, "points" FLOAT, "status" VARCHAR, PRIMARY KEY ("constructorResultsId"));

CREATE TABLE IF NOT EXISTS "constructorStandings" ("constructorStandingsId" INT, "raceId" INT, "constructorId" INT, "points" FLOAT, "position" INT, "positionText" VARCHAR, "wins" INT, PRIMARY KEY ("constructorStandingsId"));

CREATE TABLE IF NOT EXISTS "driverStandings" ("driverStandingsId" INT, "raceId" INT, "driverId" INT, "points" FLOAT, "position" INT, "positionText" VARCHAR, "wins" INT, PRIMARY KEY ("driverStandingsId"));

CREATE TABLE IF NOT EXISTS "lapTimes" ("raceId" INT, "driverId" INT, "lap" INT, "position" INT, "time" VARCHAR, "milliseconds" INT);

CREATE TABLE IF NOT EXISTS "pitStops" ("raceId" INT, "driverId" INT, "stop" INT, "lap" INT, "time" VARCHAR, "duration" VARCHAR, "milliseconds" INT);

CREATE TABLE IF NOT EXISTS "qualifying" ("qualifyId" INT, "raceId" INT, "driverId" INT, "constructorId" INT, "number" INT, "position" INT, "q1" VARCHAR, "q2" VARCHAR, "q3" VARCHAR, PRIMARY KEY ("qualifyId"));

CREATE TABLE IF NOT EXISTS "status" ("statusId" INT, "status" VARCHAR, PRIMARY KEY ("statusId"));

CREATE TABLE IF NOT EXISTS "results" ("resultId" INT, "raceId" INT, "driverId" INT, "constructorId" INT, "number" INT, "grid" INT, "position" INT, "positionText" VARCHAR, "positionOrder" INT, "points" FLOAT, "laps" INT, "time" VARCHAR, "milliseconds" INT, "fastestLap" INT, "rank" INT, "fastestLapTime" VARCHAR, "fastestLapSpeed" VARCHAR, "statusId" INT, PRIMARY KEY ("resultId"));

WITH time_in_seconds AS (SELECT T1.positionOrder, CASE WHEN T1.positionOrder = 1 THEN (CAST(SUBSTR(T1.time, 1, 1) AS FLOAT) * 3600) + (CAST(SUBSTR(T1.time, 3, 2) AS FLOAT) * 60) + CAST(SUBSTR(T1.time, 6) AS FLOAT) ELSE CAST(SUBSTR(T1.time, 2) AS FLOAT) END AS time_seconds FROM results AS T1 INNER JOIN races AS T2 ON T1.raceId = T2.raceId WHERE T2.name = 'Australian Grand Prix' AND NOT T1.time IS NULL AND T2.year = 2008), champion_time AS (SELECT time_seconds FROM time_in_seconds WHERE positionOrder = 1), last_driver_incremental AS (SELECT time_seconds FROM time_in_seconds WHERE positionOrder = (SELECT MAX(positionOrder) FROM time_in_seconds)) SELECT (CAST((SELECT time_seconds FROM last_driver_incremental) AS FLOAT) * 100) / (SELECT time_seconds + (SELECT time_seconds FROM last_driver_incremental) FROM champion_time);

SELECT (CAST((JULIANDAY(T1.time) - JULIANDAY(T2.time)) * 24 * 60 * 60 * 1000 AS FLOAT) / (JULIANDAY(T1.time) * 24 * 60 * 60 * 1000)) * 100 AS percentage FROM (SELECT time FROM results WHERE raceId = (SELECT raceId FROM races WHERE year = 2008 AND name = 'Australian Grand Prix') AND positionOrder = 1) AS T1, (SELECT time FROM results WHERE raceId = (SELECT raceId FROM races WHERE year = 2008 AND name = 'Australian Grand Prix') AND positionOrder = (SELECT MAX(positionOrder) FROM results WHERE raceId = (SELECT raceId FROM races WHERE year = 2008 AND name = 'Australian Grand Prix'))) AS T2