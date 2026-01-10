-- Query: SELECT T1.Phone FROM schools AS T1 INNER JOIN satscores AS T2 ON T1.CDSCode = T2.cds ORDER BY CAST(T2.NumGE1500 AS REAL) / T2.NumTstTakr DESC LIMIT 3
Sort(1, dir=['DESCENDING'], offset=0, limit=3)
  Project($17, CAST($59 AS FLOAT) / $55, id=3)
    Join(condition=$0 = $49, type=INNER, id=2)
      Scan(table=schools, id = 0)
      Scan(table=satscores, id = 1)