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

SELECT CAST(COUNT(CASE WHEN NOT T2.time IS NULL THEN T2.driverId END) AS FLOAT) * 100 / COUNT(T2.driverId) FROM races AS T1 INNER JOIN results AS T2 ON T2.raceId = T1.raceId WHERE T1.date = '1983-07-16';

SELECT CAST(COUNT(CASE WHEN NOT T2.time IS NULL THEN 1 END) AS FLOAT) * 100 / COUNT(T2.driverId) FROM races AS T1 INNER JOIN results AS T2 ON T1.raceId = T2.raceId WHERE T1.date = '1983-07-16'