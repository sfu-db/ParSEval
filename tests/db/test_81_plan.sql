-- Query: SELECT T2.City, T1.`Low Grade`, T1.`School Name` FROM frpm AS T1 INNER JOIN schools AS T2 ON T1.CDSCode = T2.CDSCode WHERE T2.State = 'CA' ORDER BY T2.Latitude ASC LIMIT 1
Sort(3, dir=['ASCENDING'], offset=0, limit=1)
  Project($38, $16, $7, $66, id=4)
    Filter(condition=$40 = 'CA', id=3)
      Join(condition=$0 = $29, type=INNER, id=2)
        Scan(table=frpm, id = 0)
        Scan(table=schools, id = 1)