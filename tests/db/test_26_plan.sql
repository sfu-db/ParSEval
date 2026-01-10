-- Query: SELECT T1.`School Name`, T2.Zip, T2.Street, T2.City, T2.State FROM frpm AS T1 INNER JOIN schools AS T2 ON T1.CDSCode = T2.CDSCode WHERE T2.County = 'Monterey' AND T1.`Free Meal Count (Ages 5-17)` > 800 AND T1.`School Type` = 'High Schools (Public)'
Project($7, $39, $36, $38, $40, id=4)
  Filter(condition=$33 = 'Monterey' AND $24 > 800 AND $9 = 'High Schools (Public)', id=3)
    Join(condition=$0 = $29, type=INNER, id=2)
      Scan(table=frpm, id = 0)
      Scan(table=schools, id = 1)