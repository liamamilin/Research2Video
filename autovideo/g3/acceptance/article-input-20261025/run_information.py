"""Real Word import and authoring through the same route/service functions as the UI."""
import json,os,sys,time
from pathlib import Path
OUT=Path(__file__).resolve().parent
RESEARCH=OUT.parents[3]/'AI_research/Base_CodingCLi'
os.chdir(RESEARCH);sys.path.insert(0,str(RESEARCH))
from web.routes.video_articles import CreateIn,create_article
from core.video import articles,store
from web.settings import get_settings
cases=json.loads((OUT/'cases.json').read_text())
record=OUT/'information-script.json'
if record.exists():
 script=store.get_script(json.loads(record.read_text())['id'])
else:
 imported=articles.save_import(get_settings().paths.state_dir,'information.docx',(OUT/'information.docx').read_bytes())
 assert imported['text']==(OUT/'information.txt').read_text()
 result=create_article(CreateIn(text=imported['text'],import_id=imported['id'],column='topic_digest',production='science_daily',**cases['information']),{'username':'admin'})
 script=store.get_script(result['id'])
 record.write_text(json.dumps({'id':script['id'],'import_id':imported['id'],'entry':'same Word parser and article create handler as the authenticated upload API'},ensure_ascii=False,indent=2)+'\n')
print(json.dumps({'id':script['id'],'mode':'adapt','matrix_config':'science_daily'}),flush=True)
last=None
while True:
 script=store.get_script(script['id']);state=(script['status'],script.get('stage'))
 if state!=last:print(json.dumps({'status':state[0],'stage':state[1],'error':script.get('error')},ensure_ascii=False),flush=True);last=state
 if script['status'] in ('DRAFT','FAILED') and not script.get('stage'):break
 time.sleep(2)
version=store.get_version(script['id'],script['current_version']) if script['current_version'] else None
(OUT/'information-version.json').write_text(json.dumps(version,ensure_ascii=False,indent=2)+'\n')
assert script['status']=='DRAFT' and not script.get('error'),script.get('error')
assert version['meta']['checks']['fidelity_checked']
print(json.dumps({'done':True,'title':version['title'],'checks':version['meta']['checks']['summary']},ensure_ascii=False),flush=True)
