"""Collect completed real-output evidence; never turn a pending review into a pass."""
import hashlib
import json
import os
from pathlib import Path
import sys
OUT=Path(__file__).resolve().parent
RESEARCH=OUT.parents[3]/'AI_research/Base_CodingCLi'
os.chdir(RESEARCH)
sys.path.insert(0,str(RESEARCH))
from core.video import store
from core.video.policy import is_core_issue
from web.services import video_executor,video_workflow
batch=json.loads((OUT/'batch.json').read_text())
rows=[]
for c in batch['cases']:
 q=json.loads((OUT/'verified'/c['key']/'verification.json').read_text())
 assert q['technical_status']=='PASS' and q['manual_content_status']=='PASS',c['key']
 s=store.get_script(c['script_id']);v=store.get_version(s['id'],s['current_version'])
 assert not s.get('stage') and v['version']==q['version']
 checks=video_workflow.rule_checks(v,video_executor.column_of(s))
 assert checks['summary']['passed'] and not any(is_core_issue(i) for i in checks['issues'])
 assert not any(p.get('evidence')=='NEEDS_REVIEW' for sec in v['sections'] for p in sec['paragraphs'])
 before=json.loads((OUT/'originals'/(c['key']+'.json')).read_text())['version']
 assert store.get_version(c['original_script_id'],c['original_version'])==before
 ir=json.loads((Path(q['video']).parent/'video-ir.json').read_text())
 form=ir.get('presentation',{}).get('preset') or 'process_diagram'
 rows.append({**{k:q[k] for k in ('case','title','script_id','version','render_id','job_id','video','duration_seconds','scenes','caption_mode','caption_cues','max_caption_characters','max_caption_seconds','technical_status','manual_content_status')},'recipe':c['recipe'],'matrix_production':c['production'],'actual_form':form,'fact_core_issues':0,'pending_review_paragraphs':0,'original_version_unchanged':True,'qa':str(OUT/'verified'/c['key']/'verification.json'),'contact_sheet':q['contact_sheet'],'manual_notes':q.get('manual_notes',[])})
activation=json.loads((OUT/'backend-activation.json').read_text());assert activation['idle_verified'] and activation['live']
research_log=(OUT/'research-final.log').read_text();auto_log=(OUT/'autovideo-final-current.log').read_text()
assert 'failed' not in research_log and 'failed' not in auto_log
files=['g2/card_editorial.py','g2/caption_alignment.py','g2/run_g2.py','g1/renderer/src/Root.tsx']
code={str(OUT.parents[2]/name):hashlib.sha256((OUT.parents[2]/name).read_bytes()).hexdigest() for name in files}
for name in ['core/video/audience_copy.py','core/video/adapt/review.py','core/video/credibility.py','web/services/video_workflow.py']:
 code[str(RESEARCH/name)]=hashlib.sha256((RESEARCH/name).read_bytes()).hexdigest()
result={'batch':batch['batch'],'dates':['2026-10-23','2026-10-24'],'status':'representative samples technically passed and manually inspected','coverage':{'scripts':8,'matrices':6,'existing_recipe_families':4,'forms':sorted({r['actual_form'] for r in rows}),'uncovered_recipes':batch['uncovered_recipes'],'all_matrices_and_recipes_claimed':False},'fixes':['原文缺失断言进入共同事实修复与出片检查；旧审校成功不能绕过','否定与能力限定完整提炼，媒体转述、研究估计和研究者解释保留标签','信息动效、流程图和快速卡片共享独立短句字幕轨道，不改变旁白与动画时间线','字幕绑定原文、时间线及音轨哈希；换声音和倍速必须重新对齐','共用写稿与审校规则禁止把解释性假设和补充的方法细节冒充来源事实'],'samples':rows,'source_verification':'Publisher and primary pages opened and read; not independent experimental reproduction','manual_review_scope':'Current manuscript against checked sources plus actual decoded frame per scene. Automated forced alignment, exact text/time coverage and full decode. Not a complete human listening of every audio track.','manual_full_audio_listening':False,'old_outputs_preserved':True,'published_to_video_platforms':False,'new_features_added':False,'known_validation_limit':'Historical frozen whole-workspace release manifest differs from current working directory; failure recorded separately, manifest not regenerated. One pre-existing skip and this manifest test deselected in final AutoVideo regression.','backend_activation':activation,'code_sha256':code,'test_logs':{'research':str(OUT/'research-final.log'),'autovideo':str(OUT/'autovideo-final-current.log'),'full_manifest_failure':str(OUT/'autovideo-regression-final.log')}}
(OUT/'acceptance.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
print(json.dumps({'samples':len(rows),'forms':result['coverage']['forms'],'all_pass':True,'report':str(OUT/'acceptance.json')},ensure_ascii=False))
