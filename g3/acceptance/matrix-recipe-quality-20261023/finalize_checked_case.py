"""Apply a reviewed batch revision as a new version, then run ordinary audits."""
from copy import deepcopy
import hashlib
from datetime import datetime
import json
import os
from pathlib import Path
import sys

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[2]
RESEARCH = ROOT.parent / "AI_research/Base_CodingCLi"
os.chdir(RESEARCH)
sys.path.insert(0, str(RESEARCH))
from core import provenance, report_meta
from core.video import reports, sources as video_sources, store, steps, writer
from web.services import video_executor, video_workflow

key = sys.argv[1]
edits = json.loads((OUT/"edits"/(sys.argv[2] if len(sys.argv)>2 else key+".json")).read_text())
case = next(x for x in json.loads((OUT/"batch.json").read_text())["cases"] if x["key"]==key)
assert key != "consumer", "control manuscript is preserved"
script = store.get_script(case["script_id"])
assert script and not script.get("stage") and script["status"]=="DRAFT"
old = store.get_version(script["id"],script["current_version"])
audit_dir = OUT/"revisions"
audit_dir.mkdir(exist_ok=True)
before = audit_dir/(key+f"-before-v{old['version']}.json")
assert not before.exists(), "revision already applied; inspect its record before retrying"
before.write_text(json.dumps(old,ensure_ascii=False,indent=2))

checks = [c for c in json.loads((OUT/"source-checks.json").read_text())["checks"] if c["case"]==key]
original_report = writer.report_body((RESEARCH/case["source_report"]).read_text())
packet = f"# 本批样本原网页复核记录\n\n复核日期：{datetime.now().strftime('%Y-%m-%d')}。以下是实际打开原网页后的事实摘要；原研究报告和旧稿保留。\n\n"
for source in checks:
    packet += f"## {source['publisher']}\n{source['facts']}\n成立条件：{source['must_keep']}\n来源：{source['url']}\n\n"
packet += f"## 原报告追溯\n原报告保留于 {case['source_report']}，SHA-256：{hashlib.sha256(original_report.encode()).hexdigest()}。本次修订仅使用上面已经核对的事实与限定，不把旧摘要的缺项当作原文缺失。\n"
titles={row['url']:row['title'] for row in video_sources.report_sources(original_report)}
packet += "\n\n## 本次核验来源清单\n\n| 来源 | 标题 | 日期 | 性质 | 链接 |\n|---|---|---|---|---|\n"
for source in checks:
    packet += f"| {source['publisher']} | {titles.get(source['url'],source['publisher'])} | {source['date']} | 原网页资料 | {source['url']} |\n"
relative = f"state/video_editorial_sources/matrix-recipe-quality-20261023/{key}-checked-v{old['version']+1}.md"
path = RESEARCH/relative
path.parent.mkdir(parents=True,exist_ok=True)
assert not path.exists()
path.write_text(packet)
old_meta = report_meta.get(case["source_report"]) or {}
urls = list(dict.fromkeys(c['url'] for c in checks))
citations = provenance.check(packet,urls)
report_meta.append_record(relative,script.get("research_job") or "",extra={"citation_check":citations,
    "verification_method":"publisher pages opened and read; paraphrased facts and bounds; not independent reproduction",
    "original_report_path":case["source_report"],"checked_source_urls":[c['url'] for c in checks]})
reports.attach(script["id"],relative,created_by="用户授权·样本原文复核")
store.update_script(script["id"],report_path=relative)
sections=[]
for section in edits['sections']:
    sections.append({"title":section['title'],"paragraphs":[{"text":p['text'],"citations":p['citations'],"evidence":"CITED"} for p in section['paragraphs']]})
number=video_workflow.save_edit(script['id'],title=edits['title'],sections=sections,edited_by="用户授权·样本质量修订",base_version=old['version'])
row=store.get_version(script['id'],number)
meta=deepcopy(row['meta'])
meta.update(acceptance_batch="matrix-recipe-quality-20261023",report_path=relative,
            source_review={"checked_source_urls":[c['url'] for c in checks],"packet_sha256":hashlib.sha256(path.read_bytes()).hexdigest(),"original_script_id":case['original_script_id']})
store.update_version_audit(script['id'],number,sections=row['sections'],meta=meta)
steps.log(script['id'],'write','edit',f'v{number}：原网页复核后收窄过度推断，保留真实限定',by="用户授权·质量验收")
print(json.dumps({"case":key,"version":number,"source_packet":relative},ensure_ascii=False),flush=True)
column=video_executor.column_of(store.get_script(script['id']))
from core.llm import LLMClient
from core.video import audit
from core.video.adapt import review
from types import SimpleNamespace
column=video_executor.column_of(store.get_script(script['id']))
assert column.writer != 'adapt'
config,rc=video_workflow._llm_configs(None,column)
client=LLMClient(rc or config)
row=store.get_version(script['id'],number)
written=SimpleNamespace(title=row['title'],sections=row['sections'],meta=row['meta'],length_check=row['length_check'])
store.update_script(script['id'],stage='最终稿完整事实与听感复审')
with store.beating(store.touch_script,script['id']):
    checks,note=audit.audit(client,written.sections,packet,rule_issues=review.rules(written.sections,column=column,target_minutes=column.target_minutes),column_style=column.script_style,audience=column.audience,style_severity=review.WARN)
    checks=video_workflow._current_copy_checks(checks,written.sections,written.meta)
    audited={'checks':checks,'problems':[note] if note else [],'audit_usage':dict(client.usage)}
    audit_meta=video_workflow._revise_short(script['id'],written,packet,column,config,audited,allow_editorial=False)
store.update_version_audit(script['id'],number,sections=written.sections,meta={**written.meta,**audit_meta},title=written.title,length_check=written.length_check)
store.update_script(script['id'],stage=None)
status='DRAFT'
final=store.get_version(script['id'],number)
(audit_dir/(key+f'-audited-v{number}.json')).write_text(json.dumps(final,ensure_ascii=False,indent=2))
print(json.dumps({"case":key,"status":status,"version":number,"summary":final['meta']['checks']['summary']},ensure_ascii=False),flush=True)
