-- Query: SELECT `Free Meal Count (K-12)` / `Enrollment (K-12)` FROM frpm WHERE `County Name` = 'Alameda' ORDER BY (CAST(`Free Meal Count (K-12)` AS REAL) / `Enrollment (K-12)`) DESC LIMIT 1
Sort(1, dir=['DESCENDING'], offset=0, limit=1)
  Project($19 / $18, CAST($19 AS FLOAT) / $18, id=2)
    Filter(condition=$5 = 'Alameda', id=1)
      Scan(table=frpm, id = 0)