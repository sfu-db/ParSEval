-- Query: SELECT Website FROM schools WHERE (AdmFName1 = 'Mike' AND AdmLName1 = 'Larson') OR (AdmFName1 = 'Dante' AND AdmLName1 = 'Alvarez')
Project($19, id=2)
  Filter(condition=$39 = 'Mike' AND $40 = 'Larson' OR $39 = 'Dante' AND $40 = 'Alvarez', id=1)
    Scan(table=schools, id = 0)