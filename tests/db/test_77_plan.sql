-- Query: SELECT T2.School, T1.`FRPM Count (Ages 5-17)` * 100 / T1.`Enrollment (Ages 5-17)` FROM frpm AS T1 INNER JOIN schools AS T2 ON T1.CDSCode = T2.CDSCode WHERE T2.County = 'Los Angeles' AND T2.GSserved = 'K-9'
Project($35, $26 * 100 / $23, id=4)
  Filter(condition=$33 = 'Los Angeles' AND $63 = 'K-9', id=3)
    Join(condition=$0 = $29, type=INNER, id=2)
      Scan(table=frpm, id = 0)
      Scan(table=schools, id = 1)