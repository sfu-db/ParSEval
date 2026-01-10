-- Query: SELECT Website FROM schools WHERE County = 'San Joaquin' AND Virtual = 'P' AND Charter = 1
Project($19, id=2)
  Filter(condition=$4 = 'San Joaquin' AND $35 = 'P' AND $22 = 1, id=1)
    Scan(table=schools, id = 0)