#!/usr/bin/env python3
"""Pinned Microsoft OLTP data projection. Uses only Python standard library."""
import argparse,csv,datetime,decimal,hashlib,json,pathlib,re,sqlite3,subprocess,concurrent.futures
ROOT=pathlib.Path(__file__).resolve().parents[1]
COMMIT='8fe3fd0c9a825fef3ae6d601e97537b612d7b214'
BASE=f'https://raw.githubusercontent.com/microsoft/sql-server-samples/{COMMIT}/'
SUB='samples/databases/adventure-works/oltp-install-script/'
NAMES='Production.Product Production.ProductModel Production.ProductCategory Production.ProductSubcategory Production.BillOfMaterials Production.WorkOrder Production.WorkOrderRouting Production.ScrapReason Production.ProductInventory Production.Location Production.UnitMeasure Purchasing.Vendor Purchasing.ProductVendor Purchasing.PurchaseOrderHeader Purchasing.PurchaseOrderDetail Purchasing.ShipMethod Sales.Customer Sales.Store Sales.SalesOrderHeader Sales.SalesOrderDetail Person.BusinessEntity Person.Person Person.Address Production.Document Production.ProductDocument'.split()
def dump(path,obj):
 path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(obj,indent=2,ensure_ascii=False)+'\n')
def fetch_one(rel):
 dest=ROOT/'raw'/pathlib.Path(rel).name
 manifest=ROOT/'metadata/source.json'
 expected=None
 if manifest.exists():
  expected=next((f['sha256'] for f in json.loads(manifest.read_text())['files'] if f['file']==dest.name),None)
  if dest.exists() and expected and hashlib.sha256(dest.read_bytes()).hexdigest()!=expected:raise ValueError('source checksum mismatch: '+dest.name)
 if not dest.exists():subprocess.run(['curl','-fsSL','--retry','3',BASE+rel,'-o',str(dest)],check=True)
 if expected and hashlib.sha256(dest.read_bytes()).hexdigest()!=expected:raise ValueError('download checksum mismatch: '+dest.name)
 return {'file':dest.name,'url':BASE+rel,'sha256':hashlib.sha256(dest.read_bytes()).hexdigest(),'bytes':dest.stat().st_size}
def metadata(sql):
 tables=[]
 for m in re.finditer(r'CREATE TABLE \[(\w+)\]\.\[(\w+)\]\((.*?)\n\) ON',sql,re.S):
  name=m[1]+'.'+m[2]
  if name not in NAMES:continue
  cols=[];computed=[]
  for line in m[3].splitlines():
   c=re.match(r'\s*\[(\w+)\]\s+(.*)',line)
   if not c:continue
   if c[2].startswith('AS '):
    computed.append({'name':c[1],'expression':c[2].rstrip(',')})
    typ='nvarchar' if c[1] in ('AccountNumber','SalesOrderNumber') else 'int' if c[1] in ('DocumentLevel','StockedQty') and name!='Purchasing.PurchaseOrderDetail' else 'decimal'
    cols.append({'name':c[1],'source_type':typ,'nullable':True,'description':None,'source_computed_expression':c[2].rstrip(','),'projection':'source export snapshot; not recomputed'})
    continue
   typ=re.match(r'\[?(\w+)\]?(\([^)]*\))?',c[2])
   cols.append({'name':c[1],'source_type':typ[1]+(typ[2] or ''),'nullable':'NOT NULL' not in c[2],'description':None})
  t={'name':name,'sqlite_name':name.replace('.','_'),'columns':cols,'computed_columns_snapshotted':computed,'primary_key':[]}
  imp=re.search(r'BULK INSERT \['+m[1]+r'\]\.\['+m[2]+r"\] FROM '.*?WITH \((.*?)\);",sql,re.S)
  assert imp,name
  t['field_delimiter']=re.search(r"FIELDTERMINATOR\s*=\s*'([^']*)'",imp[1])[1].replace('\\t','\t')
  t['record_delimiter']=re.search(r"ROWTERMINATOR\s*=\s*'([^']*)'",imp[1])[1].replace('0x0a','\n').replace('\\n','\n')
  pk=re.search(r'ALTER TABLE \['+m[1]+r'\]\.\['+m[2]+r'\].*?CONSTRAINT \[PK_[^]]+\] PRIMARY KEY[^\n]*\s*\((.*?)\)',sql,re.S)
  if pk:t['primary_key']=re.findall(r'\[(\w+)\]',pk[1])
  for c in cols:
   typ=c['source_type'].lower().split('(')[0];c['sqlite_type']='INTEGER' if typ in ('int','smallint','tinyint','bigint','bit','flag') else 'TEXT'
  tables.append(t)
 fks=[]
 for a in re.finditer(r'ALTER TABLE \[(\w+)\]\.\[(\w+)\] ADD(.*?)(?:\nGO)',sql,re.S):
  frm=a[1]+'.'+a[2]
  if frm not in NAMES:continue
  for f in re.finditer(r'CONSTRAINT \[([^]]+)\] FOREIGN KEY\s*\((.*?)\) REFERENCES \[(\w+)\]\.\[(\w+)\]\s*\((.*?)\)',a[3],re.S):
   to=f[3]+'.'+f[4];fks.append({'name':f[1],'from_table':frm,'to_table':to,'from_columns':re.findall(r'\[(\w+)\]',f[2]),'to_columns':re.findall(r'\[(\w+)\]',f[5]),'in_scope':to in NAMES})
 for d in re.finditer(r"sp_addextendedproperty\] N'MS_Description', N'((?:[^']|'')*)', N'SCHEMA', \[(\w+)\], N'TABLE', \[(\w+)\], (?:N'COLUMN', \[(\w+)\]|NULL, NULL)",sql):
  for t in tables:
   if t['name']==d[2]+'.'+d[3]:
    if not d[4]:t['description']=d[1].replace("''", "'")
    else:
     for c in t['columns']:
      if c['name']==d[4]:c['description']=d[1].replace("''", "'")
 return {'source_commit':COMMIT,'source_type_aliases':{'Name':'nvarchar(50)','Flag':'bit','AccountNumber':'nvarchar(15)','OrderNumber':'nvarchar(25)','Phone':'nvarchar(25)'},'tables':tables,'foreign_keys':fks,'scope':'25-table projection, not full SQL Server installation','projection_rules':{'computed_columns':'source-export snapshots retained; expressions recorded; not dynamically recomputed','decimal_money':'canonical exact decimal strings in CSV and SQLite TEXT; exact arithmetic uses Python Decimal; CAST to REAL is approximate','datetime':'validated ISO text; SQLite date functions supported','hierarchyid':'opaque source text; no SQL Server methods','xml':'opaque UTF-8 text; no XML query support','varbinary':'opaque source hex text, preserved original bytes; no document rendering','spatial':'opaque source text; no spatial methods','constraints':'source PK/FK checked separately; SQL Server check/default/trigger/index behavior not recreated'}}
def records(raw,field,record):
 text=raw.decode('utf-8-sig').replace('\r\n','\n')
 # Files use LF. Reject partial final record and malformed row counts upstream.
 if not text.endswith(record):raise ValueError('missing final record delimiter')
 return [row.split(field) for row in text[:-len(record)].split(record)]
def convert(value,column):
 if value=='':
  if not column['nullable']:raise ValueError('null in required '+column['name'])
  return None
 typ=column['source_type'].lower().split('(')[0]
 if typ in ('int','smallint','tinyint','bigint','bit','flag'):
  if not re.fullmatch(r'-?\d+',value):raise ValueError('invalid integer')
  n=int(value)
  if typ in ('bit','flag') and n not in (0,1):raise ValueError('invalid boolean')
  return n
 if typ in ('decimal','numeric','money','smallmoney','float','real'):
  d=decimal.Decimal(value)
  if not d.is_finite():raise ValueError('nonfinite numeric')
  return str(d)
 if typ in ('datetime','date','smalldatetime'):
  datetime.datetime.fromisoformat(value)
 return value

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--fetch-only',action='store_true');args=ap.parse_args()
 (ROOT/'raw').mkdir(exist_ok=True)
 initial=[fetch_one(SUB+'instawdb.sql'),fetch_one('license.txt')]
 sql=(ROOT/'raw/instawdb.sql').read_text(encoding='utf-8-sig');meta=metadata(sql)
 assert len(meta['tables'])==25
 with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:files=list(pool.map(fetch_one,[SUB+t['name'].split('.')[1]+'.csv' for t in meta['tables']]))
 dump(ROOT/'metadata/source.json',{'repository':'https://github.com/microsoft/sql-server-samples','commit':COMMIT,'license':'MIT; see raw/license.txt','files':initial+files})
 dump(ROOT/'metadata/schema.json',meta)
 if args.fetch_only:return
 final_dbpath=ROOT/'data/adventureworks.sqlite';final_dbpath.parent.mkdir(exist_ok=True)
 dbpath=ROOT/'data/adventureworks.build.sqlite'
 if dbpath.exists():dbpath.unlink()
 db=sqlite3.connect(dbpath);counts={}
 for t in meta['tables']:
  cols=t['columns'];values=records((ROOT/'raw'/ (t['name'].split('.')[1]+'.csv')).read_bytes(),t['field_delimiter'],t['record_delimiter'])
  rows=[]
  for i,row in enumerate(values,1):
   if len(row)!=len(cols):raise ValueError(f'{t["name"]} row {i}: {len(row)} fields, expected {len(cols)}')
   try:rows.append([convert(v,c) for v,c in zip(row,cols)])
   except Exception as e:raise ValueError(f'{t["name"]} row {i}: {e}') from e
  definitions=[]
  for c in cols:
   typ=c['source_type'].lower().split('(')[0];aff='INTEGER' if typ in ('int','smallint','tinyint','bigint','bit','flag') else 'TEXT'
   c['sqlite_type']=aff;definitions.append('"'+c['name']+'" '+aff)
  db.execute(f'CREATE TABLE "{t["sqlite_name"]}" ({",".join(definitions)})')
  db.executemany(f'INSERT INTO "{t["sqlite_name"]}" VALUES ({",".join("?" for c in cols)})',rows)
  out=ROOT/'data/csv'/ (t['sqlite_name']+'.csv');out.parent.mkdir(exist_ok=True)
  with out.open('w',newline='') as f:w=csv.writer(f);w.writerow([c['name'] for c in cols]);w.writerows(rows)
  counts[t['name']]=len(rows)
 db.commit();dump(ROOT/'metadata/schema.json',meta)
 checks=[]
 byname={t['name']:t for t in meta['tables']}
 for t in meta['tables']:
  keys=t['primary_key'];assert keys,t['name'];group=','.join('"'+k+'"' for k in keys)
  duplicates=db.execute(f'SELECT COUNT(*) FROM (SELECT {group} FROM "{t["sqlite_name"]}" GROUP BY {group} HAVING COUNT(*)>1)').fetchone()[0]
  nulls=db.execute(f'SELECT COUNT(*) FROM "{t["sqlite_name"]}" WHERE '+ ' OR '.join(f'"{k}" IS NULL' for k in keys)).fetchone()[0]
  checks.append({'kind':'primary_key','table':t['name'],'duplicates':duplicates,'nulls':nulls});assert duplicates==nulls==0
  db.execute(f'CREATE UNIQUE INDEX "pk_{t["sqlite_name"]}" ON "{t["sqlite_name"]}" ({group})')
 for f in meta['foreign_keys']:
  if not f['in_scope']:continue
  frm=byname[f['from_table']]['sqlite_name'];to=byname[f['to_table']]['sqlite_name'];join=' AND '.join(f'a."{x}"=b."{y}"' for x,y in zip(f['from_columns'],f['to_columns']));nonnull=' AND '.join(f'a."{x}" IS NOT NULL' for x in f['from_columns'])
  missing=db.execute(f'SELECT COUNT(*) FROM "{frm}" a WHERE {nonnull} AND NOT EXISTS (SELECT 1 FROM "{to}" b WHERE {join})').fetchone()[0]
  checks.append({'kind':'foreign_key','name':f['name'],'unmatched_rows':missing});assert missing==0,(f,missing)
 db.commit();db.close();dbpath.replace(final_dbpath);dump(ROOT/'metadata/data-verification.json',{'date':'2026-11-16','row_counts':counts,'total_rows':sum(counts.values()),'checks':checks,'external_foreign_keys':[f for f in meta['foreign_keys'] if not f['in_scope']]})
 print(json.dumps({'tables':len(counts),'rows':sum(counts.values()),'database':str(final_dbpath)}))
if __name__=='__main__':main()
