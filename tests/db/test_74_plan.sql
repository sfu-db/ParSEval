-- Query: SELECT MIN(T1.`Low Grade`) FROM frpm AS T1 INNER JOIN schools AS T2 ON T1.CDSCode = T2.CDSCode WHERE T2.NCESDist = 613360 AND T2.EdOpsCode = 'SPECON'
Aggregate(keys=[], aggs=[MIN($0)])
  Project($16, id=4)
    Filter(condition=CAST($30 AS INT) = 613360 AND $58 = 'SPECON', id=3)
      Join(condition=$0 = $29, type=INNER, id=2)
        Scan(table=frpm, id = 0)
        Scan(table=schools, id = 1)