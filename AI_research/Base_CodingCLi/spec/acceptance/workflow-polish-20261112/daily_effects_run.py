"""Run an unedited daily report through its normal automatic production recipe.

Only state/storage are isolated. No prepared pictures, script, layout, or duration
overrides. Select an existing production from the real daily batch.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import sys
import time

import yaml

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))


def save(path: Path, value) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n')


def prepare(out: Path, run_id: str, production: str = 'flash_each') -> None:
    out.mkdir(parents=True, exist_ok=False)
    with sqlite3.connect(f'file:{ROOT/"state/video.db"}?mode=ro', uri=True) as db:
        raw = db.execute('SELECT spec,created_at FROM autopilot_runs WHERE id=?', (run_id,)).fetchone()
    if raw is None:
        raise ValueError('Missing real daily batch')
    spec = json.loads(raw[0])
    report = Path(spec['report'])
    relative = report.relative_to(ROOT)
    for name in ('config', 'jobs'):
        shutil.copytree(ROOT/name, out/name)
    destination = out/relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(report, destination)
    # The production report identity and source packet are automatic inputs too.
    # Omitting .report.json changes topic IDs and invalidates heat-cache matches.
    for sidecar in report.parent.glob(report.name+'.*'):
        if sidecar.is_file():
            shutil.copy2(sidecar, destination.parent/sidecar.name)
    state = out/'state'
    state.mkdir()
    # Keep real prior coverage so automatic story selection sees earlier videos.
    # This is an isolated snapshot, never an edit of the production database.
    with sqlite3.connect(f'file:{ROOT/"state/video.db"}?mode=ro', uri=True) as source_db:
        with sqlite3.connect(state/'video.db') as history:
            source_db.backup(history)
            history.execute('PRAGMA foreign_keys=OFF')
            tables = [r[0] for r in history.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
            for table in tables:
                quoted = '"'+table.replace('"', '""')+'"'
                columns = {r[1]: r[2] for r in history.execute(f'PRAGMA table_info({quoted})')}
                if columns.get('created_at') == 'TEXT':
                    history.execute(f'DELETE FROM {quoted} WHERE created_at>=?', (raw[1],))
            for table in tables:
                quoted = '"'+table.replace('"', '""')+'"'
                columns = {r[1] for r in history.execute(f'PRAGMA table_info({quoted})')}
                if 'script_id' in columns:
                    history.execute(f'DELETE FROM {quoted} WHERE script_id IS NOT NULL AND script_id NOT IN (SELECT id FROM scripts)')
                if 'run_id' in columns:
                    history.execute(f'DELETE FROM {quoted} WHERE run_id IS NOT NULL AND run_id NOT IN (SELECT id FROM autopilot_runs)')
            history.commit()
            history_count = history.execute('SELECT count(*) FROM scripts').fetchone()[0]
    if (ROOT/'state/report_meta.jsonl').exists():
        shutil.copy2(ROOT/'state/report_meta.jsonl', state/'report_meta.jsonl')
    # Original retrieved text is an automatic writer input, not just a cache.
    # Its reader requires the material file to live beside the current report.
    # Relocate the reference as well as the bytes; preserve all other metadata.
    from core import report_material, report_meta
    metadata_path = state/'report_meta.jsonl'
    metadata = [json.loads(line) for line in metadata_path.read_text().splitlines()] if metadata_path.exists() else []
    material_checks = []
    for row in metadata:
        if report_meta.normalize_rel(row.get('rel') or row.get('path', '')) != report_meta.normalize_rel(str(relative)):
            continue
        reference = row.get('material') or {}
        source_material = Path(reference.get('path', '')).resolve()
        original_text = report_material.read(reference, str(report), report.read_text())
        if source_material.is_file() and source_material.parent == report.parent:
            copied = destination.parent/source_material.name
            shutil.copy2(source_material, copied)
            row['material'] = {**reference, 'path': str(copied)}
        isolated_text = report_material.read(row.get('material') or {}, str(destination), destination.read_text())
        assert original_text == isolated_text, 'Isolated writer lost automatic retrieved material'
        material_checks.append({'same_loaded_text': True, 'characters': len(original_text),
                                'sha256': hashlib.sha256(original_text.encode()).hexdigest()})
    if metadata_path.exists():
        metadata_path.write_text(''.join(json.dumps(row, ensure_ascii=False)+'\n' for row in metadata))
    heat = ROOT/'state/video_heat.json'
    if heat.exists():
        shutil.copy2(heat, state/'video_heat.json')
    path = out/'config/system.yaml'
    config = yaml.safe_load(path.read_text())
    config['video'].setdefault('storage', {})['dir'] = str(out/'video-output')
    config['video']['archive'] = {'dir': str(out/'archive'), 'keep_days': 7,
                                'autovideo_data': str(out/'unused-engine-data')}
    path.write_text(yaml.safe_dump(config, allow_unicode=True, sort_keys=False))
    picks = [p for p in spec['picks'] if p.get('production') == production]
    if len(picks) != 1:
        raise ValueError('Expected one unchanged production from the real daily batch')
    selected = {**spec, 'picks': picks, 'report': str(destination)}
    save(out/'input.json', {'source_daily_run': run_id, 'source_spec': spec, 'spec': selected,
        'source_report': str(report), 'report_sha256': hashlib.sha256(report.read_bytes()).hexdigest(),
        'manual_materials': [], 'manual_script_or_storyboard': False,
        'history': {'source': 'read-only production SQLite snapshot', 'exclude_created_at_or_after': raw[1],
                    'retained_scripts': history_count, 'historical_updated_fields_not_reconstructed': True},
        'automatic_heat_cache_sha256': hashlib.sha256(heat.read_bytes()).hexdigest() if heat.exists() else None,
        'automatic_material_checks': material_checks,
        'configuration_changes': ['isolated output storage', 'isolated archive destination'],
        'selection': f'Existing {production} production only; its original selection settings unchanged'})
    print(json.dumps({'prepared': str(out), 'report': str(relative)}, ensure_ascii=False), flush=True)


def run(out: Path, *, inspect_selection: bool = False) -> None:
    from web.settings import get_settings
    settings = get_settings()
    for name in ('config', 'jobs', 'output', 'state', 'logs'):
        setattr(settings.paths, name+'_dir', str(out/name))
    from core import activity, report_meta, search_health, telemetry
    activity.use_state_dir(settings.paths.state_dir)
    report_meta.META_FILE = str(out/'state/report_meta.jsonl')
    search_health.use_state_dir(settings.paths.state_dir)
    telemetry.use_state_dir(settings.paths.state_dir)
    from core.video import autopilot_store as ap, store
    from web.services import video_autopilot as pilot, video_render
    store.use_state_dir(settings.paths.state_dir)
    store.init_db()
    record = json.loads((out/'input.json').read_text())
    assert hashlib.sha256(Path(record['spec']['report']).read_bytes()).hexdigest() == record['report_sha256']
    if inspect_selection:
        notes = []
        items = pilot.pick_topics(record['spec'], notes)
        result = {'items': [{'id': i['topic']['id'], 'title': i['topic']['title']} for i in items], 'notes': notes}
        save(out/'selection-check.json', result)
        print(json.dumps(result, ensure_ascii=False), flush=True)
        return
    client = video_render.get_client()
    client.config_status()
    if (out/'location.json').exists():
        batch_id = json.loads((out/'location.json').read_text())['run_id']
    else:
        batch_id = pilot.start_run(record['spec'], started_by='autopilot')['id']
        save(out/'location.json', {'run_id': batch_id})
    last = None
    deadline = time.monotonic()+7200
    while time.monotonic() < deadline:
        batch = ap.get_run(batch_id)
        pilot._tick_run(batch)
        batch = ap.get_run(batch_id)
        scripts = [store.get_script(i['script_id']) for i in batch['items'] if i.get('script_id')]
        renders = [r for s in scripts for r in store.list_renders(s['id'])]
        for name, value in [('batch', batch), ('scripts', scripts), ('renders', renders)]:
            save(out/f'{name}.json', value)
        for script in scripts:
            if script.get('current_version'):
                save(out/f'version-{script["id"]}.json', store.get_version(script['id'], script['current_version']))
        state = (batch['status'], [(s['status'], s.get('stage')) for s in scripts],
                 [(r['status'], r.get('active_stage')) for r in renders])
        if state != last:
            print(json.dumps({'state': state}, ensure_ascii=False), flush=True)
            last = state
        if batch['status'] == 'DONE':
            if not batch['items'] or any(i['state'] not in {'DONE', 'CANCELLED'} for i in batch['items']):
                raise RuntimeError('Daily run has unfinished or failed items')
            completed = [r for r in renders if r['status'] == 'COMPLETED']
            if not completed:
                raise RuntimeError('Daily workflow completed without a video; no effect acceptance possible')
            receipts = []
            for render in completed:
                job = client.get_job(render['autovideo_job_id'])
                save(out/f'engine-job-{render["id"]}.json', job)
                receipts.append({'render_id': render['id'], 'job_id': render['autovideo_job_id'],
                                 'files_dir': str(video_render.files_dir(render['id']))})
            save(out/'receipt.json', {'status': 'pipeline_completed_effect_not_yet_evaluated',
                'source_daily_run': record['source_daily_run'], 'run_id': batch_id, 'videos': receipts,
                'automatic_skips': [{'topic': i['topic']['title'], 'note': i.get('note'), 'gate': i.get('gate')}
                                    for i in batch['items'] if i['state'] == 'CANCELLED'],
                'manual_materials': [], 'manual_script_or_storyboard': False,
                'publishing': False})
            print(json.dumps({'completed': receipts}, ensure_ascii=False), flush=True)
            return
        if batch['status'] in {'FAILED', 'CANCELLED'} or any(i['state'] == 'HELD' for i in batch['items']):
            raise RuntimeError('Daily workflow stopped; inspect saved batch and logs without bypassing stages')
        time.sleep(3)
    raise TimeoutError('Daily workflow deadline reached; evidence preserved')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--source-run')
    parser.add_argument('--production', default='flash_each')
    parser.add_argument('--run', action='store_true')
    parser.add_argument('--inspect-selection', action='store_true')
    args = parser.parse_args()
    if args.run or args.inspect_selection:
        run(args.output.resolve(), inspect_selection=args.inspect_selection)
    else:
        prepare(args.output.resolve(), args.source_run, args.production)
