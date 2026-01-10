-- Query: SELECT County, COUNT(Virtual) FROM schools WHERE (County = 'San Diego' OR County = 'Santa Barbara') AND Virtual = 'F' GROUP BY County ORDER BY COUNT(Virtual) DESC LIMIT 1
Sort(1, dir=['DESCENDING'], offset=0, limit=1)
  Aggregate(keys=[$0], aggs=[COUNT($1)])
    Project($4, $35, id=2)
      Filter(condition=$4 = 'San Diego' OR $4 = 'Santa Barbara' AND $35 = 'F', id=1)
        Scan(table=schools, id = 0)