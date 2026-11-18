"""Run current Flash research and the roundup author with isolated project state."""
from __future__ import annotations
import argparse
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import time

ROOT = Path(__file__).resolve().parents[4]
HERE = Path(__file__).resolve().parent
BASE = ROOT / 'state/acceptance-content-value-20261115/natural-writing-20261116'


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def seed_flash_collection(work, job_name):
    """Use only this job's delivered baseline and related archived observations."""
    from core.config import load_job
    from core.material_archive import MaterialArchive
    from core.source_coverage import targets
    from core.source_supply import for_job
    from core.source_supply_store import SupplyStore, identity
    from core.sources import resolve

    config_dir = str(work / 'config')
    job = load_job(str(work / 'jobs'), job_name)
    profile = for_job(config_dir, job)
    assert profile is not None
    original = SupplyStore(ROOT / 'state', profile['key'])
    isolated = SupplyStore(work / 'state', profile['key'])
    key = identity(job_name) + '.json'
    files = {}
    for name in ('pool.json', 'coverage.json', f'baselines/{key}', f'candidates/{key}'):
        target = isolated.root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(original.root / name, target)
        files[name] = hashlib.sha256(target.read_bytes()).hexdigest()
    assert isolated.baseline(job_name)['items']

    pool = isolated.pool()['sources']
    specs = sorted(set(resolve(job.get('sources') or [], config_dir))
                   | {r['spec'] for r in targets(profile, job_name, pool)}
                   | {r['spec'] for r in pool if job_name in r['jobs']})
    archive = MaterialArchive(work / 'state')
    counts = {}
    where = ','.join('?' for _ in specs)
    with sqlite3.connect(f"file:{ROOT / 'state/materials.db'}?mode=ro", uri=True) as live, archive.connect() as snapshot:
        live.execute('BEGIN')
        for table, width in (('observations', 7), ('fetches', 6)):
            rows = live.execute(f'SELECT * FROM {table} WHERE spec IN ({where})', specs)
            placeholders = ','.join('?' for _ in range(width))
            snapshot.executemany(f'INSERT INTO {table} VALUES ({placeholders})', rows)
            counts[table] = snapshot.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]
    return {'mode': 'fresh_with_existing_delivery_history', 'profile': profile['key'],
            'history_sha256': files, 'archive_rows': counts, 'source_specs': len(specs)}


def replay_selected_item(work, variant, job):
    """Run the regular single-item writer on a captured automatic selection."""
    from core import report_material, report_meta
    from core.video import audience_strategy as strategy
    from core.video.adapt.common import parse_json

    if variant == 'selection-replay-warm':
        trial = BASE / 'warm-selection-entry'
        packets = json.loads((trial / 'items.json').read_text())
        selected = json.loads((trial / 'selection.json').read_text())['candidates'][0]
    else:
        trial = BASE / 'selection-input-trial/flash'
        receipt = json.loads((trial / 'receipt.json').read_text())
        original = Path(receipt['control_request'])
        body = json.loads(original.read_text())['messages'][-1]['content']
        sources = body.split('\n初选：\n', 1)[1].split('\n原文：\n', 1)[1]
        assert hashlib.sha256(sources.encode()).hexdigest() == receipt['sources_sha256']
        packets = json.loads(sources)
        response = original.with_name(original.name.replace('-request', '-response'))
        control = parse_json(json.loads(response.read_text())['content'])
        candidate = json.loads((trial / 'selection.json').read_text())
        before, after = (next(c for c in data['candidates'] if '改页码' in c['title']) for data in (control, candidate))
        assert before['source_ids'] == after['source_ids']
        selected = before if variant == 'selection-replay-control' else after
    selection = strategy.Selection.model_validate({'candidates': [selected]})
    strategy.validate_sources(selection, packets)
    material = strategy.author_material(selection.candidates[0], packets)
    report = 'output/flash/2026-11-16_ai_flash_discovery.md'
    target = work / report
    target.parent.mkdir(parents=True, exist_ok=True)
    text = strategy.render_report(selection, packets, 'flash')
    target.write_text(text)
    ref = report_material.save(str(target), text, [material])
    report_meta.append_record(report, job, extra={'material': ref, 'audience_strategy': {
        'strategy': strategy.MARKER, 'channel': 'flash', 'candidate_count': 1}})
    return report, {'mode': 'same_selected_item_and_original_documents',
                    'material_sha256': ref['sha256'], 'source_ids': selected['source_ids'],
                    'report_sha256': ref['report_sha256'], 'kind': variant}


def main(variant):
    fresh = variant in {'current', 'latest', 'latest-warm'}
    replay = variant.startswith('selection-replay-')
    name = 'natural-writing-20261116-flash-live'
    work = Path('/tmp') / (name if variant == 'current' else f'{name}-{variant}')
    out = BASE / 'flash-live' / variant
    if work.exists() or out.exists():
        raise FileExistsError('Preserve attempts')
    out.mkdir(parents=True)
    source = ROOT
    for folder in ('core', 'utils', 'web', 'config', 'jobs'):
        shutil.copytree(source / folder, work / folder,
                        ignore=shutil.ignore_patterns('__pycache__', '*.pyc', '*.lock', '.DS_Store'))
    job = 'monitoring/ai_flash_discovery'
    job_path = work / 'jobs' / (job + '.yaml')
    if variant == 'same-supply-control':
        original = BASE / 'research-field-soft/before/jobs' / (job + '.yaml')
        shutil.copyfile(original, job_path)
    for folder in ('state', 'logs'):
        (work / folder).mkdir()
    sys.path.insert(0, str(work))
    os.chdir(work)
    from dotenv import load_dotenv
    load_dotenv(ROOT / '.env', override=False)
    from secrets import token_urlsafe
    os.environ['AI_RESEARCH_SECRET_KEY'] = token_urlsafe(40)
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
            try:
                response = super().chat(messages, **kwargs)
            except Exception as exc:
                cause = exc.__cause__ or exc
                reason = str(exc).lower()
                save(out / 'calls' / (stamp + '-error.json'), {
                    'type': type(exc).__name__,
                    'http_status': getattr(cause, 'status_code', None),
                    'context_limit': any(term in reason for term in (
                        'maximum context', 'context length', 'context_length_exceeded')),
                    'timeout': 'timed out' in reason or 'timeout' in reason,
                })
                raise
            save(out / 'calls' / (stamp + '-response.json'), {'content': response.content,
                 'usage': dict(self.usage), 'model_switches': list(getattr(self, 'model_switches', []))})
            return response

    llm.LLMClient = Client
    for name, module in list(sys.modules.items()):
        if name.startswith(('core.', 'web.')) and getattr(module, 'LLMClient', None) is original_client:
            module.LLMClient = Client
    from core.engine import ResearchEngine
    from core.video import columns, episodes, excerpt, production, reports, topic_store
    from web.services import video_executor, video_workflow
    video_workflow.spawn = lambda *a, **kw: None
    engine = ResearchEngine(config_dir=str(work / 'config'), jobs_dir=str(work / 'jobs'),
                            workspace_dir=str(work), state_dir=str(work / 'state'), date_override='2026-11-16')
    collection = {'mode': 'fresh'}
    if variant == 'latest-warm':
        collection = seed_flash_collection(work, job)
    elif replay:
        report, collection = replay_selected_item(work, variant, job)
    elif not fresh:
        from core.provenance import extract_urls
        from core.search import SearchResult
        frozen = BASE / 'flash-research-prompt-pair/flexible_fields'
        source_block = (frozen / 'source-block.txt').read_text()
        prior = json.loads((frozen / 'input-receipt.json').read_text())
        sha = hashlib.sha256(source_block.encode()).hexdigest()
        assert sha == prior['source_sha256']
        supply_path = ROOT / 'output/flash/2026-11-16_ai_flash_discovery.md.source-supply.json'
        supply = json.loads(supply_path.read_text())
        stats = {key: row['delivered'] for key, row in supply['sources'].items()}
        fetched = [SearchResult(title='', url=url) for url in extract_urls(source_block)]
        engine._fetch_sources_strict = lambda _job: (source_block, (fetched, stats))
        collection = {'mode': 'frozen_actual_initial_supply', 'source': str(frozen / 'source-block.txt'),
                      'source_sha256': sha, 'chars': len(source_block)}
    save(out / 'input-receipt.json', {
        'job': job,
        'job_sha256': hashlib.sha256(job_path.read_bytes()).hexdigest(),
        'writer_sha256': hashlib.sha256((work / 'core/video/writer.py').read_bytes()).hexdigest(),
        'selector_sha256': hashlib.sha256((work / 'core/video/audience_strategy.py').read_bytes()).hexdigest(),
        'isolation': str(work),
        'collection': collection,
        'interventions': (
            ['selected report item and original documents from real refinement; no new supplemental material'] if replay
            else ['frozen initial supply only in same-supply variants; live extra reading and supplement',
                  'latest-warm copies delivery/source history only; prior event and publication state is isolated'])
            + ['report-to-script service called synchronously; no publication or rendering']})
    started = time.monotonic()
    save(out / 'status.json', {'status': 'researching', 'pid': os.getpid()})
    if not replay:
        report = engine.run_job(job)
    if not report:
        save(out / 'status.json', {'status': 'research_failed', 'seconds': time.monotonic() - started})
        raise SystemExit(1)
    shutil.copyfile(work / report, out / 'research.txt')
    supply_receipt = Path(str(work / report) + '.source-supply.json')
    if supply_receipt.is_file():
        shutil.copyfile(supply_receipt, out / 'source-supply.json')
    for receipt_path in (work / report).parent.glob(f'{Path(report).name}.audience-*.json'):
        shutil.copyfile(receipt_path, out / receipt_path.name)
    production_id = 'flash_each' if replay else 'flash_daily'
    config = production.get(str(work / 'config'), str(work / 'jobs'), production_id)
    assert config and config.usable
    record = reports.record_file(report, job=job, created_by='natural-writing-soft-input')
    source = topic_store.resolve(str(work / 'jobs'), config.job)
    if replay:
        _, items = topic_store.read_report(source, report, str(work))
        assert len(items) == 1
        topic = excerpt.topic_for(columns.for_new_script(str(work / 'config'), config.recipe), items[0])
        assert topic['from_item']
        topic['report_id'] = record['id']
    else:
        topic = episodes.topic(record, str(work), job=config.job, source=source, production=config.key)
    topic['_production'] = config.key
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
    print(json.dumps({'status': result}), flush=True)
    if result != 'DRAFT' or not version:
        raise SystemExit(1)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('variant', nargs='?', default='current',
                        choices=('current', 'latest', 'latest-warm', 'same-supply-control', 'same-supply-current',
                                 'same-supply-soft-selector', 'same-supply-full-sources',
                                 'selection-replay-control', 'selection-replay-candidate', 'selection-replay-warm'))
    main(parser.parse_args().variant)
