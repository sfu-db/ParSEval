-- Query: SELECT T1.`Enrollment (Ages 5-17)` FROM frpm AS T1 INNER JOIN schools AS T2 ON T1.CDSCode = T2.CDSCode WHERE T2.EdOpsCode = 'SSS' AND T2.City = 'Fremont' AND T1.`Academic Year` BETWEEN 2014 AND 2015
Project($23, id=4)
  Filter(condition=$58 = 'SSS' AND $38 = 'Fremont' AND CAST($1 AS INT) >= 2014 AND CAST($1 AS INT) <= 2015, id=3)
    Join(condition=$0 = $29, type=INNER, id=2)
      Scan(table=frpm, id = 0)
      Scan(table=schools, id = 1)