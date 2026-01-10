-- Query: SELECT COUNT(T1.`School Name`) FROM frpm AS T1 INNER JOIN schools AS T2 ON T1.CDSCode = T2.CDSCode WHERE T2.County = 'Amador' AND T1.`Low Grade` = 9 AND T1.`High Grade` = 12
Aggregate(keys=[], aggs=[COUNT($0)])
  Project($7, id=4)
    Filter(condition=$33 = 'Amador' AND CAST($16 AS INT) = 9 AND CAST($17 AS INT) = 12, id=3)
      Join(condition=$0 = $29, type=INNER, id=2)
        Scan(table=frpm, id = 0)
        Scan(table=schools, id = 1)