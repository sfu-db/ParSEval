from sqlglot.planner import Plan


from sqlglot import parse_one

from sqlglot.executor import execute


sql = """    
    SELECT c.name, CASE WHEN o.amount > 500 THEN 'VIP' ELSE 'REG' END as tier
    FROM customers c
    JOIN cte o ON c.id = o.cust_id OR c.email = o.contact_email
    WHERE c.age > 18 OR c.name = 'John'
    GROUP BY c.name
    HAVING COUNT(*) > 1"""

sql2 = """    
    SELECT c.name, CASE WHEN o.amount > 500 THEN 'VIP' ELSE 'REG' END as tier
    FROM customers c, cte
    WHERE c.age > 18 OR c.name = 'John' and c.id = o.cust_id OR c.email = o.contact_email
    GROUP BY c.name
    HAVING COUNT(*) > 1"""
expr = parse_one(sql2, dialect="sqlite")

print(repr(expr))

print(Plan(expr).root)
