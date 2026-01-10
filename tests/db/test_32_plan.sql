-- Query: SELECT CAST(T1.`FRPM Count (K-12)` AS REAL) / T1.`Enrollment (K-12)` FROM frpm AS T1 INNER JOIN schools AS T2 ON T1.CDSCode = T2.CDSCode WHERE T2.SOC = 66 ORDER BY T1.`FRPM Count (K-12)` DESC LIMIT 5
Sort(1, dir=['DESCENDING'], offset=0, limit=5)
  Project(CAST($21 AS FLOAT) / $18, $21, id=4)
    Filter(condition=CAST($56 AS INT) = 66, id=3)
      Join(condition=$0 = $29, type=INNER, id=2)
        Scan(table=frpm, id = 0)
        Scan(table=schools, id = 1)