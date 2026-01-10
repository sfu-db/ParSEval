-- Query: SELECT T2.MailStreet, T2.School FROM satscores AS T1 INNER JOIN schools AS T2 ON T1.cds = T2.CDSCode ORDER BY T1.AvgScrMath DESC LIMIT 5, 1
Sort(2, dir=['DESCENDING'], offset=5, limit=1)
  Project($23, $17, $8, id=3)
    Join(condition=$0 = $11, type=INNER, id=2)
      Scan(table=satscores, id = 0)
      Scan(table=schools, id = 1)