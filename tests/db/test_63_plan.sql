-- Query: SELECT AdmFName1, AdmLName1, School, City FROM schools WHERE Charter = 1 AND CharterNum = '00D2'
Project($39, $40, $6, $9, id=2)
  Filter(condition=$22 = 1 AND $23 = '00D2', id=1)
    Scan(table=schools, id = 0)