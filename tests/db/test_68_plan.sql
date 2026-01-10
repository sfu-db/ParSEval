-- Query: SELECT County FROM schools WHERE strftime('%Y', ClosedDate) BETWEEN '1980' AND '1989' AND StatusType = 'Closed' AND SOC = 11 GROUP BY County ORDER BY COUNT(School) DESC LIMIT 1
Sort(1, dir=['DESCENDING'], offset=0, limit=1)
  Aggregate(keys=[$0], aggs=[COUNT($1)])
    Project($4, $6, id=2)
      Filter(condition=FUNCTION_CALL(STRFTIME, '%Y', CAST($21 AS TIMESTAMP)) >= '1980' AND FUNCTION_CALL(STRFTIME, '%Y', CAST($21 AS TIMESTAMP)) <= '1989' AND $3 = 'Closed' AND CAST($27 AS INT) = 11, id=1)
        Scan(table=schools, id = 0)