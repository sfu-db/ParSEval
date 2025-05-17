CREATE TABLE IF NOT EXISTS "atom" ("atom_id" VARCHAR, "molecule_id" VARCHAR, "element" VARCHAR, PRIMARY KEY ("atom_id"));

CREATE TABLE IF NOT EXISTS "bond" ("bond_id" VARCHAR, "molecule_id" VARCHAR, "bond_type" VARCHAR, PRIMARY KEY ("bond_id"));

CREATE TABLE IF NOT EXISTS "connected" ("atom_id" VARCHAR, "atom_id2" VARCHAR, "bond_id" VARCHAR);

CREATE TABLE IF NOT EXISTS "molecule" ("molecule_id" VARCHAR, "label" VARCHAR, PRIMARY KEY ("molecule_id"));

SELECT T.atom_id FROM atom AS T WHERE T.molecule_id = 'TR186';

SELECT T1.atom_id, T1.element FROM atom AS T1 WHERE T1.molecule_id = 'TR186'