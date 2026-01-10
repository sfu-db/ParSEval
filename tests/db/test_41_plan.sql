-- Query: SELECT T2.School FROM satscores AS T1 INNER JOIN schools AS T2 ON T1.cds = T2.CDSCode WHERE T2.Virtual = 'F' ORDER BY T1.AvgScrRead DESC LIMIT 5
Sort(1, dir=['DESCENDING'], offset=0, limit=5)
  Project($17, $7, id=4)
    Filter(condition=$46 = 'F', id=3)
      Join(condition=$0 = $11, type=INNER, id=2)
        Scan(table=satscores, id = 0)
        Scan(table=schools, id = 1)