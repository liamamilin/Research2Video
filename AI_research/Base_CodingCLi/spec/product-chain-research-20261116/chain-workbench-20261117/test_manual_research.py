from pathlib import Path
import sys,json
from types import SimpleNamespace
from unittest.mock import patch
ROOT=Path(__file__).resolve().parent;sys.path.insert(0,str(ROOT/'runtime'))
from web.services import chain_runner as runner
saved={}
def put(row):saved.update(row)
detail={'key':'only_research','name':'只更新报告','job':'research','outputs_detail':[{'key':'output','auto':False,'errors':[]}],'runs':[],'scripts':[]}
with patch.object(runner.chains,'detail',return_value=detail),patch.object(runner.chains,'active_for_job',return_value=[]),patch.object(runner.TaskRegistry,'is_running',return_value=False),patch.object(runner.chains,'paths',return_value=SimpleNamespace(config_dir='config',jobs_dir='jobs')),patch.object(runner,'put',side_effect=put),patch.object(runner,'get',side_effect=lambda _rid:saved.copy()),patch.object(runner,'start_job',return_value=SimpleNamespace(task_id='isolated-test')) as start:
 row=runner.start('only_research',{}, {'username':'test'})
 assert row['outputs']==[] and row['stage']=='RESEARCH' and row['task_id']=='isolated-test'
 start.assert_called_once()
(ROOT/'manual-research-tests.json').write_text(json.dumps({'paused_outputs_allow_manual_report_only_run':True},indent=2))
print('Manual research continues with all video outputs paused')
