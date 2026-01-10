-- Query: SELECT COUNT(T2.School) FROM frpm AS T1 INNER JOIN schools AS T2 ON T1.CDSCode = T2.CDSCode WHERE T2.County = 'Los Angeles' AND T2.Charter = 0 AND CAST(T1.`Free Meal Count (K-12)` AS REAL) * 100 / T1.`Enrollment (K-12)` < 0.18
Aggregate(keys=[], aggs=[COUNT($0)])
  Project($35, id=4)
    Filter(condition=$33 = 'Los Angeles' AND $51 = 0 AND CAST($19 AS FLOAT) * 100 / $18 < 0.18, id=3)
      Join(condition=$0 = $29, type=INNER, id=2)
        Scan(table=frpm, id = 0)
        Scan(table=schools, id = 1)