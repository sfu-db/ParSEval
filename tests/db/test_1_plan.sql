-- Query: SELECT `Free Meal Count (Ages 5-17)` / `Enrollment (Ages 5-17)` FROM frpm WHERE `Educational Option Type` = 'Continuation School' AND `Free Meal Count (Ages 5-17)` / `Enrollment (Ages 5-17)` IS NOT NULL ORDER BY `Free Meal Count (Ages 5-17)` / `Enrollment (Ages 5-17)` ASC LIMIT 3
Sort(0, dir=['ASCENDING'], offset=0, limit=3)
  Project($24 / $23, id=2)
    Filter(condition=$10 = 'Continuation School' AND $24 / $23 IS NOT NULL, id=1)
      Scan(table=frpm, id = 0)