-- Query: SELECT AVG(T1.NumTstTakr) FROM satscores AS T1 INNER JOIN schools AS T2 ON T1.cds = T2.CDSCode WHERE strftime('%Y', T2.OpenDate) = '1980' AND T2.County = 'Fresno'
Aggregate(keys=[], aggs=[AVG($0)])
  Project($6, id=4)
    Filter(condition=FUNCTION_CALL(STRFTIME, '%Y', CAST($31 AS TIMESTAMP)) = '1980' AND $15 = 'Fresno', id=3)
      Join(condition=$0 = $11, type=INNER, id=2)
        Scan(table=satscores, id = 0)
        Scan(table=schools, id = 1)