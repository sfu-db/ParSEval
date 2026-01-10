-- Query: SELECT T1.AvgScrMath, T2.County FROM satscores AS T1 INNER JOIN schools AS T2 ON T1.cds = T2.CDSCode WHERE T1.AvgScrMath IS NOT NULL ORDER BY T1.AvgScrMath + T1.AvgScrRead + T1.AvgScrWrite ASC LIMIT 1
Sort(2, dir=['ASCENDING'], offset=0, limit=1)
  Project($8, $15, $8 + $7 + $9, id=4)
    Filter(condition=$8 IS NOT NULL, id=3)
      Join(condition=$0 = $11, type=INNER, id=2)
        Scan(table=satscores, id = 0)
        Scan(table=schools, id = 1)