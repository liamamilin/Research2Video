"""Run the existing adaptation pipeline again with checked primary-source facts."""
import hashlib
import json
import os
from pathlib import Path
import sys
import time

OUT=Path(__file__).resolve().parent
RESEARCH=OUT.parents[3]/"AI_research/Base_CodingCLi"
os.chdir(RESEARCH)
sys.path.insert(0,str(RESEARCH))
from core import provenance,report_meta
from core.video import reports,store
from web.services import video_executor

case=next(c for c in json.loads((OUT/"batch.json").read_text())["cases"] if c['key']=='ai-deep')
script=store.get_script(case['script_id'])
assert script['status']=='DRAFT' and not script.get('stage') and script['current_version']==1
before=store.get_version(script['id'],1)
checks=[c for c in json.loads((OUT/"source-checks.json").read_text())['checks'] if c['case']=='ai-deep']
packet='# WildAWM：本批实片验收的原文核验摘要\n\n复核日期：2026-10-23。仅记录实际打开原始论文和官方仓库后的事实；原报告保留，不把摘要遗漏当作原文未披露。\n\n'
for c in checks:packet+=f"## {c['publisher']}\n{c['facts']}\n限定：{c['must_keep']}\n来源：{c['url']}\n\n"
packet+='## 可用于讲述的事实关系\n网页、MCP工具和命令行共享同一世界状态；世界随虚构人格和日期演化。任务与验证器共同生成，执行和反事实检查用于筛选。论文作者报告了训练后的跨基准提升，这些成绩有具体评测设置，不等于任意真实部署都能复现。仓库提供已发布环境、模型及合成/评测路径，安装依赖包括Python 3.12、Docker和Node.js。复现门槛包含工程环境，不据此断言模型调用没有成本。\n'
packet+='## 来源清单\n\n| 标题 | 日期 | 性质 | 链接 |\n|---|---|---|---|\n'
for c in checks:packet+=f"| {'WildAWM 原始论文' if 'arxiv' in c['url'] else 'WildAWM 官方项目'} | {c['date']} | 一手 | {c['url']} |\n"
relative='state/video_editorial_sources/matrix-recipe-quality-20261023/ai-deep-checked-v2.md'
path=RESEARCH/relative;path.parent.mkdir(parents=True,exist_ok=True);assert not path.exists();path.write_text(packet)
urls=[c['url'] for c in checks]
report_meta.append_record(relative,'',extra={'citation_check':provenance.check(packet,urls),'verification_method':'actual original paper and repository opened and read; paraphrased facts, no independent reproduction','original_report_path':case['source_report']})
reports.attach(script['id'],relative,created_by='用户授权·深度样本原文复核')
store.update_script(script['id'],report_path=relative)
records=OUT/'revisions';records.mkdir(exist_ok=True)
(records/'ai-deep-before-v1.json').write_text(json.dumps(before,ensure_ascii=False,indent=2))
video_executor.rerun(script['id'],'write','用户授权·深度样本原文复核重写')
last=None
while True:
    row=store.get_script(script['id'])
    state=(row['status'],row.get('stage'),row['current_version'])
    if state!=last:print(json.dumps({'case':'ai-deep','status':state[0],'stage':state[1],'version':state[2]},ensure_ascii=False),flush=True);last=state
    if row['status'] in ('FAILED','PLAN_READY') or (row['status']=='DRAFT' and not row.get('stage')):break
    time.sleep(2)
assert row['current_version']>1,row.get('error')
final=store.get_version(script['id'],row['current_version'])
(records/f"ai-deep-rewritten-v{row['current_version']}.json").write_text(json.dumps(final,ensure_ascii=False,indent=2))
print(json.dumps({'version':row['current_version'],'summary':final['meta']['checks']['summary']},ensure_ascii=False),flush=True)
