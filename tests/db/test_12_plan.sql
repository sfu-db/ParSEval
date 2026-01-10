-- Query: SELECT MAX(CAST(T1.`Free Meal Count (Ages 5-17)` AS REAL) / T1.`Enrollment (Ages 5-17)`) FROM frpm AS T1 INNER JOIN satscores AS T2 ON T1.CDSCode = T2.cds WHERE CAST(T2.NumGE1500 AS REAL) / T2.NumTstTakr > 0.3
Aggregate(keys=[], aggs=[MAX($0)])
  Project(CAST($24 AS FLOAT) / $23, id=4)
    Filter(condition=CAST($39 AS FLOAT) / $35 > 0.3, id=3)
      Join(condition=$0 = $29, type=INNER, id=2)
        Scan(table=frpm, id = 0)
        Scan(table=satscores, id = 1)