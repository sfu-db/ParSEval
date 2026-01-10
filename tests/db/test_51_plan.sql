-- Query: SELECT T2.MailStreet, T2.School FROM satscores AS T1 INNER JOIN schools AS T2 ON T1.cds = T2.CDSCode WHERE T1.AvgScrRead IS NOT NULL ORDER BY T1.AvgScrRead ASC LIMIT 1
Sort(2, dir=['ASCENDING'], offset=0, limit=1)
  Project($23, $17, $7, id=4)
    Filter(condition=$7 IS NOT NULL, id=3)
      Join(condition=$0 = $11, type=INNER, id=2)
        Scan(table=satscores, id = 0)
        Scan(table=schools, id = 1)