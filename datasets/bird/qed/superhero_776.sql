CREATE TABLE IF NOT EXISTS "alignment" ("id" INT, "alignment" VARCHAR, PRIMARY KEY ("id"));

CREATE TABLE IF NOT EXISTS "attribute" ("id" INT, "attribute_name" VARCHAR, PRIMARY KEY ("id"));

CREATE TABLE IF NOT EXISTS "colour" ("id" INT, "colour" VARCHAR, PRIMARY KEY ("id"));

CREATE TABLE IF NOT EXISTS "gender" ("id" INT, "gender" VARCHAR, PRIMARY KEY ("id"));

CREATE TABLE IF NOT EXISTS "publisher" ("id" INT, "publisher_name" VARCHAR, PRIMARY KEY ("id"));

CREATE TABLE IF NOT EXISTS "race" ("id" INT, "race" VARCHAR, PRIMARY KEY ("id"));

CREATE TABLE IF NOT EXISTS "superhero" ("id" INT, "superhero_name" VARCHAR, "full_name" VARCHAR, "gender_id" INT, "eye_colour_id" INT, "hair_colour_id" INT, "skin_colour_id" INT, "race_id" INT, "publisher_id" INT, "alignment_id" INT, "height_cm" INT, "weight_kg" INT, PRIMARY KEY ("id"));

CREATE TABLE IF NOT EXISTS "hero_attribute" ("hero_id" INT, "attribute_id" INT, "attribute_value" INT);

CREATE TABLE IF NOT EXISTS "superpower" ("id" INT, "power_name" VARCHAR, PRIMARY KEY ("id"));

CREATE TABLE IF NOT EXISTS "hero_power" ("hero_id" INT, "power_id" INT);

SELECT CAST(COUNT(CASE WHEN T3.colour = 'Blue' THEN T1.id ELSE NULL END) AS FLOAT) * 100 / COUNT(T1.id) FROM superhero AS T1 INNER JOIN gender AS T2 ON T1.gender_id = T2.id INNER JOIN colour AS T3 ON T1.skin_colour_id = T3.id WHERE T2.gender = 'Female';

SELECT CAST(SUM(CASE WHEN T2.colour = 'Blue' THEN 1 ELSE 0 END) AS FLOAT) * 100 / COUNT(T1.gender_id) FROM superhero AS T1 INNER JOIN colour AS T2 ON T1.eye_colour_id = T2.id WHERE T1.gender_id = (SELECT id FROM gender WHERE gender = 'Female')