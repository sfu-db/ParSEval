-- Query: SELECT COUNT(CDSCode) FROM schools WHERE City = 'San Joaquin' AND MailState = 'CA' AND StatusType = 'Active'
Aggregate(keys=[], aggs=[COUNT(*)])
  Project($0, id=2)
    Filter(condition=$9 = 'San Joaquin' AND $16 = 'CA' AND $3 = 'Active', id=1)
      Scan(table=schools, id = 0)