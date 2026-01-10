-- Query: SELECT NCESDist FROM schools WHERE SOC = 31
Project($1, id=2)
  Filter(condition=CAST($27 AS INT) = 31, id=1)
    Scan(table=schools, id = 0)