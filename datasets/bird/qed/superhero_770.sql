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

SELECT T1.superhero_name FROM superhero AS T1 INNER JOIN hero_attribute AS T2 ON T1.id = T2.hero_id INNER JOIN attribute AS T3 ON T3.id = T2.attribute_id INNER JOIN publisher AS T4 ON T4.id = T1.publisher_id WHERE T4.publisher_name = 'Dark Horse Comics' AND T3.attribute_name = 'Durability' ORDER BY T2.attribute_value DESC LIMIT 1;

SELECT T1.superhero_name FROM superhero AS T1 INNER JOIN hero_attribute AS T2 ON T1.id = T2.hero_id INNER JOIN attribute AS T3 ON T2.attribute_id = T3.id INNER JOIN publisher AS T4 ON T1.publisher_id = T4.id WHERE T3.attribute_name = 'durability' AND T4.publisher_name = 'Dark Horse Comics' ORDER BY T2.attribute_value DESC LIMIT 1