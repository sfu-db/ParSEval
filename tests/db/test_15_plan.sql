-- Query: SELECT T1.District FROM schools AS T1 INNER JOIN satscores AS T2 ON T1.CDSCode = T2.cds WHERE T1.StatusType = 'Active' ORDER BY T2.AvgScrRead DESC LIMIT 1
Sort(1, dir=['DESCENDING'], offset=0, limit=1)
  Project($5, $56, id=4)
    Filter(condition=$3 = 'Active', id=3)
      Join(condition=$0 = $49, type=INNER, id=2)
        Scan(table=schools, id = 0)
        Scan(table=satscores, id = 1)