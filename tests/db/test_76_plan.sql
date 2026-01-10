-- Query: SELECT T2.City FROM frpm AS T1 INNER JOIN schools AS T2 ON T1.CDSCode = T2.CDSCode WHERE T1.`NSLP Provision Status` = 'Lunch Provision 2' AND T2.County = 'Merced' AND T1.`Low Grade` = 9 AND T1.`High Grade` = 12 AND T2.EILCode = 'HS'
Project($38, id=4)
  Filter(condition=$11 = 'Lunch Provision 2' AND $33 = 'Merced' AND CAST($16 AS INT) = 9 AND CAST($17 AS INT) = 12 AND $60 = 'HS', id=3)
    Join(condition=$0 = $29, type=INNER, id=2)
      Scan(table=frpm, id = 0)
      Scan(table=schools, id = 1)