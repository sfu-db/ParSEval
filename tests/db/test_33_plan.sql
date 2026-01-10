-- Query: SELECT T2.Website, T1.`School Name` FROM frpm AS T1 INNER JOIN schools AS T2 ON T1.CDSCode = T2.CDSCode WHERE T1.`Free Meal Count (Ages 5-17)` BETWEEN 1900 AND 2000 AND T2.Website IS NOT NULL
Project($48, $7, id=4)
  Filter(condition=$24 >= 1900 AND $24 <= 2000 AND $48 IS NOT NULL, id=3)
    Join(condition=$0 = $29, type=INNER, id=2)
      Scan(table=frpm, id = 0)
      Scan(table=schools, id = 1)