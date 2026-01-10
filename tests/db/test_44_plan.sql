-- Query: SELECT T1.AvgScrWrite, T2.City FROM satscores AS T1 INNER JOIN schools AS T2 ON T1.cds = T2.CDSCode ORDER BY T1.NumGE1500 DESC LIMIT 1
Sort(2, dir=['DESCENDING'], offset=0, limit=1)
  Project($9, $20, $10, id=3)
    Join(condition=$0 = $11, type=INNER, id=2)
      Scan(table=satscores, id = 0)
      Scan(table=schools, id = 1)