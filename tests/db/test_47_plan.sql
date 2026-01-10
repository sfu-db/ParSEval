-- Query: SELECT CAST(COUNT(School) AS REAL) / 12 FROM schools WHERE DOC = 52 AND County = 'Alameda' AND strftime('%Y', OpenDate) = '1980'
Project(CAST($0 AS FLOAT) / 12, id=4)
  Aggregate(keys=[], aggs=[COUNT($0)])
    Project($6, id=2)
      Filter(condition=CAST($25 AS INT) = 52 AND $4 = 'Alameda' AND FUNCTION_CALL(STRFTIME, '%Y', CAST($20 AS TIMESTAMP)) = '1980', id=1)
        Scan(table=schools, id = 0)