-- Query: SELECT School, MailZip FROM schools WHERE AdmFName1 = 'Avetik' AND AdmLName1 = 'Atoian'
Project($6, $15, id=2)
  Filter(condition=$39 = 'Avetik' AND $40 = 'Atoian', id=1)
    Scan(table=schools, id = 0)