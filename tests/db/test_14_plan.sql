-- Query: SELECT T1.NCESSchool FROM schools AS T1 INNER JOIN frpm AS T2 ON T1.CDSCode = T2.CDSCode ORDER BY T2.`Enrollment (Ages 5-17)` DESC LIMIT 5
Sort(1, dir=['DESCENDING'], offset=0, limit=5)
  Project($2, $72, id=3)
    Join(condition=$0 = $49, type=INNER, id=2)
      Scan(table=schools, id = 0)
      Scan(table=frpm, id = 1)