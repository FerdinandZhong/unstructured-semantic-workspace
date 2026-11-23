import decimal,pathlib,sys,unittest
from unittest.mock import patch
import prepare_data as p
class ParserTests(unittest.TestCase):
 def test_special_delimiters_preserve_embedded_tabs_and_newlines(self):
  self.assertEqual(p.records(b'a\tinside+|multi\nline&|\r\n','+|','&|\n'),[['a\tinside','multi\nline']])
 def test_missing_record_delimiter_fails(self):
  with self.assertRaises(ValueError):p.records(b'a\tb','\t','\n')
 def test_invalid_utf8_fails(self):
  with self.assertRaises(UnicodeDecodeError):p.records(b'\xff\n','\t','\n')
 def test_invalid_integer_and_required_null_fail(self):
  c={'name':'ID','source_type':'int','nullable':False}
  for v in ('1.1','', 'bad'):
   with self.assertRaises(ValueError):p.convert(v,c)
 def test_nonfinite_decimal_fails(self):
  with self.assertRaises(ValueError):p.convert('NaN',{'name':'cost','source_type':'money','nullable':False})
 def test_invalid_date_fails(self):
  with self.assertRaises(ValueError):p.convert('2024-02-30',{'name':'DueDate','source_type':'datetime','nullable':False})
 def test_decimal_precision_retained(self):
  self.assertEqual(p.convert('123456789012345.1234',{'name':'cost','source_type':'money','nullable':False}),'123456789012345.1234')
 def test_source_schema_composite_keys_and_relationships(self):
  m=p.metadata((p.ROOT/'raw/instawdb.sql').read_text());t=next(t for t in m['tables'] if t['name']=='Purchasing.ProductVendor')
  self.assertEqual(t['primary_key'],['ProductID','BusinessEntityID']);self.assertEqual(len(m['tables']),25)
  self.assertTrue(any(f['from_table']=='Sales.SalesOrderDetail' and len(f['from_columns'])==2 for f in m['foreign_keys']))
 def test_corrupted_cached_source_rejected(self):
  import tempfile,json
  with tempfile.TemporaryDirectory() as tmp:
   r=pathlib.Path(tmp);(r/'raw').mkdir();(r/'metadata').mkdir();(r/'raw/x').write_bytes(b'corrupt');(r/'metadata/source.json').write_text(json.dumps({'files':[{'file':'x','sha256':'wrong'}]}))
   with patch.object(p,'ROOT',r):
    with self.assertRaisesRegex(ValueError,'checksum mismatch'):p.fetch_one('x')
if __name__=='__main__':unittest.main()
