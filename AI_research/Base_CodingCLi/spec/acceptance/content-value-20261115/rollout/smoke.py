"""Exercise normal admission/dedup/author paths against frozen automatic material."""
from dataclasses import asdict
import json
import os
from pathlib import Path
import shutil
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
STAGED = Path('/tmp/content-rollout-20261115')
WORK = Path(os.environ.get('CONTENT_ROLLOUT_SMOKE_DIR', '/tmp/content-rollout-smoke-20261115'))


def main():
    WORK.mkdir(exist_ok=False)
    for name in ('config', 'jobs'):
        shutil.copytree(STAGED/name, WORK/name)
    for name in ('state', 'logs'):
        (WORK/name).mkdir()
    sys.path.insert(0, str(STAGED))
    os.chdir(WORK)
    from dotenv import load_dotenv
    load_dotenv(ROOT/'.env', override=False)
    from web.settings import get_settings
    paths = get_settings().paths
    for name in ('config', 'jobs', 'state', 'logs', 'output'):
        setattr(paths, name+'_dir', str(WORK/name))
    from core import activity, telemetry, report_material, report_meta
    activity.use_state_dir(str(WORK/'state'))
    telemetry.use_state_dir(str(WORK/'state'))
    from core.video import audience_strategy as strategy, store, reports, production
    from web.services import video_autopilot as auto, video_workflow as workflow
    store.init_db()
    workflow.spawn = lambda *a, **kw: None  # Run the same worker synchronously below.
    for channel, key in [('flash','flash_daily'),('t05','chatgpt_t05_comparison'),('t06','chatgpt_t06_value')]:
        if os.environ.get('CONTENT_ROLLOUT_CHANNEL') and channel not in os.environ['CONTENT_ROLLOUT_CHANNEL'].split(','):
            continue
        frozen = json.loads((ROOT/'state/acceptance-content-value-20261115/inputs'/f'{channel}.json').read_text())
        selection = strategy.Selection.model_validate_json((HERE.parent/'results'/channel/'selection.json').read_text())
        text = strategy.render_report(selection, strategy.packets(frozen['material']), channel)
        path = WORK/frozen['report_path']
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        material = report_material.save(str(path), text, [frozen['material']])
        job = next(k for k,v in strategy.JOBS.items() if v == channel)
        report_meta.append_record(str(path.relative_to(WORK)), job, extra={'material':material,
            'audience_strategy':{'strategy':strategy.MARKER,'channel':channel}})
        reports.record_file(str(path), job=job)
        config = production.get(paths.config_dir, paths.jobs_dir, key)
        pick = {'production':key, 'column':config.recipe, 'all':True}
        if config.split == 'whole':
            pick['whole'] = True
        else:
            pick['source'] = config.source
        if config.content_mode == 'episode':
            pick['content_mode'] = 'episode'
        picks = auto.pick_topics({'job':job, 'report':str(path), 'picks':[pick]})
        if not picks:
            raise RuntimeError(f'No normal production candidate: {channel}')
        batch = auto.ap.create_run({'job':job,'picks':[pick]},picks,started_by='content-rollout-smoke')
        item = batch['items'][0]
        topic = auto._prepare(item)
        assert strategy.topic_channel(topic) == channel
        print(json.dumps({'channel':channel,'phase':'writing','audience':topic.get('fields',{}).get('目标受众')},ensure_ascii=False),flush=True)
        script = workflow.start(item['column_key'], {**topic,'_production':key}, 'content-rollout-smoke')
        status = workflow.run(script['id'])
        if status != 'DRAFT':
            raise RuntimeError(f'{channel}: {status}: {store.get_script(script["id"]).get("error")}')
        result = store.get_version(script['id'], 1)
        assert result['meta']['audience_strategy']['channel'] == channel
        dest = WORK/'results';dest.mkdir(exist_ok=True)
        (dest/f'{channel}.json').write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n')
        print(json.dumps({'channel':channel,'production':key,'status':status,'title':result['title'],
                          'script_id':script['id'],'strategy':result['meta']['audience_strategy']},ensure_ascii=False),flush=True)


if __name__ == '__main__':
    main()
