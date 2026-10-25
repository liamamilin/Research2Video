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
s=store.get_script(case['script_id']);assert s['status']=='DRAFT' and not s.get('stage') and s['current_version']==2
old=store.get_version(s['id'],2);sections=deepcopy(old['sections']);changed=0
for sec in sections:
 for p in sec['paragraphs']:
  before=p['text'];p['text']=before.replace('它们是论文作者在自家协议下的自述结果，报告里没有记录第三方独立复测。','它们是论文作者在所述协议下报告的结果。').replace('所以它不只是实验室内部的自测。','这些外部基准的名称、任务数量和指标，让评测范围更清楚。')
  changed+=before!=p['text']
assert changed==2
(OUT/'revisions/ai-deep-before-v2.json').write_text(json.dumps(old,ensure_ascii=False,indent=2))
vn=video_workflow.save_edit(s['id'],title=old['title'],sections=sections,edited_by='用户授权·深度样本口播复核',base_version=2)
v=store.get_version(s['id'],vn);column=video_executor.column_of(s)
_,sheet=video_workflow._base_adaptation(s,2);style=video_workflow._style_of(old['meta'],column)
config,review_config=video_workflow._llm_configs(None,column)
client,auditor=pipeline._clients(config,review_config,None)
store.update_script(s['id'],stage='修订后的完整三项审校')
problems=[]
with store.beating(store.touch_script,s['id']):
 state=pipeline._audit(client,auditor,v['sections'],old['meta']['plan'],sheet,column,style,problems)
pipeline._mark_evidence(v['sections'],state['checks']['issues'])
meta={**v['meta'],'checks':state['checks'],'problems':problems,'acceptance_batch':'matrix-recipe-quality-20261023','report_path':s['report_path'],'usage':dict(client.usage),'review_usage':dict(auditor.usage),'manual_edit_reason':'删除无法由摘要证明的独立复测缺失断言，保留作者自报与真实评测范围'}
store.update_version_audit(s['id'],vn,sections=v['sections'],meta=meta)
store.update_script(s['id'],stage=None)
final=store.get_version(s['id'],vn)
(OUT/f'revisions/ai-deep-audited-v{vn}.json').write_text(json.dumps(final,ensure_ascii=False,indent=2))
print(json.dumps({'version':vn,'summary':state['checks']['summary'],'problems':problems},ensure_ascii=False),flush=True)
