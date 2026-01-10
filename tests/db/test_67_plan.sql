-- Query: SELECT COUNT(School) FROM schools WHERE strftime('%Y', ClosedDate) = '1989' AND City = 'San Francisco' AND DOCType = 'Community College District'
Aggregate(keys=[], aggs=[COUNT($0)])
  Project($6, id=2)
    Filter(condition=FUNCTION_CALL(STRFTIME, '%Y', CAST($21 AS TIMESTAMP)) = '1989' AND $9 = 'San Francisco' AND $26 = 'Community College District', id=1)
      Scan(table=schools, id = 0)