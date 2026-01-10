-- Query: SELECT T2.Phone FROM frpm AS T1 INNER JOIN schools AS T2 ON T1.CDSCode = T2.CDSCode WHERE T1.`Charter Funding Type` = 'Directly funded' AND T1.`Charter School (Y/N)` = 1 AND T2.OpenDate > '2000-01-01'
Project($46, id=4)
  Filter(condition=$14 = 'Directly funded' AND $12 = 1 AND $49 > CAST('2000-01-01' AS DATE), id=3)
    Join(condition=$0 = $29, type=INNER, id=2)
      Scan(table=frpm, id = 0)
      Scan(table=schools, id = 1)