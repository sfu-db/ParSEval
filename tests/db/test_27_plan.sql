-- Query: SELECT T2.School, T1.AvgScrWrite, T2.Phone, strftime('%Y', T2.OpenDate), strftime('%Y', T2.ClosedDate) FROM schools AS T2 LEFT JOIN satscores AS T1 ON T2.CDSCode = T1.cds WHERE strftime('%Y', T2.OpenDate) > '1991' AND strftime('%Y', T2.ClosedDate) < '2000'
Project($6, $58, $17, STRFTIME('%Y', CAST($20 AS TIMESTAMP)), STRFTIME('%Y', CAST($21 AS TIMESTAMP)), id=4)
  Filter(condition=FUNCTION_CALL(STRFTIME, '%Y', CAST($20 AS TIMESTAMP)) > '1991' AND FUNCTION_CALL(STRFTIME, '%Y', CAST($21 AS TIMESTAMP)) < '2000', id=3)
    Join(condition=$0 = $49, type=LEFT, id=2)
      Scan(table=schools, id = 0)
      Scan(table=satscores, id = 1)