-- Query: SELECT CAST(T2.`Free Meal Count (Ages 5-17)` AS REAL) / T2.`Enrollment (Ages 5-17)` FROM schools AS T1 INNER JOIN frpm AS T2 ON T1.CDSCode = T2.CDSCode WHERE T1.AdmFName1 = 'Kacey' AND T1.AdmLName1 = 'Gibson'
Project(CAST($73 AS FLOAT) / $72, id=4)
  Filter(condition=$39 = 'Kacey' AND $40 = 'Gibson', id=3)
    Join(condition=$0 = $49, type=INNER, id=2)
      Scan(table=schools, id = 0)
      Scan(table=frpm, id = 1)