"""Frozen-source replay through research, selection and existing authoring; no publication."""
import json
import os
from pathlib import Path
import re
import shutil
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
STAGED = Path('/tmp/content-expand-20261115')


def main():
    key = sys.argv[1]
    work = Path('/tmp/content-expand-smoke-20261115')/key
    work.mkdir(parents=True, exist_ok=False)
    for name in ('config','jobs'):
        shutil.copytree(STAGED/name,work/name)
    for name in ('state','logs'):
        (work/name).mkdir()
    frozen=json.loads((ROOT/'state/acceptance-content-value-20261115/expansion/inputs'/(key+'.json')).read_text())
    sys.path.insert(0,str(STAGED));os.chdir(work)
    from dotenv import load_dotenv
    load_dotenv(ROOT/'.env',override=False)
    from web.settings import get_settings
    paths=get_settings().paths
    for name in ('config','jobs','state','logs','output'):
        setattr(paths,name+'_dir',str(work/name))
    from core import activity,telemetry,ai_dossier_sources,sources
    activity.use_state_dir(str(work/'state'));telemetry.use_state_dir(str(work/'state'))
    from core.engine import ResearchEngine
    from core.search import SearchResult
    from core.video import audience_strategy as strategy,store,reports,production
    from web.services import video_autopilot as auto,video_workflow as workflow
    store.init_db()
    workflow.spawn=lambda *a,**k:None
    original=work/frozen['report_path'];original.parent.mkdir(parents=True,exist_ok=True)
    original.write_text(frozen['report'])
    engine=ResearchEngine(str(work/'config'),str(work/'jobs'),workspace_dir=str(work))
    # Freeze only acquisition: all model research/selection/writing calls are real.
    items=[SearchResult(p['title'],p['url'],excerpts=[p['text']]) for p in strategy.packets(frozen['material']) if p['url']]
    engine._fetch_sources_strict=lambda job:(frozen['material'],(items,{}))
    engine._notify=lambda *a,**kw:None
    ai_dossier_sources.wanted=lambda job:False  # Existing complete frozen materials; no extra network acquisition.
    if key.endswith('_illustrated'):
        ideas=[]
        for line in frozen['material'].splitlines():
            m=re.match(r'- (.+?)（(https?://[^）]+)）$',line)
            if m:ideas.append(SearchResult(m[1],m[2]))
        sources.fetch_all=lambda *a,**kw:ideas
    print(json.dumps({'production':key,'phase':'research','frozen_report':frozen['report_path']},ensure_ascii=False),flush=True)
    path=engine.run_job(frozen['job'])
    if not path:raise RuntimeError('Research failed; see isolated state and log')
    reports.record_file(str(path),job=frozen['job'])
    config=production.get(paths.config_dir,paths.jobs_dir,key)
    pick={'production':key,'column':config.recipe,'all':True}
    pick.update({'whole':True} if config.split=='whole' else {'source':config.source})
    if config.content_mode=='episode':pick['content_mode']='episode'
    picks=auto.pick_topics({'job':frozen['job'],'report':str(path),'picks':[pick]})
    if not picks:raise RuntimeError('No production candidate')
    batch=auto.ap.create_run({'job':frozen['job'],'picks':[pick]},picks,started_by='expansion-smoke')
    item=batch['items'][0];topic=auto._prepare(item)
    channel=strategy.JOBS[frozen['job']]
    assert strategy.topic_channel(topic)==channel
    print(json.dumps({'production':key,'phase':'writing','audience':topic.get('fields',{}).get('目标受众')},ensure_ascii=False),flush=True)
    script=workflow.start(item['column_key'],{**topic,'_production':key},'expansion-smoke')
    status=workflow.run(script['id'])
    if status!='DRAFT':raise RuntimeError(str(store.get_script(script['id']).get('error')))
    version=store.get_version(script['id'],1)
    assert version['meta']['audience_strategy']['channel']==channel
    (work/'result.json').write_text(json.dumps({'production':key,'report':str(path),'topic':topic,'version':version},ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'production':key,'status':status,'title':version['title'],
                      'strategy':version['meta']['audience_strategy']},ensure_ascii=False),flush=True)


if __name__=='__main__':
    main()
