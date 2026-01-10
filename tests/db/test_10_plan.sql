-- Query: SELECT T2.`FRPM Count (Ages 5-17)` FROM satscores AS T1 INNER JOIN frpm AS T2 ON T1.cds = T2.CDSCode ORDER BY T1.AvgScrRead DESC LIMIT 1
Sort(1, dir=['DESCENDING'], offset=0, limit=1)
  Project($37, $7, id=3)
    Join(condition=$0 = $11, type=INNER, id=2)
      Scan(table=satscores, id = 0)
      Scan(table=frpm, id = 1)