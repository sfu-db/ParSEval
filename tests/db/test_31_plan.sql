-- Query: SELECT CAST(`Free Meal Count (K-12)` AS REAL) / `Enrollment (K-12)` FROM frpm ORDER BY `Enrollment (K-12)` DESC LIMIT 9, 2
Sort(1, dir=['DESCENDING'], offset=9, limit=2)
  Project(CAST($19 AS FLOAT) / $18, $18, id=1)
    Scan(table=frpm, id = 0)