from pathlib import Path
import sys,sqlite3,json
from types import SimpleNamespace
from contextlib import nullcontext
from unittest.mock import patch
ROOT=Path(__file__).resolve().parent;sys.path.insert(0,str(ROOT/'runtime'))
from web.services import video_render as v
class InlineThread:
 def __init__(self,target,**kwargs):self.target=target
 def start(self):self.target()
with patch.object(v,'process',side_effect=[sqlite3.OperationalError('database is locked'),'COMPLETED']) as process,patch.object(v.threading,'Thread',InlineThread),patch.object(v.time,'sleep'):
 v.start('isolated-test')
 assert process.call_count==2
 assert 'isolated-test' not in v._running
with patch.object(v,'get_settings',return_value=SimpleNamespace(paths=SimpleNamespace(state_dir=str(ROOT/'runtime/state')))),patch.object(v,'file_lock',return_value=nullcontext()),patch.object(v.store,'beating',return_value=nullcontext()),patch.object(v.store,'get_render',return_value={}),patch.object(v,'_timing',side_effect=sqlite3.OperationalError('database is locked')),patch.object(v,'_fail') as fail:
 try:v.process('isolated-test',client=object())
 except sqlite3.OperationalError:pass
 else:raise AssertionError('Transient DB error was swallowed')
 fail.assert_not_called()
assert not v._database_busy(sqlite3.OperationalError('no such table'))
(ROOT/'render-following-tests.json').write_text(json.dumps({'busy_database_retries_same_render':True,'no_false_failure':True,'no_new_remote_submission':True},indent=2))
print('Database contention preserves the render and resumes the same remote job')
