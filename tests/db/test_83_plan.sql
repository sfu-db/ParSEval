-- Query: SELECT T2.City, COUNT(T2.CDSCode) FROM frpm AS T1 INNER JOIN schools AS T2 ON T1.CDSCode = T2.CDSCode WHERE T2.Magnet = 1 AND T2.GSoffered = 'K-8' AND T1.`NSLP Provision Status` = 'Multiple Provision Types' GROUP BY T2.City
Aggregate(keys=[$0], aggs=[COUNT(*)])
  Project($38, $29, id=4)
    Filter(condition=$65 = 1 AND $62 = 'K-8' AND $11 = 'Multiple Provision Types', id=3)
      Join(condition=$0 = $29, type=INNER, id=2)
        Scan(table=frpm, id = 0)
        Scan(table=schools, id = 1)