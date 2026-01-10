-- Query: SELECT Phone, Ext, School FROM schools WHERE Zip = '95203-3704'
Project($17, $18, $6, id=2)
  Filter(condition=$10 = '95203-3704', id=1)
    Scan(table=schools, id = 0)