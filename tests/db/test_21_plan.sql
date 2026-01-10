-- Query: SELECT COUNT(CDSCode) FROM frpm WHERE `County Name` = 'Los Angeles' AND `Free Meal Count (K-12)` > 500 AND `Free Meal Count (K-12)` < 700
Aggregate(keys=[], aggs=[COUNT(*)])
  Project($0, id=2)
    Filter(condition=$5 = 'Los Angeles' AND $19 > 500 AND $19 < 700, id=1)
      Scan(table=frpm, id = 0)