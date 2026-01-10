-- Query: SELECT COUNT(*) FROM schools WHERE CharterNum = '00D4' AND MailCity = 'Hickman'
Aggregate(keys=[], aggs=[COUNT(*)])
  Project(0, id=2)
    Filter(condition=$23 = '00D4' AND $14 = 'Hickman', id=1)
      Scan(table=schools, id = 0)