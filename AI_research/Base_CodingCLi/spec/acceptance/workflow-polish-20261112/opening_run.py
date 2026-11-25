"""Exercise the production opening author and normal render entry in isolated state."""
from __future__ import annotations
import argparse
import copy
import json
from pathlib import Path
import shutil
import sqlite3
import sys
import time
import yaml

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
TARGET = 'b2e218c2a2744b8ea0a505e44e6c169b'


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n')


def setup(out):
    if not out.exists():
        out.mkdir(parents=True)
        for name in ('config', 'jobs'):
            shutil.copytree(ROOT/name, out/name)
        (out/'state').mkdir()
        with sqlite3.connect(f'file:{ROOT/"state/video.db"}?mode=ro', uri=True) as source:
            with sqlite3.connect(out/'state/video.db') as destination:
                source.backup(destination)
        shutil.copy2(ROOT/'state/report_meta.jsonl', out/'state/report_meta.jsonl')
        path = out/'config/system.yaml'
        config = yaml.safe_load(path.read_text())
        config['video'].setdefault('storage', {})['dir'] = str(out/'video-output')
        config['video']['archive'] = {'dir': str(out/'archive'), 'keep_days': 7, 'autovideo_data': str(out/'unused-engine-data')}
        path.write_text(yaml.safe_dump(config, allow_unicode=True, sort_keys=False))
    from web.settings import get_settings
    settings = get_settings()
    for name in ('config', 'jobs', 'state', 'logs'):
        setattr(settings.paths, name+'_dir', str(out/name))
    from core import activity, report_meta, telemetry
    activity.use_state_dir(str(out/'state'))
    telemetry.use_state_dir(str(out/'state'))
    report_meta.META_FILE = str(out/'state/report_meta.jsonl')
    from core.video import store
    store.use_state_dir(str(out/'state'))
    store.init_db()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--mode', choices=['repair', 'rewrite', 'render'], required=True)
    parser.add_argument('--script', default=TARGET)
    args = parser.parse_args()
    out = args.output.resolve()
    setup(out)
    from core.video import store, writer, persona
    from core.llm import LLMClient
    from web.services import video_workflow as workflow, video_executor as executor, video_render
    script = store.get_script(args.script)
    report = Path(script['report_path'])
    if not report.is_absolute():
        report = ROOT/report
    destination = out/'output'/report.relative_to(ROOT/'output')
    destination.parent.mkdir(parents=True, exist_ok=True)
    for source_file in report.parent.iterdir():
        if source_file.is_file():
            shutil.copy2(source_file, destination.parent/source_file.name)
    if args.mode == 'repair':
        original = store.get_version(args.script, 1)
        save(out/'before.json', original)
        parsed = writer._Script.model_validate(original)
        config, _ = workflow._llm_configs(None)
        client = LLMClient(config)
        changed, receipt = writer.introduce_opening(parsed, persona.load(), client)
        attempt_path = out/f'opening-attempt-{time.time_ns()}.json'
        save(attempt_path, {'result': changed.model_dump(), 'receipt': receipt, 'usage': dict(client.usage)})
        if receipt['status'] != 'rewritten':
            raise RuntimeError('Opening was not repaired; inspect retained attempt')
        sections = copy.deepcopy(original['sections'])
        sections[0]['paragraphs'] = [p.model_dump() for p in changed.sections[0].paragraphs]
        version = workflow.save_edit(args.script, title=original['title'], sections=sections,
                                     edited_by='opening-validation', base_version=1)
        save(out/'after.json', store.get_version(args.script, version))
        save(out/'interventions.json', {'selected_real_script': args.script,
             'manual_script_or_storyboard': False, 'manual_materials': [], 'publishing': False,
             'entry': 'writer.introduce_opening -> save_edit -> approve -> render',
             'scope': 'Existing script correction; not a fresh full authoring run',
             'unchanged_later_sections': sections[1:] == original['sections'][1:]})
        print(json.dumps({'version': version, 'opening': sections[0], 'receipt': receipt}, ensure_ascii=False), flush=True)
    elif args.mode == 'rewrite':
        # Preserve all automatically acquired report/material bytes and their paths.
        source = Path(script['report_path'])
        if not source.is_absolute():
            source = ROOT/source
        destination = out/'output'/source.relative_to(ROOT/'output')
        destination.parent.mkdir(parents=True, exist_ok=True)
        for f in source.parent.iterdir():
            if f.is_file():
                shutil.copy2(f, destination.parent/f.name)
        # The report reader expects material alongside the report, and validates it.
        meta = out/'state/report_meta.jsonl'
        rows = [json.loads(line) for line in meta.read_text().splitlines()]
        for row in rows:
            reference = row.get('material') or {}
            p = Path(reference.get('path', ''))
            if p.is_file() and p.parent == source.parent:
                row['material'] = {**reference, 'path': str(destination.parent/p.name)}
        meta.write_text(''.join(json.dumps(row, ensure_ascii=False)+'\n' for row in rows))
        workflow.rewrite(args.script, started_by='opening-validation')
        deadline = time.monotonic()+3600
        while time.monotonic() < deadline:
            script = store.get_script(args.script)
            if script['status'] != 'WRITING':
                break
            time.sleep(3)
        save(out/f'script-{args.script}.json', script)
        version = store.get_version(args.script, script['current_version'])
        save(out/f'version-{args.script}.json', version)
        if script.get('error') or version['version'] <= 1:
            raise RuntimeError(script.get('error') or 'No new version')
        print(json.dumps({'script': args.script, 'version': version['version'], 'opening': version['sections'][0],
                          'prompt': version['meta'].get('prompt_version'), 'introduction': version['meta'].get('introduction')}, ensure_ascii=False), flush=True)
    else:
        location = out/f'render-{args.script}.json'
        if location.exists():
            rid = json.loads(location.read_text())['id']
            video_render.start(rid)
        else:
            executor.approve(args.script, script['current_version'], 'opening-validation')
            render = executor.render(args.script, 'opening-validation')
            rid = render['id']
            save(location, render)
        previous = None
        while True:
            render = store.get_render(rid)
            state = (render['status'], render.get('active_stage'), render.get('autovideo_job_id'))
            if previous != state:
                print(json.dumps({'render': rid, 'state': state}, ensure_ascii=False), flush=True)
                previous = state
                save(location, render)
            if render['status'] in ('COMPLETED', 'FAILED', 'CANCELLED'):
                break
            time.sleep(5)
        save(location, render)
        if render['status'] != 'COMPLETED':
            raise RuntimeError('Render did not complete: '+str(render.get('error')))
        print(json.dumps({'video': str(video_render.files_dir(rid)/'video.mp4')}, ensure_ascii=False), flush=True)

if __name__ == '__main__':
    main()
