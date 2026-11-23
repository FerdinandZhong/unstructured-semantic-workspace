#!/usr/bin/env python3
"""Read-only smoke queries, exact decimal checks, key rejection probes."""
import datetime,decimal,json,pathlib,sqlite3
r=pathlib.Path(__file__).resolve().parents[1];d=sqlite3.connect(f'file:{r}/data/adventureworks.sqlite?mode=ro',uri=True)
assert d.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
assert d.execute('SELECT COUNT(*),SUM(OrderQty) FROM Sales_SalesOrderDetail WHERE ProductID=712 AND SalesOrderID IN (SELECT SalesOrderID FROM Sales_SalesOrderHeader WHERE CustomerID=29705)').fetchone()==(12,113)
assert d.execute("SELECT COUNT(*) FROM Sales_SalesOrderHeader WHERE date(OrderDate) IS NULL OR julianday(DueDate)<julianday(OrderDate)").fetchone()[0]==0
assert d.execute('SELECT typeof(UnitPrice) FROM Sales_SalesOrderDetail LIMIT 1').fetchone()[0]=='text'
lines=d.execute('SELECT OrderQty,UnitPrice,UnitPriceDiscount,LineTotal FROM Sales_SalesOrderDetail').fetchall();D=decimal.Decimal
mismatches=sum(D(str(q))*D(price)*(D(1)-D(discount))!=D(total) for q,price,discount,total in lines)
assert mismatches==0
schema=json.loads((r/'metadata/schema.json').read_text());external=[f for f in schema['foreign_keys'] if not f['in_scope']]
# Meaningful negative probes in an isolated memory database, never mutate the package.
n=sqlite3.connect(':memory:');n.execute('PRAGMA foreign_keys=ON');n.executescript('CREATE TABLE p(id INTEGER PRIMARY KEY);CREATE TABLE c(id INTEGER PRIMARY KEY,pid INTEGER REFERENCES p(id));INSERT INTO p VALUES(1);')
rejections=[]
for sql in ('INSERT INTO p VALUES(1)','INSERT INTO c VALUES(1,999)'):
 try:n.execute(sql)
 except sqlite3.IntegrityError:rejections.append(sql)
assert len(rejections)==2
result={'date':'2026-11-16','integrity_check':'ok','sales_line_exact_formula_checks':len(lines),'formula_mismatches':mismatches,'exploratory_candidate_customer_id':29705,'exploratory_candidate_quantity':113,'exploratory_candidate_order_lines':12,'typed_date_due_order_checks':'all sales order headers pass','decimal_storage':'TEXT exact; decimal.Decimal arithmetic; SQLite SUM on decimal TEXT is approximate and unsupported for exact metrics','negative_checks':['duplicate key rejected in isolated memory fixture','orphan foreign key rejected in isolated memory fixture'],'external_dependency_count':len(external),'application_or_live_checks':'not_run; local data preparation only'}
(r/'metadata/query-verification.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))
