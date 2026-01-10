-- Query: SELECT CAST(SUM(CASE WHEN FundingType = 'Locally funded' THEN 1 ELSE 0 END) AS REAL) * 100 / SUM(CASE WHEN FundingType != 'Locally funded' THEN 1 ELSE 0 END) FROM schools WHERE County = 'Santa Clara' AND Charter = 1
Project(CAST($0 AS FLOAT) * 100 / $1, id=4)
  Aggregate(keys=[], aggs=[SUM($0), SUM($1)])
    Project(CASE WHEN $24 = 'Locally funded' THEN 1 ELSE 0 END, CASE WHEN $24 <> 'Locally funded' THEN 1 ELSE 0 END, id=2)
      Filter(condition=$4 = 'Santa Clara' AND $22 = 1, id=1)
        Scan(table=schools, id = 0)