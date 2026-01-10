-- Query: SELECT CAST(SUM(CASE WHEN DOC = 54 THEN 1 ELSE 0 END) AS REAL) / SUM(CASE WHEN DOC = 52 THEN 1 ELSE 0 END) FROM schools WHERE StatusType = 'Merged' AND County = 'Orange'
Project(CAST($0 AS FLOAT) / $1, id=4)
  Aggregate(keys=[], aggs=[SUM($0), SUM($1)])
    Project(CASE WHEN CAST($25 AS INT) = 54 THEN 1 ELSE 0 END, CASE WHEN CAST($25 AS INT) = 52 THEN 1 ELSE 0 END, id=2)
      Filter(condition=$3 = 'Merged' AND $4 = 'Orange', id=1)
        Scan(table=schools, id = 0)