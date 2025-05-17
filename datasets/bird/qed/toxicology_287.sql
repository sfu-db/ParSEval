CREATE TABLE IF NOT EXISTS "atom" ("atom_id" VARCHAR, "molecule_id" VARCHAR, "element" VARCHAR, PRIMARY KEY ("atom_id"));

CREATE TABLE IF NOT EXISTS "bond" ("bond_id" VARCHAR, "molecule_id" VARCHAR, "bond_type" VARCHAR, PRIMARY KEY ("bond_id"));

CREATE TABLE IF NOT EXISTS "connected" ("atom_id" VARCHAR, "atom_id2" VARCHAR, "bond_id" VARCHAR);

CREATE TABLE IF NOT EXISTS "molecule" ("molecule_id" VARCHAR, "label" VARCHAR, PRIMARY KEY ("molecule_id"));

SELECT CAST(COUNT(CASE WHEN T.bond_type = '#' THEN T.bond_id ELSE NULL END) AS FLOAT) * 100 / COUNT(T.bond_id) FROM bond AS T;

SELECT CAST(SUM(CASE WHEN bond.bond_type = '#' THEN 1 ELSE 0 END) AS FLOAT) * 100 / COUNT(molecule.molecule_id) AS percentage FROM molecule LEFT JOIN bond ON molecule.molecule_id = bond.molecule_id