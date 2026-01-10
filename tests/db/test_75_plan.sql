-- Query: SELECT T2.EILName, T2.School FROM frpm AS T1 INNER JOIN schools AS T2 ON T1.CDSCode = T2.CDSCode WHERE T1.`NSLP Provision Status` = 'Breakfast Provision 2' AND T1.`County Code` = 37
Project($61, $35, id=4)
  Filter(condition=$11 = 'Breakfast Provision 2' AND CAST($2 AS INT) = 37, id=3)
    Join(condition=$0 = $29, type=INNER, id=2)
      Scan(table=frpm, id = 0)
      Scan(table=schools, id = 1)