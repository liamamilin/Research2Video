"""Compare placing frozen automatic supplements beside existing report passages."""
from __future__ import annotations
import argparse
from dataclasses import replace
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
    work = Path('/tmp/natural-writing-20261116-placed-supplement') / domain
    out = BASE / 'placed-supplement' / domain / 'candidate'
    if work.exists() or out.exists():
        raise FileExistsError('Preserve attempts')
    out.mkdir(parents=True)
    source = ROOT
    for folder in ('core', 'utils', 'web', 'config', 'jobs'):
        shutil.copytree(source / folder, work / folder,
                        ignore=shutil.ignore_patterns('__pycache__', '*.pyc', '*.lock', '.DS_Store'))
    job = f'{domain}/{domain}_illustrated'
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
    from core import activity, events, llm, report_meta, state, telemetry
    from core.video import store
    for module in (activity, events, state, telemetry, store):
        module.use_state_dir(str(work / 'state'))
    store.init_db()
    report_meta.META_FILE = str(work / 'state/report_meta.jsonl')
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
    from core.video import production, reports
    from web.services import video_executor, video_workflow
    video_workflow.spawn = lambda *a, **kw: None
    from core.config import load_system_config
    from core import report_material
    from place_supplement import organize
    control = BASE / 'research-brief' / domain / 'candidate'
    receipt = next(r for r in (json.loads(p.read_text()) for p in control.glob('*.audience-*.json'))
                   if r.get('question_material'))
    unit = receipt['question_material']['topics'][0]
    original = receipt['initial_report']
    added = unit['retrieved_sources']
    system = load_system_config(str(work / 'config'))
    llm_config = llm.LLMConfig.from_system(system, 'ai')
    writer_path = work / 'core/video/writer.py'
    save(out / 'input-receipt.json', {
        'job': job, 'control': str(control), 'source_sha256': hashlib.sha256(json.dumps(added, ensure_ascii=False).encode()).hexdigest(),
        'original_sha256': hashlib.sha256(original.encode()).hexdigest(),
        'writer_sha256': hashlib.sha256(writer_path.read_bytes()).hexdigest(),
        'intervention': 'same original selected research and raw supplement bodies; place one enrichment output in original prose; run actual report-to-script service without another search'})
    started = time.monotonic()
    save(out / 'status.json', {'status': 'organizing', 'pid': os.getpid()})
    client = Client(llm_config)
    try:
        enriched, detail = organize(client, original, {'title': unit['title'], 'channel': receipt['channel']}, added)
    except Exception as exc:
        save(out / 'status.json', {'status': 'organization_failed', 'error': type(exc).__name__})
        raise
    finally:
        client.close()
    save(out / 'placements.json', detail)
    report = f'output/{domain}/2026-11-16_{domain}_illustrated.md'
    path = work / report
    path.parent.mkdir(parents=True)
    path.write_text(enriched)
    (out / 'research.txt').write_text(enriched)
    used_ids = {eid for edit in detail['edits'] for eid in edit['used_ids']}
    material = report_material.save(str(path), enriched, [s['text'] for s in added if s['id'] in used_ids])
    report_meta.append_record(report, job, extra={'audience_strategy': {'strategy': receipt['strategy'],
        'channel': receipt['channel'], 'candidate_count': 1, 'question_material': 'question-v1'}, 'material': material})
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
    args = parser.parse_args()
    main(args.domain)
