-- Query: SELECT COUNT(T1.CDSCode) FROM frpm AS T1 INNER JOIN satscores AS T2 ON T1.CDSCode = T2.cds WHERE T1.`Charter Funding Type` = 'Directly funded' AND T1.`County Name` = 'Contra Costa' AND T2.NumTstTakr <= 250
Aggregate(keys=[], aggs=[COUNT(*)])
  Project($0, id=4)
    Filter(condition=$14 = 'Directly funded' AND $5 = 'Contra Costa' AND $35 <= 250, id=3)
      Join(condition=$0 = $29, type=INNER, id=2)
        Scan(table=frpm, id = 0)
        Scan(table=satscores, id = 1)