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

SELECT DISTINCT T3.colour FROM superhero AS T1 INNER JOIN race AS T2 ON T1.race_id = T2.id INNER JOIN colour AS T3 ON T1.hair_colour_id = T3.id WHERE T1.height_cm = 185 AND T2.race = 'Human';

SELECT colour.colour FROM superhero INNER JOIN race ON superhero.race_id = race.id INNER JOIN colour ON superhero.hair_colour_id = colour.id WHERE superhero.height_cm = 185 AND race.race = 'Human'