"""Offline entry-point probe; capture and stop before the first model request.

This does not generate prose or prove quality. Knowledge source fetching is
replaced with an empty result. Video examples use the existing validation
creator and saved report; they do not prove selection or publishing behavior.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import sys

ROOT = Path(__file__).resolve().parents[4]
HERE = Path(__file__).resolve().parent
BASE = ROOT / "state/acceptance-content-value-20261115/natural-writing-20261116"
DOMAINS = ("company", "consumer", "culture", "economy", "geography", "history", "industry", "science", "software", "tech")


def save(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")


class Captured(BaseException):
    pass


def main(variant: str) -> None:
    work = Path('/tmp/natural-writing-20261116-entry-probe') / variant
    out = BASE / 'entry-probe' / variant
    if work.exists() or out.exists():
        raise FileExistsError('Preserve previous attempts')
    out.mkdir(parents=True)
    for folder in ('core', 'utils', 'web', 'config', 'jobs'):
        shutil.copytree(ROOT / folder, work / folder,
                        ignore=shutil.ignore_patterns('__pycache__', '*.pyc', '.DS_Store', '*.lock'))
    for folder in ('state', 'logs'):
        (work / folder).mkdir(exist_ok=True)
    manifest = json.loads((HERE / 'candidates/structural-requirements/manifest.json').read_text())
    if variant == 'candidate':
        for entry in manifest:
            rel = entry['path']
            # Do not silently base the experiment on a concurrently changed config.
            assert hashlib.sha256((ROOT / rel).read_bytes()).hexdigest() == entry['original_sha256']
            shutil.copyfile(HERE / 'candidates/structural-requirements' / rel, work / rel)
    sys.path.insert(0, str(work))
    os.chdir(work)
    os.environ['AI_RESEARCH_SECRET_KEY'] = 'offline-entry-probe-local-only-20261116-7c91d2'

    def no_network(*args, **kwargs):
        raise AssertionError('Offline capture cannot open network connections')

    socket.create_connection = no_network
    socket.socket.connect = no_network
    socket.socket.connect_ex = no_network
    from web.settings import get_settings
    settings = get_settings()
    for folder in ('config', 'jobs', 'state', 'logs'):
        setattr(settings.paths, folder + '_dir', str(work / folder))
    from core import activity, events, llm, report_meta, state, telemetry
    from core.video import store
    state.use_state_dir(str(work / 'state'))
    activity.use_state_dir(str(work / 'state'))
    events.use_state_dir(str(work / 'state'))
    telemetry.use_state_dir(str(work / 'state'))
    store.use_state_dir(str(work / 'state'))
    store.init_db()
    report_meta.META_FILE = str(work / 'state/report_meta.jsonl')
    current = {}
    calls = []

    def capture(self, messages, **kwargs):
        path = out / (current['case'] + '-request.json')
        save(path, {'messages': messages, 'options': kwargs,
                    'evidence_level': 'offline entry routing only; no model called',
                    'substitutions': current['substitutions']})
        calls.append({'case': current['case'], 'path': str(path.relative_to(ROOT)),
                      'status': 'captured_before_model'})
        raise Captured()

    llm.LLMClient._make_client = lambda self: None
    llm.LLMClient.chat = capture
    from core.engine import ResearchEngine
    from core import sources
    sources.fetch_all = lambda *args, **kwargs: []
    engine = ResearchEngine(config_dir=str(work / 'config'), jobs_dir=str(work / 'jobs'),
                            workspace_dir=str(work), state_dir=str(work / 'state'), date_override='2026-11-16')
    for domain in DOMAINS:
        job = f'{domain}/{domain}_illustrated'
        current.update(case=domain + '-research', substitutions=['source fetch returns empty; isolated past-title history empty', 'LLM stopped before sending'])
        try:
            engine.run_job(job)
        except Captured:
            pass
        else:
            raise AssertionError('Research did not reach model: ' + job)

    from web.services import video_executor, video_workflow
    video_workflow.spawn = lambda *args, **kwargs: None
    baseline = json.loads((BASE / 'baseline/scripts/194a1831aabc4f9e9558f75544c2293b.json').read_text())
    report = BASE / 'baseline/material_files' / baseline['report_path']
    target = work / baseline['report_path']
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(report, target)
    topic = deepcopy(baseline['topic'])
    for key in ('matrix_dedup', 'story', 'after_id', 'episode_id', '_story_fallbacks'):
        topic.pop(key, None)
    topic['_production'] = baseline['production']
    current.update(case='t05-new', substitutions=['existing saved report', 'validation creator excludes selection and publishing', 'spawn made synchronous', 'LLM stopped before sending'])
    created = video_executor.create(baseline['column_key'], topic, 'prompt-quality-validation')
    save(out / 't05-new-context.json', store.get_script(created['id'])['content_context'])
    try:
        video_workflow.run(created['id'], started_by='prompt-quality-validation')
    except Captured:
        store.update_script(created['id'], status='FAILED', stage=None, error='Offline probe stopped before model; no draft generated')
    else:
        raise AssertionError('New T05 did not reach model')

    # An older saved script intentionally keeps its previous context on rewrite.
    old = store.create_script(baseline['column_key'], deepcopy(topic), created_by='prompt-quality-validation',
                              production=baseline['production'], content_context=baseline['content_context'])
    store.update_script(old['id'], status='FAILED', report_path=str(target), stage=None)
    current.update(case='t05-old-rewrite', substitutions=['cloned historical script context', 'existing saved report', 'validation creator excludes selection and publishing', 'LLM stopped before sending'])
    try:
        video_workflow.run_rewrite(old['id'], started_by='prompt-quality-validation', previous_status='FAILED')
    except Captured:
        store.update_script(old['id'], status='FAILED', stage=None, error='Offline probe stopped before model; no draft generated')
    else:
        raise AssertionError('Old T05 did not reach model')
    save(out / 'receipt.json', {'variant': variant, 'calls': calls, 'network_calls': 0,
                               'quality_verified': False, 'production_modified': False})
    print(json.dumps({'variant': variant, 'captured': len(calls), 'network_calls': 0}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('variant', choices=['control', 'candidate'])
    main(parser.parse_args().variant)
