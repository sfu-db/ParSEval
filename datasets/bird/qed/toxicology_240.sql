CREATE TABLE IF NOT EXISTS "atom" ("atom_id" VARCHAR, "molecule_id" VARCHAR, "element" VARCHAR, PRIMARY KEY ("atom_id"));

CREATE TABLE IF NOT EXISTS "bond" ("bond_id" VARCHAR, "molecule_id" VARCHAR, "bond_type" VARCHAR, PRIMARY KEY ("bond_id"));

CREATE TABLE IF NOT EXISTS "connected" ("atom_id" VARCHAR, "atom_id2" VARCHAR, "bond_id" VARCHAR);

CREATE TABLE IF NOT EXISTS "molecule" ("molecule_id" VARCHAR, "label" VARCHAR, PRIMARY KEY ("molecule_id"));

SELECT COUNT(T.bond_id) FROM connected AS T WHERE SUBSTR(T.atom_id, -2) = '19';

SELECT COUNT(bond_id) FROM connected WHERE atom_id LIKE 'TR%_19' OR atom_id2 LIKE 'TR%_19'