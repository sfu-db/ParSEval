CREATE TABLE IF NOT EXISTS "atom" ("atom_id" VARCHAR, "molecule_id" VARCHAR, "element" VARCHAR, PRIMARY KEY ("atom_id"));

CREATE TABLE IF NOT EXISTS "bond" ("bond_id" VARCHAR, "molecule_id" VARCHAR, "bond_type" VARCHAR, PRIMARY KEY ("bond_id"));

CREATE TABLE IF NOT EXISTS "connected" ("atom_id" VARCHAR, "atom_id2" VARCHAR, "bond_id" VARCHAR);

CREATE TABLE IF NOT EXISTS "molecule" ("molecule_id" VARCHAR, "label" VARCHAR, PRIMARY KEY ("molecule_id"));

SELECT CAST((SELECT COUNT(T1.atom_id) FROM connected AS T1 INNER JOIN bond AS T2 ON T1.bond_id = T2.bond_id GROUP BY T2.bond_type ORDER BY COUNT(T2.bond_id) DESC LIMIT 1) AS FLOAT) * 100 / (SELECT COUNT(atom_id) FROM connected);

SELECT CAST(COUNT(T1.bond_id) AS FLOAT) * 100 / (SELECT COUNT(T2.atom_id) FROM atom AS T2 INNER JOIN connected AS T3 ON T2.atom_id = T3.atom_id GROUP BY T2.element ORDER BY COUNT(T2.atom_id) DESC LIMIT 1) FROM bond AS T1 INNER JOIN connected AS T2 ON T1.bond_id = T2.bond_id