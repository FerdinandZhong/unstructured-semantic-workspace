#!/usr/bin/env python3
"""Export original Microsoft OLE DOC payloads; optional macOS textutil conversion."""
import hashlib,json,pathlib,shutil,sqlite3,subprocess
r=pathlib.Path(__file__).resolve().parents[1];out=r/'data/native-documents';out.mkdir(exist_ok=True)
d=sqlite3.connect(f'file:{r}/data/adventureworks.sqlite?mode=ro',uri=True);d.row_factory=sqlite3.Row
manifest=[]
for row in d.execute("SELECT * FROM Production_Document WHERE FileExtension='.doc'"):
 b=bytes.fromhex(row['Document']);assert b.startswith(bytes.fromhex('D0CF11E0A1B11AE1'))
 f=out/(row['DocumentNode']+'.doc');f.write_bytes(b)
 item={'document_node':row['DocumentNode'],'title':row['Title'],'file':str(f.relative_to(r)),'sha256':hashlib.sha256(b).hexdigest(),'bytes':len(b),'products':[x[0] for x in d.execute('SELECT ProductID FROM Production_ProductDocument WHERE DocumentNode=?',(row['DocumentNode'],))],'attribution':'Microsoft AdventureWorks sample, MIT; raw/license.txt','text_status':'not_run'}
 if shutil.which('textutil'):
  target=f.with_suffix('.txt');result=subprocess.run(['textutil','-convert','txt','-output',str(target),str(f)],capture_output=True,text=True)
  item['text_status']='converted_not_human_reviewed' if result.returncode==0 else 'failed';item['text_file']=str(target.relative_to(r)) if result.returncode==0 else None
 manifest.append(item)
(r/'metadata/native-documents.json').write_text(json.dumps(manifest,indent=2)+'\n');print(json.dumps({'documents':len(manifest),'converted':sum(x['text_status']=='converted_not_human_reviewed' for x in manifest)}))
