"""Use the next existing candidate after an isolated model-format failure; do not retry the same topic."""
import json,os,sys
from pathlib import Path
HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[3]
STAGED=Path('/tmp/question-material-rollout-20261115');work=Path('/tmp/question-material-smoke-20261115/ai_tool_tutorials')
sys.path.insert(0,str(STAGED));os.chdir(work)
from dotenv import load_dotenv
load_dotenv(ROOT/'.env',override=False)
from web.settings import get_settings
for name in ('config','jobs','state','logs','output'):setattr(get_settings().paths,name+'_dir',str(work/name))
from core import activity,telemetry,report_meta
activity.use_state_dir(str(work/'state'));telemetry.use_state_dir(str(work/'state'))
from core.video import store
from web.services import video_autopilot as auto,video_workflow as workflow
store.init_db();workflow.spawn=lambda *a,**k:None
batch=auto.ap.get_run(auto.ap.list_runs()[0]['id']);attempts=[]
for item in batch['items'][1:]:
 topic=auto._prepare(item)
 assert topic['fields']['选题入口']=='ai_tutorial'
 assert report_meta.get(topic['from_report'])['material']
 print(json.dumps({'phase':'writing_next_candidate','title':topic['title']},ensure_ascii=False),flush=True)
 script=workflow.start(item['column_key'],{**topic,'_production':'ai_tool_tutorials'},'question-next-candidate')
 status=workflow.run(script['id'])
 attempts.append({'title':topic['title'],'status':status,'script_id':script['id']})
 (work/'next-candidate-attempts.json').write_text(json.dumps(attempts,ensure_ascii=False,indent=2)+'\n')
 if status!='DRAFT':continue
 version=store.get_version(script['id'],1)
 assert version['meta']['audience_strategy']['writer']=='original_with_audience_scope'
 (work/'result.json').write_text(json.dumps({'production':'ai_tool_tutorials','report':topic['from_report'],'topic':topic,'version':version},ensure_ascii=False,indent=2)+'\n')
 print(json.dumps({'status':status,'title':version['title']},ensure_ascii=False),flush=True)
 break
else:raise RuntimeError('No remaining candidate reached DRAFT; keep the recorded failures')
