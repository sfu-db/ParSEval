-- Query: SELECT T2.Website FROM satscores AS T1 INNER JOIN schools AS T2 ON T1.cds = T2.CDSCode WHERE T1.NumTstTakr BETWEEN 2000 AND 3000 AND T2.County = 'Los Angeles'
Project($30, id=4)
  Filter(condition=$6 >= 2000 AND $6 <= 3000 AND $15 = 'Los Angeles', id=3)
    Join(condition=$0 = $11, type=INNER, id=2)
      Scan(table=satscores, id = 0)
      Scan(table=schools, id = 1)