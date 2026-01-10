-- Query: SELECT COUNT(School) FROM schools WHERE DOC = 52 AND Charter = 1 AND City = 'Hickman'
Aggregate(keys=[], aggs=[COUNT($0)])
  Project($6, id=2)
    Filter(condition=CAST($25 AS INT) = 52 AND $22 = 1 AND $9 = 'Hickman', id=1)
      Scan(table=schools, id = 0)