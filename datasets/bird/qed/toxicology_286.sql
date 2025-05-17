CREATE TABLE IF NOT EXISTS "atom" ("atom_id" VARCHAR, "molecule_id" VARCHAR, "element" VARCHAR, PRIMARY KEY ("atom_id"));

CREATE TABLE IF NOT EXISTS "bond" ("bond_id" VARCHAR, "molecule_id" VARCHAR, "bond_type" VARCHAR, PRIMARY KEY ("bond_id"));

CREATE TABLE IF NOT EXISTS "connected" ("atom_id" VARCHAR, "atom_id2" VARCHAR, "bond_id" VARCHAR);

CREATE TABLE IF NOT EXISTS "molecule" ("molecule_id" VARCHAR, "label" VARCHAR, PRIMARY KEY ("molecule_id"));

SELECT T1.element FROM atom AS T1 INNER JOIN connected AS T2 ON T1.atom_id = T2.atom_id INNER JOIN bond AS T3 ON T2.bond_id = T3.bond_id WHERE T3.bond_id = 'TR001_10_11';

SELECT CASE WHEN T1.element = 'cl' THEN 'Chlorine' WHEN T1.element = 'c' THEN 'Carbon' WHEN T1.element = 'h' THEN 'Hydrogen' WHEN T1.element = 'o' THEN 'Oxygen' WHEN T1.element = 's' THEN 'Sulfur' WHEN T1.element = 'n' THEN 'Nitrogen' WHEN T1.element = 'p' THEN 'Phosphorus' WHEN T1.element = 'na' THEN 'Sodium' WHEN T1.element = 'br' THEN 'Bromine' WHEN T1.element = 'f' THEN 'Fluorine' WHEN T1.element = 'i' THEN 'Iodine' WHEN T1.element = 'sn' THEN 'Tin' WHEN T1.element = 'pb' THEN 'Lead' WHEN T1.element = 'te' THEN 'Tellurium' WHEN T1.element = 'ca' THEN 'Calcium' END AS "Element" FROM atom AS T1 INNER JOIN connected AS T2 ON T1.atom_id = T2.atom_id OR T1.atom_id = T2.atom_id2 WHERE T2.bond_id = 'TR001_10_11'