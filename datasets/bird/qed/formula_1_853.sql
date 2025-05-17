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

SELECT COUNT(T3.raceId) FROM circuits AS T1 INNER JOIN races AS T3 ON T3.circuitID = T1.circuitId WHERE NOT T1.country IN ('Bahrain', 'China', 'Singapore', 'Japan', 'Korea', 'Turkey', 'UAE', 'Malaysia', 'Spain', 'Monaco', 'Azerbaijan', 'Austria', 'Belgium', 'France', 'Germany', 'Hungary', 'Italy', 'UK') AND T3.year = 2010;

SELECT COUNT(DISTINCT T1.raceId) FROM races AS T1 INNER JOIN circuits AS T2 ON T1.circuitId = T2.circuitId WHERE T1.year = 2010 AND NOT T2.location IN ('Asia', 'Europe')