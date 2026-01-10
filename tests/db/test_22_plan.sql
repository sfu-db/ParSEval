-- Query: SELECT sname FROM satscores WHERE cname = 'Contra Costa' AND sname IS NOT NULL ORDER BY NumTstTakr DESC LIMIT 1
Sort(1, dir=['DESCENDING'], offset=0, limit=1)
  Project($2, $6, id=2)
    Filter(condition=$4 = 'Contra Costa' AND $2 IS NOT NULL, id=1)
      Scan(table=satscores, id = 0)