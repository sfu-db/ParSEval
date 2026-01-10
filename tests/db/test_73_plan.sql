-- Query: SELECT T1.`FRPM Count (Ages 5-17)` FROM frpm AS T1 INNER JOIN schools AS T2 ON T1.CDSCode = T2.CDSCode WHERE T2.MailStreet = 'PO Box 1040' AND T2.SOCType = 'Youth Authority Facilities'
Project($26, id=4)
  Filter(condition=$41 = 'PO Box 1040' AND $57 = 'Youth Authority Facilities', id=3)
    Join(condition=$0 = $29, type=INNER, id=2)
      Scan(table=frpm, id = 0)
      Scan(table=schools, id = 1)