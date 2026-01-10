-- Query: SELECT T2.`School Name` FROM satscores AS T1 INNER JOIN frpm AS T2 ON T1.cds = T2.CDSCode WHERE CAST(T2.`Free Meal Count (K-12)` AS REAL) / T2.`Enrollment (K-12)` > 0.1 AND T1.NumGE1500 > 0
Project($18, id=4)
  Filter(condition=CAST($30 AS FLOAT) / $29 > 0.1 AND $10 > 0, id=3)
    Join(condition=$0 = $11, type=INNER, id=2)
      Scan(table=satscores, id = 0)
      Scan(table=frpm, id = 1)