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

WITH fastest_lap_times AS (SELECT T1.raceId, T1.driverId, T1.FastestLapTime, (CAST(SUBSTR(T1.FastestLapTime, 1, INSTR(T1.FastestLapTime, ':') - 1) AS FLOAT) * 60) + (CAST(SUBSTR(T1.FastestLapTime, INSTR(T1.FastestLapTime, ':') + 1, INSTR(T1.FastestLapTime, '.') - INSTR(T1.FastestLapTime, ':') - 1) AS FLOAT)) + (CAST(SUBSTR(T1.FastestLapTime, INSTR(T1.FastestLapTime, '.') + 1) AS FLOAT) / 1000) AS time_in_seconds FROM results AS T1 WHERE NOT T1.FastestLapTime IS NULL), lap_record_race AS (SELECT T1.raceId, T1.driverId FROM results AS T1 INNER JOIN races AS T2 ON T1.raceId = T2.raceId INNER JOIN circuits AS T3 ON T2.circuitId = T3.circuitId INNER JOIN (SELECT MIN(fastest_lap_times.time_in_seconds) AS min_time_in_seconds FROM fastest_lap_times INNER JOIN races AS T2 ON fastest_lap_times.raceId = T2.raceId INNER JOIN circuits AS T3 ON T2.circuitId = T3.circuitId WHERE T2.name = 'Austrian Grand Prix') AS T4 ON (CAST(SUBSTR(T1.FastestLapTime, 1, INSTR(T1.FastestLapTime, ':') - 1) AS FLOAT) * 60) + (CAST(SUBSTR(T1.FastestLapTime, INSTR(T1.FastestLapTime, ':') + 1, INSTR(T1.FastestLapTime, '.') - INSTR(T1.FastestLapTime, ':') - 1) AS FLOAT)) + (CAST(SUBSTR(T1.FastestLapTime, INSTR(T1.FastestLapTime, '.') + 1) AS FLOAT) / 1000) = T4.min_time_in_seconds WHERE T2.name = 'Austrian Grand Prix') SELECT T4.duration FROM lap_record_race INNER JOIN pitStops AS T4 ON lap_record_race.raceId = T4.raceId AND lap_record_race.driverId = T4.driverId;

SELECT T2.duration FROM lapTimes AS T1 INNER JOIN pitStops AS T2 ON T1.raceId = T2.raceId AND T1.driverId = T2.driverId WHERE T1.time = (SELECT MIN(time) FROM lapTimes INNER JOIN races ON lapTimes.raceId = races.raceId INNER JOIN circuits ON races.circuitId = circuits.circuitId WHERE circuits.name = 'Austrian Grand Prix Circuit')