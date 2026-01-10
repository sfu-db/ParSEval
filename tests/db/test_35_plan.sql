-- Query: SELECT T2.AdmEmail1 FROM frpm AS T1 INNER JOIN schools AS T2 ON T1.CDSCode = T2.CDSCode WHERE T1.`Charter School (Y/N)` = 1 ORDER BY T1.`Enrollment (K-12)` ASC LIMIT 1
Sort(1, dir=['ASCENDING'], offset=0, limit=1)
  Project($70, $18, id=4)
    Filter(condition=$12 = 1, id=3)
      Join(condition=$0 = $29, type=INNER, id=2)
        Scan(table=frpm, id = 0)
        Scan(table=schools, id = 1)