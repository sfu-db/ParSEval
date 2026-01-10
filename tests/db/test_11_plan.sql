-- Query: SELECT T2.CDSCode FROM schools AS T1 INNER JOIN frpm AS T2 ON T1.CDSCode = T2.CDSCode WHERE T2.`Enrollment (K-12)` + T2.`Enrollment (Ages 5-17)` > 500
Project($49, id=4)
  Filter(condition=$67 + $72 > 500, id=3)
    Join(condition=$0 = $49, type=INNER, id=2)
      Scan(table=schools, id = 0)
      Scan(table=frpm, id = 1)