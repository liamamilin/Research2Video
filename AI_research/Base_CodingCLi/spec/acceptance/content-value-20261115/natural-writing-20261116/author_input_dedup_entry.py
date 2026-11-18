"""Replay frozen report and automatically selected event through the normal author service."""
from __future__ import annotations

import argparse
from dataclasses import asdict, replace
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
CASES = {'svg': 'd74b8ce57a7d447788ddcf54e6fa9ed6', 'marked_t05': '5749903e81e54ff48e042cd3ac988892'}
JOBS = {'svg': 'monitoring/ai_flash_discovery', 'marked_t05': 'chatgpt_acquisition/t05_comparison'}
FROZEN_POOLS = {'svg': Path('/tmp/natural-writing-20261116-original-feedback-final-r2/svg/candidate/state/video.db'),
                'marked_t05': Path('/tmp/natural-writing-20261116-t05-kept-item/marked/candidate/state/video.db')}
ILLUSTRATED = {'science': '4e99439efa84453997e519b74e679477', 'culture': 'f2c742f07ca04e8b979bb08b023ce7b1'}


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def run(case, variant, illustrated=False, round_number=8, adopted=False):
    suite = 'author-input-dedup'
    work = Path('/tmp/natural-writing-20261116-' + suite) / case / variant
    out = BASE / suite / case / variant
    if work.exists() or out.exists():
        raise FileExistsError('Preserve attempts')
    out.mkdir(parents=True)
    source = ROOT
    for folder in ('core', 'utils', 'web', 'config', 'jobs'):
        shutil.copytree(source / folder, work / folder,
                        ignore=shutil.ignore_patterns('__pycache__', '*.pyc', '*.lock', '.DS_Store'))
    for folder in ('state', 'logs'):
        (work / folder).mkdir()
    original = json.loads((BASE / 'baseline/scripts' / ((ILLUSTRATED if illustrated else CASES)[case] + '.json')).read_text())
    report_rel = original['report_path']
    if original['topic'].get('joint_plan'):
        ref = original['topic']['joint_plan']
        plan_rel = Path('state/joint-plans') / (ref['id'] + '.json')
        plan_source = ROOT / plan_rel
        assert json.loads(plan_source.read_text())['id'] == ref['id']
        (work / plan_rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(plan_source, work / plan_rel)
        shutil.copyfile(plan_source, out / 'joint-plan-input.json')
        save(out / 'joint-plan-receipt.json', {'path': str(plan_rel),
            'sha256': hashlib.sha256(plan_source.read_bytes()).hexdigest(),
            'scope': 'same saved automatic allocation referenced by original topic; no new allocation'})
    material = dict(original['meta'].get('material') or {})
    material_rel = str(Path(material['path']).relative_to(ROOT)) if material else ''
    for rel in [report_rel] + ([material_rel] if material else []):
        target = work / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(BASE / 'baseline/material_files' / rel, target)
    if material:
        material_data = json.loads((work / material_rel).read_text())
        assert hashlib.sha256(material_data['material'].encode()).hexdigest() == material['sha256']
        assert material_data['report_sha256'] == material['report_sha256']
        assert hashlib.sha256((work / report_rel).read_bytes()).hexdigest() == material['report_sha256']
        material['path'] = str(work / material_rel)
    sys.path.insert(0, str(work))
    os.chdir(work)
    from dotenv import load_dotenv
    load_dotenv(ROOT / '.env', override=False)
    os.environ['AI_RESEARCH_SECRET_KEY'] = 'isolated-natural-writing-holdout-20261116'
    from web.settings import get_settings
    settings = get_settings()
    for folder in ('config', 'jobs', 'state', 'logs'):
        setattr(settings.paths, folder + '_dir', str(work / folder))
    from core import activity, events, llm, report_meta, state, telemetry
    from core.video import store
    for module in (activity, events, state, telemetry, store):
        module.use_state_dir(str(work / 'state'))
    store.init_db()
    frozen_pool = FROZEN_POOLS[case]
    with sqlite3.connect(f"file:{frozen_pool}?mode=ro", uri=True) as db:
        rows = db.execute("SELECT id, scope, payload, created_at FROM video_story_pools").fetchall()
    assert len(rows) == 1
    with store.connect(write=True) as db:
        db.executemany("INSERT INTO video_story_pools VALUES(?,?,?,?)", rows)
    save(out / "event-pool-receipt.json", {"id": rows[0][0], "sha256": hashlib.sha256(rows[0][2].encode()).hexdigest(),
         "source": str(frozen_pool), "note": "Only previous automatic event organization is replayed; no historical claims copied."})
    report_meta.META_FILE = str(work / 'state/report_meta.jsonl')
    job = JOBS.get(case) or case + '/' + case + ('_illustrated' if illustrated else '_weekly')
    extra = {'material': material}
    if original['meta'].get('audience_strategy'):
        extra['audience_strategy'] = original['meta']['audience_strategy']
    report_meta.append_record(report_rel, job, extra=extra)

    original_client = llm.LLMClient

    class Client(original_client):
        def __init__(self, config, *args, **kwargs):
            assert config.base_url == 'https://opencode.ai/zen/go/v1' and config.model == 'deepseek-v4.1-flash'
            super().__init__(replace(config, fallback_models=[], fallback_providers=[]), *args, **kwargs)

        def chat(self, messages, **kwargs):
            assert not messages[0]['content'].startswith('把给定素材整理成视频选题事件池'), 'frozen pool did not match'
            stamp = str(time.time_ns())
            save(out / 'calls' / (stamp + '-request.json'), {'messages': messages, 'options': kwargs,
                 'endpoint': self.config.base_url, 'model': self.config.model})
            response = super().chat(messages, **kwargs)
            save(out / 'calls' / (stamp + '-response.json'), {'content': response.content,
                 'usage': dict(self.usage), 'model_switches': list(getattr(self, 'model_switches', []))})
            return response

    llm.LLMClient = Client
    # Some adapters import the class by value before the service is imported.
    for name, module in list(sys.modules.items()):
        if name.startswith(('core.', 'web.')) and getattr(module, 'LLMClient', None) is original_client:
            module.LLMClient = Client
    from core.video import episodes, production, reports, topic_store
    from web.services import video_executor, video_workflow
    video_workflow.spawn = lambda *args, **kwargs: None
    config = production.get(str(work / 'config'), str(work / 'jobs'), original['production'])
    assert config and config.usable
    record = reports.record_file(report_rel, job=config.job, created_by='natural-writing-holdout')
    if config.content_mode == 'episode':
        topic = episodes.topic(record, str(work), job=config.job,
                               source=topic_store.resolve(str(work / 'jobs'), config.job), production=config.key)
    else:
        topic = {**original['topic'], 'from_report': report_rel, 'report_id': record['id']}  # saved automatic topic, unchanged
    topic['_production'] = config.key
    created = video_executor.create(config.recipe, topic, 'natural-writing-holdout')
    save(out / 'input-receipt.json', {'source_script': original['id'], 'source_hashes': {x['file']: hashlib.sha256((source / x['file']).read_bytes()).hexdigest() for x in json.loads((HERE / 'soft-applied-files.json').read_text())}, 'production': asdict(config),
         'created_script': store.get_script(created['id']), 'report_sha256': hashlib.sha256((work / report_rel).read_bytes()).hexdigest(),
         'material_sha256': material.get('sha256'), 'interventions': ['isolated database and file copies',
         'same saved research, automatic topic and cached event pool; remove duplicated event summary only', 'spawn runs synchronously', 'model calls recorded; same authorized endpoint'],
         'entry': 'same episodes.topic and video_executor.create as report-create route, then video_workflow.run',
         'scope': 'saved automatic research to complete script; no upstream refetch, publishing or rendering'})
    save(out / 'status.json', {'status': 'running', 'pid': os.getpid()})
    started = time.monotonic()
    result = video_workflow.run(created['id'], started_by='natural-writing-holdout')
    script = store.get_script(created['id'])
    save(out / 'script-record.json', script)
    version = store.get_version(created['id'], script['current_version']) if script['current_version'] else None
    if version:
        save(out / 'script.json', version)
        (out / 'script.txt').write_text(version['title'] + '\n\n' + '\n\n'.join(
            s['title'] + '\n' + '\n'.join(p['text'] for p in s['paragraphs']) for s in version['sections']))
    save(out / 'status.json', {'status': result, 'seconds': time.monotonic() - started, 'error': script.get('error')})
    print(json.dumps({'case': case, 'variant': variant, 'status': result}, ensure_ascii=False), flush=True)
    if result != 'DRAFT' or not version:
        raise SystemExit(1)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('case', choices=CASES)
    parser.add_argument('variant', choices=['candidate'])
    parser.add_argument('--illustrated', action='store_true')
    parser.add_argument('--round', type=int, default=8)
    parser.add_argument('--adopted', action='store_true')
    args = parser.parse_args()
    run(args.case, args.variant, args.illustrated, args.round, args.adopted)
