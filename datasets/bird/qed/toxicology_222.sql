CREATE TABLE IF NOT EXISTS "atom" ("atom_id" VARCHAR, "molecule_id" VARCHAR, "element" VARCHAR, PRIMARY KEY ("atom_id"));

CREATE TABLE IF NOT EXISTS "bond" ("bond_id" VARCHAR, "molecule_id" VARCHAR, "bond_type" VARCHAR, PRIMARY KEY ("bond_id"));

CREATE TABLE IF NOT EXISTS "connected" ("atom_id" VARCHAR, "atom_id2" VARCHAR, "bond_id" VARCHAR);

CREATE TABLE IF NOT EXISTS "molecule" ("molecule_id" VARCHAR, "label" VARCHAR, PRIMARY KEY ("molecule_id"));

SELECT SUBSTR(T.bond_id, 1, 7) AS atom_id1, T.molecule_id || SUBSTR(T.bond_id, 8, 2) AS atom_id2 FROM bond AS T WHERE T.molecule_id = 'TR001' AND T.bond_id = 'TR001_2_6';

SELECT T1.atom_id, T1.atom_id2 FROM connected AS T1 INNER JOIN atom AS T2 ON T1.atom_id = T2.atom_id INNER JOIN bond AS T3 ON T1.bond_id = T3.bond_id WHERE T3.molecule_id = 'TR001' AND T3.bond_id = 'TR001_2_6'