-- Query: SELECT COUNT(T1.CDSCode) FROM schools AS T1 INNER JOIN satscores AS T2 ON T1.CDSCode = T2.cds WHERE T1.StatusType = 'Merged' AND T2.NumTstTakr < 100 AND T1.County = 'Alameda'
Aggregate(keys=[], aggs=[COUNT(*)])
  Project($0, id=4)
    Filter(condition=$3 = 'Merged' AND $55 < 100 AND $4 = 'Alameda', id=3)
      Join(condition=$0 = $49, type=INNER, id=2)
        Scan(table=schools, id = 0)
        Scan(table=satscores, id = 1)