-- Query: SELECT GSserved FROM schools WHERE City = 'Adelanto' GROUP BY GSserved ORDER BY COUNT(GSserved) DESC LIMIT 1
Sort(1, dir=['DESCENDING'], offset=0, limit=1)
  Aggregate(keys=[$0], aggs=[COUNT($0)])
    Project($34, id=2)
      Filter(condition=$9 = 'Adelanto', id=1)
        Scan(table=schools, id = 0)