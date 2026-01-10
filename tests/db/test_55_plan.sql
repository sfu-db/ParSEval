-- Query: SELECT CAST(SUM(CASE WHEN County = 'Colusa' THEN 1 ELSE 0 END) AS REAL) / SUM(CASE WHEN County = 'Humboldt' THEN 1 ELSE 0 END) FROM schools WHERE MailState = 'CA'
Project(CAST($0 AS FLOAT) / $1, id=4)
  Aggregate(keys=[], aggs=[SUM($0), SUM($1)])
    Project(CASE WHEN $4 = 'Colusa' THEN 1 ELSE 0 END, CASE WHEN $4 = 'Humboldt' THEN 1 ELSE 0 END, id=2)
      Filter(condition=$16 = 'CA', id=1)
        Scan(table=schools, id = 0)