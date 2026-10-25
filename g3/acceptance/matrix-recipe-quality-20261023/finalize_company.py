"""Recheck the human-reviewed final company copy without another story proposal."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace
OUT=Path(__file__).resolve().parent
RESEARCH=OUT.parents[3]/'AI_research/Base_CodingCLi';os.chdir(RESEARCH);sys.path.insert(0,str(RESEARCH))
from core.llm import LLMClient
from core.video import audit,store
from core.video.adapt import review
from web.services import video_executor,video_workflow
s=store.get_script('5a35f4c46d924cdaa0f14cd387134e59');assert s['current_version']==2 and s['status']=='DRAFT' and not s.get('stage')
old=store.get_version(s['id'],2);sections=deepcopy(old['sections'])
ps=[p for sec in sections for p in sec['paragraphs']];assert len(ps)==7
ps[0]['text']='据 Fast Company 报道，谷歌和 Voltus 签订了一份吉瓦级的需求响应协议。这类虚拟电厂调节的是用电需求，并不是按同样规模新建一座发电站。这个规模按可以聚合的用电需求算，和新增发电装机容量的含义不同。'
ps[1]['text']='所谓需求侧的虚拟电厂，可以把分散的用电需求组织起来，在电网需要时减少或错开一部分用电。这里的调节能力来自用电方式的变化，和新建一台发电机组增加供给，是两种不同的机制。'
ps[-1]['text']='这笔交易的看点，是让用电大户也参与需求调节。它能提供多少帮助，需要放到实际运行条件下观察。不能只凭合同规模，就把调节能力当成新增发电量。'
(OUT/'revisions/company-before-v2.json').write_text(json.dumps(old,ensure_ascii=False,indent=2))
vn=video_workflow.save_edit(s['id'],title='吉瓦级虚拟电厂，调的是用电',sections=sections,edited_by='用户授权·样本最终事实复核',base_version=2)
v=store.get_version(s['id'],vn);meta={**v['meta'],'source_review':old['meta']['source_review'],'report_path':s['report_path'],'acceptance_batch':'matrix-recipe-quality-20261023'}
written=SimpleNamespace(title=v['title'],sections=v['sections'],meta=meta,length_check=v['length_check'])
column=video_executor.column_of(s);config,rc=video_workflow._llm_configs(None,column);client=LLMClient(rc or config)
store.update_script(s['id'],stage='最终稿完整事实与听感复审')
with store.beating(store.touch_script,s['id']):
 checks,note=audit.audit(client,written.sections,Path(s['report_path']).read_text(),rule_issues=review.rules(written.sections,column=column,target_minutes=column.target_minutes),column_style=column.script_style,audience=column.audience,style_severity=review.WARN)
 checks=video_workflow._current_copy_checks(checks,written.sections,meta)
 audit_meta={'checks':checks,'problems':[note] if note else [],'audit_usage':dict(client.usage)}
 final_meta=video_workflow._revise_short(s['id'],written,Path(s['report_path']).read_text(),column,config,audit_meta,allow_editorial=False)
store.update_version_audit(s['id'],vn,sections=written.sections,meta={**meta,**final_meta,'manual_edit_reason':'补充明确报道归属，删去燃料和用电恢复的绝对化类比，保持需求调节与发电装机的区别'},title=written.title,length_check=written.length_check)
store.update_script(s['id'],stage=None)
final=store.get_version(s['id'],vn);(OUT/f'revisions/company-audited-v{vn}.json').write_text(json.dumps(final,ensure_ascii=False,indent=2))
print(json.dumps({'version':vn,'summary':final['meta']['checks']['summary']},ensure_ascii=False),flush=True)
