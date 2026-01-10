-- Query: SELECT COUNT(School) FROM schools WHERE strftime('%Y', OpenDate) BETWEEN '2000' AND '2005' AND County = 'Stanislaus' AND FundingType = 'Directly funded'
Aggregate(keys=[], aggs=[COUNT($0)])
  Project($6, id=2)
    Filter(condition=FUNCTION_CALL(STRFTIME, '%Y', CAST($20 AS TIMESTAMP)) >= '2000' AND FUNCTION_CALL(STRFTIME, '%Y', CAST($20 AS TIMESTAMP)) <= '2005' AND $4 = 'Stanislaus' AND $24 = 'Directly funded', id=1)
      Scan(table=schools, id = 0)