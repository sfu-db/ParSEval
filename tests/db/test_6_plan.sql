-- Query: SELECT T2.School FROM satscores AS T1 INNER JOIN schools AS T2 ON T1.cds = T2.CDSCode WHERE T2.Magnet = 1 AND T1.NumTstTakr > 500
Project($17, id=4)
  Filter(condition=$47 = 1 AND $6 > 500, id=3)
    Join(condition=$0 = $11, type=INNER, id=2)
      Scan(table=satscores, id = 0)
      Scan(table=schools, id = 1)