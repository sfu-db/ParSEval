CREATE TABLE IF NOT EXISTS "atom" ("atom_id" VARCHAR, "molecule_id" VARCHAR, "element" VARCHAR, PRIMARY KEY ("atom_id"));

CREATE TABLE IF NOT EXISTS "bond" ("bond_id" VARCHAR, "molecule_id" VARCHAR, "bond_type" VARCHAR, PRIMARY KEY ("bond_id"));

CREATE TABLE IF NOT EXISTS "connected" ("atom_id" VARCHAR, "atom_id2" VARCHAR, "bond_id" VARCHAR);

CREATE TABLE IF NOT EXISTS "molecule" ("molecule_id" VARCHAR, "label" VARCHAR, PRIMARY KEY ("molecule_id"));

SELECT DISTINCT T1.element FROM atom AS T1 INNER JOIN connected AS T2 ON T1.atom_id = T2.atom_id WHERE T2.bond_id = 'TR001_2_4';

SELECT CASE WHEN T2.element = 'cl' THEN 'Chlorine' WHEN T2.element = 'c' THEN 'Carbon' WHEN T2.element = 'h' THEN 'Hydrogen' WHEN T2.element = 'o' THEN 'Oxygen' WHEN T2.element = 's' THEN 'Sulfur' WHEN T2.element = 'n' THEN 'Nitrogen' WHEN T2.element = 'p' THEN 'Phosphorus' WHEN T2.element = 'na' THEN 'Sodium' WHEN T2.element = 'br' THEN 'Bromine' WHEN T2.element = 'f' THEN 'Fluorine' WHEN T2.element = 'i' THEN 'Iodine' WHEN T2.element = 'sn' THEN 'Tin' WHEN T2.element = 'pb' THEN 'Lead' WHEN T2.element = 'te' THEN 'Tellurium' WHEN T2.element = 'ca' THEN 'Calcium' END AS "Element" FROM connected AS T1 INNER JOIN atom AS T2 ON T1.atom_id = T2.atom_id OR T1.atom_id2 = T2.atom_id WHERE T1.bond_id = 'TR001_2_4'