-- Query: SELECT GSoffered FROM schools ORDER BY ABS(longitude) DESC LIMIT 1
Sort(1, dir=['DESCENDING'], offset=0, limit=1)
  Project($33, ABS($38), id=1)
    Scan(table=schools, id = 0)