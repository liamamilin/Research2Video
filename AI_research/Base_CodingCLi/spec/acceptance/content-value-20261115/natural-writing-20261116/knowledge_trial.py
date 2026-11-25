"""Compare knowledge research prompts with identical automatically obtained inputs.

Stops after the first research response, before enrichment and downstream writing.
This is a research-stage experiment, not an end-to-end production claim.
"""
from __future__ import annotations
import argparse
from dataclasses import asdict, replace
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import time

ROOT = Path(__file__).resolve().parents[4]
HERE = Path(__file__).resolve().parent
BASE = ROOT/'state/acceptance-content-value-20261115/natural-writing-20261116/knowledge-trial'


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n')


def freeze():
    sys.path.insert(0,str(ROOT))
    from core import knowledge_job, sources
    from core.config import load_job
    def one(domain):
        path=BASE/'inputs'/f'{domain}.json'
        if path.exists(): raise FileExistsError('Preserve frozen inputs')
        job=load_job(str(ROOT/'jobs'),f'{domain}/{domain}_illustrated')
        titles=knowledge_job.past_titles(job,str(ROOT))
        specs=sources.resolve(list(job['sources']),str(ROOT/'config'))
        ideas=sources.fetch_all(specs,limit_per_source=int(job.get('sources_limit',8)))
        value={'job':f'{domain}/{domain}_illustrated','past_titles':titles,'ideas':[asdict(x) for x in ideas],
               'resolved_sources':specs,'captured_at':time.time(),'input_kind':'automatic current source adapters and original job past_titles; no hand-selected topic'}
        save(path,value)
        return {'domain':domain,'history_titles':len(titles),'source_ideas':len(ideas)}
    with ThreadPoolExecutor(max_workers=2) as pool:
        for result in pool.map(one,['science','culture']):print(json.dumps(result),flush=True)


class ResearchCaptured(BaseException):pass


def run(domain,variant):
    work=Path('/tmp/natural-writing-20261116-knowledge')/domain/variant
    out=BASE/'results'/domain/variant
    if work.exists() or out.exists():raise FileExistsError('Preserve attempts')
    out.mkdir(parents=True)
    for folder in ('core','utils','web','config','jobs'):
        shutil.copytree(Path('/tmp/natural-writing-20261116-r1/control')/folder,work/folder,
                        ignore=shutil.ignore_patterns('__pycache__','*.pyc','*.lock','.DS_Store'))
    for folder in ('state','logs'):(work/folder).mkdir()
    rel=Path(f'jobs/{domain}/{domain}_illustrated.yaml')
    if variant=='candidate':shutil.copyfile(HERE/'candidates/structural-requirements'/rel,work/rel)
    else:
        # Both arms use the original file captured with the candidate manifest.
        manifest=json.loads((HERE/'candidates/structural-requirements/manifest.json').read_text())
        expected=next(r['original_sha256'] for r in manifest if r['path']==str(rel))
        assert hashlib.sha256((ROOT/rel).read_bytes()).hexdigest()==expected
        shutil.copyfile(ROOT/rel,work/rel)
    sys.path.insert(0,str(work));os.chdir(work)
    from dotenv import load_dotenv
    load_dotenv(ROOT/'.env',override=False)
    from core import activity,events,llm,state,telemetry,knowledge_job,sources
    from core.search import SearchResult
    from core.video import store
    state.use_state_dir(str(work/'state'));activity.use_state_dir(str(work/'state'))
    events.use_state_dir(str(work/'state'));telemetry.use_state_dir(str(work/'state'))
    store.use_state_dir(str(work/'state'));store.init_db()
    frozen=json.loads((BASE/'inputs'/f'{domain}.json').read_text())
    knowledge_job.past_titles=lambda *a,**kw:list(frozen['past_titles'])
    sources.fetch_all=lambda *a,**kw:[SearchResult(**x) for x in frozen['ideas']]
    class Client(llm.LLMClient):
        def __init__(self,config,*args,**kwargs):
            assert config.base_url=='https://opencode.ai/zen/go/v1' and config.model=='deepseek-v4.1-flash'
            super().__init__(replace(config,fallback_models=[],fallback_providers=[]),*args,**kwargs)
        def chat(self,messages,**kwargs):
            save(out/'request.json',{'messages':messages,'options':kwargs,'model':self.config.model,
                 'endpoint':self.config.base_url,'fallbacks_disabled_to_keep_authorized_endpoint':True})
            started=time.monotonic();response=super().chat(messages,**kwargs)
            save(out/'response.json',{'content':response.content,'usage':dict(self.usage),
                 'seconds':time.monotonic()-started,'model_switches':list(getattr(self,'model_switches',[]))})
            (out/'research.txt').write_text(response.content)
            raise ResearchCaptured()
    llm.LLMClient=Client
    from core.engine import ResearchEngine
    engine=ResearchEngine(config_dir=str(work/'config'),jobs_dir=str(work/'jobs'),workspace_dir=str(work),state_dir=str(work/'state'),date_override='2026-11-16')
    save(out/'input-receipt.json',{'input':str(BASE/'inputs'/f'{domain}.json'),
         'input_sha256':hashlib.sha256((BASE/'inputs'/f'{domain}.json').read_bytes()).hexdigest(),
         'job_sha256':hashlib.sha256((work/rel).read_bytes()).hexdigest(),
         'stage':'initial knowledge research only; source responses and past titles frozen; no enrichment or script'})
    started=time.monotonic();save(out/'status.json',{'status':'running','pid':os.getpid()})
    try:engine.run_job(frozen['job'])
    except ResearchCaptured:
        save(out/'status.json',{'status':'complete','seconds':time.monotonic()-started,'stage':'initial research only'})
        print(json.dumps({'domain':domain,'variant':variant,'status':'complete'}),flush=True)
    except Exception as exc:
        save(out/'status.json',{'status':'failed','type':type(exc).__name__})
        raise
    else:raise RuntimeError('No research response captured')


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('action',choices=['freeze','run']);p.add_argument('--domain',choices=['science','culture']);p.add_argument('--variant',choices=['control','candidate']);a=p.parse_args()
    freeze() if a.action=='freeze' else run(a.domain,a.variant)
