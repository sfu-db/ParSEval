-- Query: SELECT T2.School FROM frpm AS T1 INNER JOIN schools AS T2 ON T1.CDSCode = T2.CDSCode WHERE T2.DOC = 31 ORDER BY T1.`Enrollment (K-12)` DESC LIMIT 1
Sort(1, dir=['DESCENDING'], offset=0, limit=1)
  Project($35, $18, id=4)
    Filter(condition=CAST($54 AS INT) = 31, id=3)
      Join(condition=$0 = $29, type=INNER, id=2)
        Scan(table=frpm, id = 0)
        Scan(table=schools, id = 1)