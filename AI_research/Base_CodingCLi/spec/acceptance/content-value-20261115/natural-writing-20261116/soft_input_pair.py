"""Run the existing knowledge-research -> enrichment -> manuscript services in isolation.

Only automatically captured source ideas and past titles are replayed. No topic,
research prose, material, or narration is hand-written into the run.
"""
from __future__ import annotations
import argparse
from dataclasses import asdict, replace
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import time

ROOT = Path(__file__).resolve().parents[4]
HERE = Path(__file__).resolve().parent
BASE = ROOT / 'state/acceptance-content-value-20261115/natural-writing-20261116'


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def main(domain):
    work = Path('/tmp/natural-writing-20261116-soft-input-pair-r2') / domain
    out = BASE / 'soft-input-pair-r2' / domain
    if work.exists() or out.exists():
        raise FileExistsError('Preserve attempts')
    out.mkdir(parents=True)
    source = Path('/tmp/natural-writing-20261116-soft/candidate')
    for folder in ('core', 'utils', 'web', 'config', 'jobs'):
        shutil.copytree(source / folder, work / folder,
                        ignore=shutil.ignore_patterns('__pycache__', '*.pyc', '*.lock', '.DS_Store'))
    for folder in ('state', 'logs'):
        (work / folder).mkdir()
    sys.path.insert(0, str(work))
    os.chdir(work)
    from dotenv import load_dotenv
    load_dotenv(ROOT / '.env', override=False)
    os.environ['AI_RESEARCH_SECRET_KEY'] = 'isolated-soft-input-20261116'
    os.environ['AI_RESEARCH_LOG_FILE'] = str(work / 'logs/research.log')
    from web.settings import get_settings
    settings = get_settings()
    for folder in ('config', 'jobs', 'state', 'logs'):
        setattr(settings.paths, folder + '_dir', str(work / folder))
    from core import activity, events, llm, report_meta, state, telemetry, knowledge_job, sources
    from core.video import store
    from core.search import SearchResult
    for module in (activity, events, state, telemetry, store):
        module.use_state_dir(str(work / 'state'))
    store.init_db()
    report_meta.META_FILE = str(work / 'state/report_meta.jsonl')
    frozen_path = BASE / 'knowledge-trial/inputs' / f'{domain}.json'
    frozen = json.loads(frozen_path.read_text())
    knowledge_job.past_titles = lambda *a, **kw: list(frozen['past_titles'])
    sources.fetch_all = lambda *a, **kw: [SearchResult(**x) for x in frozen['ideas']]
    original_client = llm.LLMClient

    class Client(original_client):
        def __init__(self, config, *args, **kwargs):
            assert config.base_url == 'https://opencode.ai/zen/go/v1'
            assert config.model == 'deepseek-v4.1-flash'
            super().__init__(replace(config, fallback_models=[], fallback_providers=[]), *args, **kwargs)

        def chat(self, messages, **kwargs):
            stamp = str(time.time_ns())
            save(out / 'calls' / (stamp + '-request.json'), {'messages': messages, 'options': kwargs,
                 'endpoint': self.config.base_url, 'model': self.config.model})
            response = super().chat(messages, **kwargs)
            save(out / 'calls' / (stamp + '-response.json'), {'content': response.content,
                 'usage': dict(self.usage), 'model_switches': list(getattr(self, 'model_switches', []))})
            return response

    llm.LLMClient = Client
    for name, module in list(sys.modules.items()):
        if name.startswith(('core.', 'web.')) and getattr(module, 'LLMClient', None) is original_client:
            module.LLMClient = Client
    from core.engine import ResearchEngine
    from core.video import production, reports
    from web.services import video_executor, video_workflow
    video_workflow.spawn = lambda *a, **kw: None
    job = f'{domain}/{domain}_illustrated'
    save(out / 'input-receipt.json', {'job': job, 'automatic_input': str(frozen_path),
         'input_sha256': hashlib.sha256(frozen_path.read_bytes()).hexdigest(),
         'scope': 'knowledge research, existing enrichment, report-to-script; no voice/render/publication',
         'interventions': ['isolated files/database', 'replayed initial research and automatically retrieved full passages for input-only comparison',
                           'synchronous spawn', 'recorded model calls on authorized endpoint'],
         'source_hashes': {r['file']: hashlib.sha256((source / r['file']).read_bytes()).hexdigest()
                           for r in json.loads((HERE / 'soft-candidate.json').read_text())}})
    started = time.monotonic()
    save(out / 'status.json', {'status': 'researching', 'pid': os.getpid()})
    engine = ResearchEngine(config_dir=str(work / 'config'), jobs_dir=str(work / 'jobs'),
                            workspace_dir=str(work), state_dir=str(work / 'state'), date_override='2026-11-16')
    # Freeze the actual initial dossier and fetched passages; only the material
    # organizer prompt changes. The writer stays identical to the previous run.
    from core.video import question_material as qm
    from core.video.adapt.common import call_json
    import yaml
    recorded = BASE / 'soft-research' / domain
    request = next(json.loads(p.read_text()) for p in sorted((recorded / 'calls').glob('*-request.json'))
                   if json.loads(p.read_text())['messages'][0]['content'].startswith('你只组织补充材料'))
    payload = json.loads(request['messages'][1]['content'])
    real_json = qm.call_json
    def replay_plan(client, prompt, user, schema, **kwargs):
        if prompt == qm.PLAN_PROMPT:
            return qm.Plan.model_validate(payload['questions'])
        return real_json(client, prompt, user, schema, **kwargs)
    qm.call_json = replay_plan
    qm._collect = lambda *a, **kw: (payload['added'], [])
    job_config = yaml.safe_load((work / 'jobs' / (job + '.yaml')).read_text())
    job_config['_file'] = job
    system = yaml.safe_load((work / 'config/system.yaml').read_text())
    enriched, used, receipt = qm.enhance(job_config, payload['original_material'], '',
                                       {'status': 'selected'}, system)
    assert receipt['status'] == 'enriched', receipt
    save(out / 'material-receipt.json', receipt)
    report = f'output/{domain}/2026-11-16_{domain}_illustrated.md'
    (work / report).parent.mkdir(parents=True, exist_ok=True)
    (work / report).write_text(enriched)
    if not report:
        save(out / 'status.json', {'status': 'research_failed', 'seconds': time.monotonic() - started})
        raise SystemExit(1)
    shutil.copyfile(work / report, out / 'research.txt')
    config = production.get(str(work / 'config'), str(work / 'jobs'), domain + '_illustrated')
    assert config and config.usable
    record = reports.record_file(report, job=job, created_by='natural-writing-soft-input')
    topic = {**reports.topic_for(record, str(work)), 'from_report': report, 'report_id': record['id'],
             '_production': config.key}
    created = video_executor.create(config.recipe, topic, 'natural-writing-soft-input')
    save(out / 'status.json', {'status': 'writing', 'pid': os.getpid(), 'script_id': created['id']})
    result = video_workflow.run(created['id'], started_by='natural-writing-soft-input')
    script = store.get_script(created['id'])
    version = store.get_version(created['id'], script['current_version']) if script['current_version'] else None
    save(out / 'script-record.json', script)
    if version:
        save(out / 'script.json', version)
        (out / 'script.txt').write_text(version['title'] + '\n\n' + '\n\n'.join(
            section['title'] + '\n' + '\n'.join(p['text'] for p in section['paragraphs']) for section in version['sections']))
    save(out / 'status.json', {'status': result, 'seconds': time.monotonic() - started,
                              'error': script.get('error'), 'report': report})
    print(json.dumps({'domain': domain, 'status': result}), flush=True)
    if result != 'DRAFT' or not version:
        raise SystemExit(1)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('domain', choices=['science', 'culture'])
    main(parser.parse_args().domain)
