"""Replay original/augmented saved research through the unchanged production writer after fixing input routing."""
import json, os, shutil, sys
from pathlib import Path
HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[3]
STAGED=Path('/tmp/question-material-rollout-20261115')
mode=sys.argv[1]
assert mode in ('original','fixed')
work=Path('/tmp/question-material-writer-20261115-v3')/mode
work.mkdir(parents=True,exist_ok=False)
for name in ('config','jobs'):shutil.copytree(STAGED/name,work/name)
for name in ('state','logs','output'):(work/name).mkdir()
sys.path.insert(0,str(STAGED));os.chdir(work)
from dotenv import load_dotenv
load_dotenv(ROOT/'.env',override=False)
from web.settings import get_settings
for name in ('config','jobs','state','logs','output'):setattr(get_settings().paths,name+'_dir',str(work/name))
from core import activity,telemetry,report_meta,report_material
activity.use_state_dir(str(work/'state'));telemetry.use_state_dir(str(work/'state'))
from core.video import store,reports,production,audience_strategy,question_material
from web.services import video_autopilot as auto,video_workflow as workflow
store.init_db();workflow.spawn=lambda *a,**k:None
source=Path('/tmp/question-material-smoke-20261115/consumer_illustrated')
result=json.loads((source/'result.json').read_text())
source_path=Path(result['report'])
if not source_path.is_absolute():source_path=source/source_path
receipt=json.loads(next(p for p in source_path.parent.glob(source_path.name+'.audience-*.json') if '.audience-initial-' not in p.name).read_text())
initial=receipt['initial_report']
report=initial if mode=='original' else source_path.read_text()
# Both arms keep the generated research intact; only the existing supplemental appendix differs.
assert source_path.read_text().startswith(initial)
path=work/'output/consumer/2026-11-15_consumer_illustrated.md';path.parent.mkdir(parents=True);path.write_text(report)
material=json.loads((ROOT/'state/acceptance-content-value-20261115/expansion/inputs/consumer_illustrated.json').read_text())['material']
if mode=='fixed':
 material+='\n\n'+'\n\n'.join(s['text'] for t in receipt['question_material']['topics'] for s in t['retrieved_sources'] if s['id'] in t['organization']['used_ids'])
ref=report_material.save(str(path),report,[material])
strategy={'strategy':audience_strategy.MARKER,'channel':'consumer_illustrated'}
if mode=='fixed':strategy['question_material']=question_material.MARKER
report_meta.append_record(str(path.relative_to(work)),'consumer/consumer_illustrated',extra={'audience_strategy':strategy,'material':ref})
reports.record_file(str(path),job='consumer/consumer_illustrated')
key='consumer_illustrated';config=production.get(str(work/'config'),str(work/'jobs'),key)
pick={'production':key,'column':config.recipe,'all':True,'whole':True}
picks=auto.pick_topics({'job':'consumer/consumer_illustrated','report':str(path),'picks':[pick]})
batch=auto.ap.create_run({'job':'consumer/consumer_illustrated','picks':[pick]},picks,started_by='question-writer-replay')
item=batch['items'][0];topic=auto._prepare(item)
assert topic['audience_strategy']=='consumer_illustrated'
assert report_meta.get(topic['from_report'])['material']==ref
if mode=='fixed':assert report_meta.get(topic['from_report'])['audience_strategy']['question_material']==question_material.MARKER
script=workflow.start(item['column_key'],{**topic,'_production':key},'question-writer-replay')
status=workflow.run(script['id'])
assert status=='DRAFT',store.get_script(script['id']).get('error')
version=store.get_version(script['id'],1)
assert version['meta']['audience_strategy']['writer']=='original_with_audience_scope'
if mode=='fixed':assert version['meta']['input_kind']=='report'
(work/'result.json').write_text(json.dumps({'mode':mode,'report':str(path),'topic':topic,'version':version},ensure_ascii=False,indent=2)+'\n')
print(json.dumps({'mode':mode,'status':status,'title':version['title'],'input_kind':version['meta']['input_kind']},ensure_ascii=False),flush=True)
