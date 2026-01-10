-- Query: SELECT T1.Phone FROM schools AS T1 INNER JOIN satscores AS T2 ON T1.CDSCode = T2.cds ORDER BY T2.AvgScrMath DESC LIMIT 1
Sort(1, dir=['DESCENDING'], offset=0, limit=1)
  Project($17, $57, id=3)
    Join(condition=$0 = $49, type=INNER, id=2)
      Scan(table=schools, id = 0)
      Scan(table=satscores, id = 1)