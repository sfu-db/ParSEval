CREATE TABLE IF NOT EXISTS "atom" ("atom_id" VARCHAR, "molecule_id" VARCHAR, "element" VARCHAR, PRIMARY KEY ("atom_id"));

CREATE TABLE IF NOT EXISTS "bond" ("bond_id" VARCHAR, "molecule_id" VARCHAR, "bond_type" VARCHAR, PRIMARY KEY ("bond_id"));

CREATE TABLE IF NOT EXISTS "connected" ("atom_id" VARCHAR, "atom_id2" VARCHAR, "bond_id" VARCHAR);

CREATE TABLE IF NOT EXISTS "molecule" ("molecule_id" VARCHAR, "label" VARCHAR, PRIMARY KEY ("molecule_id"));

SELECT T2.bond_id, T2.atom_id2, T1.element AS flag_have_CaCl FROM atom AS T1 INNER JOIN connected AS T2 ON T2.atom_id = T1.atom_id WHERE T2.bond_id = 'TR001_1_8' AND (T1.element = 'c1' OR T1.element = 'c');

SELECT CASE WHEN COUNT(DISTINCT T1.element) = 2 THEN 'yes' ELSE 'no' END AS yn FROM atom AS T1 INNER JOIN connected AS T2 ON T1.atom_id = T2.atom_id OR T1.atom_id = T2.atom_id2 WHERE T2.bond_id = 'TR001_1_8' AND T1.element IN ('cl', 'c')