-- Query: SELECT COUNT(T2.`School Code`) FROM satscores AS T1 INNER JOIN frpm AS T2 ON T1.cds = T2.CDSCode WHERE T1.AvgScrMath > 560 AND T2.`Charter Funding Type` = 'Directly funded'
Aggregate(keys=[], aggs=[COUNT($0)])
  Project($15, id=4)
    Filter(condition=$8 > 560 AND $25 = 'Directly funded', id=3)
      Join(condition=$0 = $11, type=INNER, id=2)
        Scan(table=satscores, id = 0)
        Scan(table=frpm, id = 1)