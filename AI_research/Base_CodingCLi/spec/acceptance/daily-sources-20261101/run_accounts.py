"""Public source → chosen tutorial research → existing matrix author and renderer.

No personal browser, account login, recording or manually placed pictures.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
import hashlib
import importlib.util
import json
import logging
from pathlib import Path
import shutil
import sys
import time

import yaml

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
JOB = 'chatgpt_acquisition/t03_tutorials'
ARTICLE = 'https://aicatchup.com/news/chatgpt-account-switching-mobile'
OFFICIAL = 'https://help.openai.com/en/articles/20001068-using-multiple-chatgpt-accounts-on-web-and-mobile'
spec = importlib.util.spec_from_file_location('matrix_acceptance', ROOT / 'spec/acceptance/matrix-tutorial-20261101/run_matrix.py')
matrix = importlib.util.module_from_spec(spec)
spec.loader.exec_module(matrix)


def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')


def setup_paths(out):
    from web.settings import get_settings
    settings = get_settings()
    for name in ['config', 'jobs', 'output', 'state', 'logs']:
        setattr(settings.paths, name + '_dir', str(out / name))
    from core import activity, events, lock, report_meta, state
    state.use_state_dir(str(out / 'state'))
    activity.use_state_dir(str(out / 'state'))
    events.use_state_dir(str(out / 'state'))
    lock._LOCK_DIR = str(out / 'state/locks')
    report_meta.META_DIR = str(out / 'state')
    report_meta.META_FILE = str(out / 'state/report_meta.jsonl')


def prepare(out):
    out.mkdir(parents=True, exist_ok=False)
    for name in ['config', 'jobs']:
        shutil.copytree(ROOT / name, out / name)
    system_path = out / 'config/system.yaml'
    system = yaml.safe_load(system_path.read_text())
    system.setdefault('notifications', {})['enabled'] = False
    system['video'].setdefault('storage', {})['dir'] = str(out / 'video-output')
    system['video']['archive'] = {'dir': str(out / 'archive'), 'keep_days': 7,
                                 'autovideo_data': str(out / 'unused-engine-data')}
    system_path.write_text(yaml.safe_dump(system, allow_unicode=True, sort_keys=False))
    save(out / 'preparation.json', {'topic': 'ChatGPT 双账号切换', 'job': JOB,
        'production': 'chatgpt_t03_tutorials', 'source': ARTICLE, 'official_reference': OFFICIAL,
        'config_snapshot': True, 'isolated_state': True, 'personal_account_required': False,
        'manual_script_or_storyboard': False, 'manual_image_assignments': 0,
        'source_sha256': {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
            for name in ['core/source_pages.py', 'core/source_supply.py', 'core/material_collection.py',
                         'core/video/auto.py', 'core/video/topic_store.py', 'web/services/video_autopilot.py']}})


def public_sources(out):
    from core.config import load_job
    from core import source_supply, sources
    from core.material_archive import MaterialArchive
    config = str(out / 'config')
    ai = load_job(str(out / 'jobs'), 'monitoring/ai_flash_discovery')
    cg = load_job(str(out / 'jobs'), 'chatgpt_acquisition/t09_updates')
    common = sorted(set(sources.resolve(ai['sources'], config)) & set(sources.resolve(cg['sources'], config)))
    selected = [s for s in common if s in {
        'rss:https://openai.com/news/rss.xml', 'rss:https://aicatchup.com/rss.xml',
        'rss:https://ratelimitnow.com/whats-new.xml',
        'page:https://learn.chatgpt.com/docs/whats-new.md',
        'page:https://developers.openai.com/api/docs/changelog.md'}]
    selected += ['catalog:https://learn.chatgpt.com/videos', 'github:anthropics/claude-code']
    def fetch(spec):
        try:
            if spec.startswith(('page:', 'catalog:')):
                kind, url = spec.split(':', 1)
                rows = source_supply.read_page(url, limit=80, timeout=20, catalog=kind == 'catalog')
            else:
                rows = sources.fetch_source(spec, limit=80, timeout=20)
            return spec, rows, ''
        except Exception as exc:
            return spec, [], str(exc)[:300]
    archive = MaterialArchive(out / 'state')
    receipts = []
    for spec, rows, error in ThreadPoolExecutor(max_workers=4).map(fetch, selected):
        archive.record(spec, rows, error=error)
        save(out / 'source-proof' / (hashlib.sha256(spec.encode()).hexdigest()[:12] + '.json'),
             {'spec': spec, 'items': [asdict(r) for r in rows], 'error': error})
        receipts.append({'spec': spec, 'items': len(rows), 'error': error,
                         'archived_items': len(archive.read(spec)[0])})
        print(json.dumps(receipts[-1], ensure_ascii=False), flush=True)
    save(out / 'public-source-proof.json', {'common_sources': common, 'reads': receipts,
        'shared_archive': str(archive.path), 'paid_content_reads': 0, 'personal_login': False})


def research(out):
    setup_paths(out)
    from core.config import load_job
    from core.engine import ResearchEngine
    job = load_job(str(out / 'jobs'), JOB)
    # The user's chosen topic is an ordinary scoped research input. Production
    # T03 and its recurring schedule are not edited for this acceptance run.
    job['sources'] = ['page:' + ARTICLE]
    job['max_searches'] = 0
    job['prompt'] += ('\n\n本次已确定唯一主题：ChatGPT 双账号切换。只产出这一项候选，使用公开来源中的具体步骤，'
        '分别讲清网页添加账号、手机切换账号、确认数据与订阅各自独立。'
        '画面可用公开资料截图与菜单路径示意；不依赖个人账号登录或录屏，不声称作者已经亲测。'
        '菜单路径保留 Add account、Settings、Switch account 等实际名称，旁白自然说明。'
        '制作一条简洁可跟随的教程，不扩展账号注册、付款或套餐百科。')
    save(out / 'research-input.json', {'job': JOB, 'sources': job['sources'],
         'chosen_topic': 'ChatGPT 双账号切换', 'search': False, 'login_required': False})
    engine = ResearchEngine(config_dir=str(out / 'config'), jobs_dir=str(out / 'jobs'),
                            workspace_dir=str(out), state_dir=str(out / 'state'), user='public-tutorial-acceptance')
    engine.run_context = {'job': job}
    report = engine.run_job(JOB)
    assert report, 'public tutorial research failed; original attempt remains on disk'
    from core.video import topic_store
    source = topic_store.resolve(str(out / 'jobs'), JOB)
    assert topic_store.report_has_content(source, report, str(out))
    save(out / 'research-receipt.json', {'report': report, 'bytes': (out / report).stat().st_size,
        'source': ARTICLE, 'anonymous_public_source': True, 'manual_report_edit': False})
    print(json.dumps({'research_report': report}, ensure_ascii=False), flush=True)


def preview(out):
    setup_paths(out)
    from core.video import store, visual_materials
    from web.services.video_visual_acquire import acquire
    store.use_state_dir(str(out / 'state'))
    render = json.loads((out / 'renders.json').read_text())[0]
    version = json.loads((out / 'version.json').read_text())
    references = [{'url': url, 'title': 'ChatGPT account switching'}
                  for url in render['visuals']['needs'][0]['urls']]
    need = visual_materials.needs_for(version, references)[0]
    raw, metadata = acquire(need, need['urls'][0], render['visuals']['policy'], remaining=55)
    folder = out / 'preview'
    folder.mkdir(exist_ok=True)
    name = 'public-step-' + str(time.time_ns())
    (folder / (name + '.png')).write_bytes(raw)
    proof = {'need': need, 'capture': metadata, 'image': str(folder / (name + '.png'))}
    save(folder / (name + '.json'), proof)
    save(folder / 'latest.json', proof)
    region = metadata['quality']['region_text']
    assert 'RELATED COVERAGE' not in region and metadata['quality'].get('heading') != 'Read next'
    print(json.dumps({'subject': need['visual_subject'], 'heading': metadata['quality'].get('heading'),
                      'region_text': region[:350], 'image': proof['image']}, ensure_ascii=False))


def rerender(out):
    setup_paths(out)
    from core.video import store
    from web.services import video_render
    store.use_state_dir(str(out / 'state'))
    end = time.monotonic() + 3600
    while not (out / 'receipt.json').exists():
        if time.monotonic() > end:
            raise TimeoutError('baseline render has not completed')
        time.sleep(2)
    baseline = json.loads((out / 'receipt.json').read_text())
    for name in ['receipt.json', 'renders.json', 'engine-job.json', 'batch.json']:
        shutil.copy2(out / name, out / ('baseline-' + name))
    script = store.get_script(baseline['script_id'])
    made = video_render.create(script)
    video_render.start(made['id'])
    client, previous = video_render.get_client(), None
    while time.monotonic() < end:
        current = store.get_render(made['id'])
        save(out / 'renders.json', store.list_renders(script['id']))
        state = (current['status'], current.get('active_stage'), current.get('autovideo_job_id'))
        if state != previous:
            print(json.dumps({'rerender': state}, ensure_ascii=False), flush=True)
            previous = state
        if current['status'] == 'FAILED':
            raise RuntimeError(current.get('error_message'))
        if current['status'] == 'COMPLETED':
            job = client.get_job(current['autovideo_job_id'])
            save(out / 'engine-job.json', job)
            final = {**baseline, 'render_id': current['id'], 'job_id': current['autovideo_job_id'],
                'files_dir': str(video_render.files_dir(current['id'])),
                'render_attempts': len(store.list_renders(script['id'])),
                'same_approved_script_version': True, 'public_selection_fix_rerender': True}
            save(out / 'receipt.json', final)
            print(json.dumps({'completed': final['job_id'], 'video': str(video_render.files_dir(current['id']) / 'video.mp4')}))
            return
        time.sleep(2)
    raise TimeoutError('public tutorial rerender exceeded acceptance deadline')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--sources', action='store_true')
    parser.add_argument('--research', action='store_true')
    parser.add_argument('--run', action='store_true')
    parser.add_argument('--preview', action='store_true')
    parser.add_argument('--rerender', action='store_true')
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    out = args.output.resolve()
    if args.sources:
        public_sources(out)
    elif args.research:
        research(out)
    elif args.run:
        matrix.run(out, job_name=JOB)
    elif args.preview:
        preview(out)
    elif args.rerender:
        rerender(out)
    else:
        prepare(out)
