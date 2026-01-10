-- Query: SELECT T1.sname, T2.`Charter Funding Type` FROM satscores AS T1 INNER JOIN frpm AS T2 ON T1.cds = T2.CDSCode WHERE T2.`District Name` LIKE 'Riverside%' GROUP BY T1.sname, T2.`Charter Funding Type` HAVING CAST(SUM(T1.AvgScrMath) AS REAL) / COUNT(T1.cds) > 400
Project($0, $1, id=7)
  Having(condition=CAST($2 AS FLOAT) / $3 > 400, id=6)
    Aggregate(keys=[$0, $1], aggs=[SUM($2), COUNT(*)])
      Project($2, $25, $8, $0, id=4)
        Filter(condition=$17 LIKE 'Riverside%', id=3)
          Join(condition=$0 = $11, type=INNER, id=2)
            Scan(table=satscores, id = 0)
            Scan(table=frpm, id = 1)