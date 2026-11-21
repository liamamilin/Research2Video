"""Run the existing knowledge-research -> enrichment -> manuscript services in isolation.

Run fresh controls, then reuse their input ideas for a shorter job prompt.
Both variants run initial research, supplemental search, and the same writer.
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


def main(domain, variant):
    work = Path('/tmp/natural-writing-20261116-research-brief') / domain / variant
    out = BASE / 'research-brief' / domain / variant
    if work.exists() or out.exists():
        raise FileExistsError('Preserve attempts')
    out.mkdir(parents=True)
    source = ROOT
    for folder in ('core', 'utils', 'web', 'config', 'jobs'):
        shutil.copytree(source / folder, work / folder,
                        ignore=shutil.ignore_patterns('__pycache__', '*.pyc', '*.lock', '.DS_Store'))
    job = f'{domain}/{domain}_illustrated'
    job_path = work / 'jobs' / (job + '.yaml')
    previous = job_path.read_text()
    phrase = '说明观看动机、新意与实际收获；'
    if variant == 'candidate':
        assert previous.count(phrase) == 1
        job_path.write_text(previous.replace(phrase, ''))
    source_reports = ROOT / 'output' / domain
    copied = work / 'output' / domain
    copied.mkdir(parents=True)
    for report in source_reports.glob(f'*_{domain}_illustrated*.md'):
        shutil.copyfile(report, copied / report.name)
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
    real_fetch = sources.fetch_all
    def get_ideas(*args, **kwargs):
        if variant == 'control':
            items = real_fetch(*args, **kwargs)
        else:
            capture = json.loads((out.parent / 'control' / 'source-ideas.json').read_text())
            items = [SearchResult(**item) for item in capture]
        save(out / 'source-ideas.json', [asdict(item) for item in items])
        return items
    sources.fetch_all = get_ideas
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
    save(out / 'input-receipt.json', {
        'job': job, 'variant': variant, 'prior_titles': knowledge_job.past_titles(
            {'output': f'output/{domain}/{{date}}_{domain}_illustrated.md'}, str(work)),
        'job_sha256': hashlib.sha256(job_path.read_bytes()).hexdigest(),
        'writer_sha256': hashlib.sha256((work / 'core/video/writer.py').read_bytes()).hexdigest(),
        'question_material_sha256': hashlib.sha256((work / 'core/video/question_material.py').read_bytes()).hexdigest(),
        'isolation': str(work),
        'interventions': ['copied real prior report files; fetched current ideas for control and copied those ideas for candidate',
                          'candidate removes the required why-watch/novelty/takeaway explanation from initial research',
                          'supplemental search runs normally in both variants',
                          'report-to-script service called synchronously; no publication']})
    started = time.monotonic()
    save(out / 'status.json', {'status': 'researching', 'pid': os.getpid()})
    engine = ResearchEngine(config_dir=str(work / 'config'), jobs_dir=str(work / 'jobs'),
                            workspace_dir=str(work), state_dir=str(work / 'state'), date_override='2026-11-16')
    report = engine.run_job(job)
    if not report:
        save(out / 'status.json', {'status': 'research_failed', 'seconds': time.monotonic() - started})
        raise SystemExit(1)
    shutil.copyfile(work / report, out / 'research.txt')
    for receipt_path in (work / report).parent.glob(f'{Path(report).name}.audience-*.json'):
        shutil.copyfile(receipt_path, out / receipt_path.name)
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
    parser.add_argument('domain', choices=['geography', 'history'])
    parser.add_argument('variant', choices=['control', 'candidate'])
    args = parser.parse_args()
    main(args.domain, args.variant)
