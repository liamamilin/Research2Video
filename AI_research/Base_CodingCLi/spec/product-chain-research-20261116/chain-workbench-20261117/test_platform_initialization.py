from pathlib import Path
import sys,tempfile,sqlite3,threading,time
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT/'runtime'))
from core import sqlite_util
from core.douyin_store import DouyinStore
from core.platform_store import PlatformStore
with tempfile.TemporaryDirectory(dir=ROOT/'runtime/tmp') as folder:
 for cls in (DouyinStore,PlatformStore):
  calls=[];original=cls._initialize_schema
  def tracked(self):
   calls.append(self.path);return original(self)
  with patch.object(cls,'_initialize_schema',tracked):
   first=cls(folder);second=cls(folder)
   assert len(calls)==1
   with second.connect() as db:assert db.execute('SELECT count(*) FROM sqlite_master').fetchone()[0]>0
   path=Path(first.path);path.rename(path.with_suffix('.saved.db'))
   replacement=cls(folder)
   assert len(calls)==2
   with replacement.connect() as db:assert db.execute('SELECT count(*) FROM sqlite_master').fetchone()[0]>0
 path=str(Path(folder)/'concurrent.db');count=[]
 def initialize():
  count.append(1)
  with sqlite3.connect(path) as db:db.execute('CREATE TABLE test(value TEXT)')
 with ThreadPoolExecutor(max_workers=4) as pool:
  list(pool.map(lambda _:sqlite_util.initialize_schema_once(path,initialize),range(8)))
 assert len(count)==1
 failure=str(Path(folder)/'failure.db')
 try:sqlite_util.initialize_schema_once(failure,lambda:(_ for _ in ()).throw(ValueError('expected')))
 except ValueError:pass
 assert failure not in sqlite_util._SCHEMA_IDENTITIES
print('PASS: repeated reads skip DDL; replacement reinitializes; concurrent initialization once; failure not cached')
