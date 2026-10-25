"""Review a small adaptation edit with the ordinary complete adaptation audits."""
from copy import deepcopy
import json
import os
from pathlib import Path
import sys
OUT=Path(__file__).resolve().parent
RESEARCH=OUT.parents[3]/'AI_research/Base_CodingCLi'
os.chdir(RESEARCH);sys.path.insert(0,str(RESEARCH))
from core.video import store
from core.video.adapt import pipeline
from web.services import video_executor,video_workflow
case=next(c for c in json.loads((OUT/'batch.json').read_text())['cases'] if c['key']=='ai-deep')
s=store.get_script(case['script_id']);assert s['status']=='DRAFT' and not s.get('stage') and s['current_version']==3
old=store.get_version(s['id'],3);sections=deepcopy(old['sections']);changed=0
for sec in sections:
 for p in sec['paragraphs']:
  before=p['text'];p['text']=before.replace('质量门在两阶段合成流程里筛掉不合格的任务和环境；','两阶段合成流程里包含质量门和任务验证器；')
  changed+=before!=p['text']
assert changed==1
(OUT/'revisions/ai-deep-before-v3.json').write_text(json.dumps(old,ensure_ascii=False,indent=2))
vn=video_workflow.save_edit(s['id'],title=old['title'],sections=sections,edited_by='用户授权·深度样本口播复核',base_version=3)
v=store.get_version(s['id'],vn);column=video_executor.column_of(s)
_,sheet=video_workflow._base_adaptation(s,3);style=video_workflow._style_of(old['meta'],column)
config,review_config=video_workflow._llm_configs(None,column)
client,auditor=pipeline._clients(config,review_config,None)
store.update_script(s['id'],stage='修订后的完整三项审校')
problems=[]
with store.beating(store.touch_script,s['id']):
 state=pipeline._audit(client,auditor,v['sections'],old['meta']['plan'],sheet,column,style,problems, previous={'audit': {'issues': [i for i in old['meta']['checks']['issues'] if i['audit']=='fact' and i['source']=='model']}}, changed={'S3P2'})
pipeline._mark_evidence(v['sections'],state['checks']['issues'])
meta={**v['meta'],'checks':state['checks'],'problems':problems,'acceptance_batch':'matrix-recipe-quality-20261023','report_path':s['report_path'],'usage':dict(client.usage),'review_usage':dict(auditor.usage),'manual_edit_reason':'按事实审计收窄质量门职责，使用正常增量事实复审并完整重读全文'}
store.update_version_audit(s['id'],vn,sections=v['sections'],meta=meta)
store.update_script(s['id'],stage=None)
final=store.get_version(s['id'],vn)
(OUT/f'revisions/ai-deep-audited-v{vn}.json').write_text(json.dumps(final,ensure_ascii=False,indent=2))
print(json.dumps({'version':vn,'summary':state['checks']['summary'],'problems':problems},ensure_ascii=False),flush=True)
