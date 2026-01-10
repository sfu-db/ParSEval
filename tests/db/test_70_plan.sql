-- Query: SELECT COUNT(School) FROM schools WHERE (StatusType = 'Closed' OR StatusType = 'Active') AND County = 'Alpine'
Aggregate(keys=[], aggs=[COUNT($0)])
  Project($6, id=2)
    Filter(condition=$3 = 'Closed' OR $3 = 'Active' AND $4 = 'Alpine', id=1)
      Scan(table=schools, id = 0)