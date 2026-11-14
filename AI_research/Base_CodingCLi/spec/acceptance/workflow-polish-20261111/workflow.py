"""Isolated normal T05 report -> writer -> material matching -> engine -> download."""
from __future__ import annotations
import argparse
import importlib.util
import json
from pathlib import Path
import shutil
import sys
import yaml

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv
load_dotenv(ROOT / '.env')
spec = importlib.util.spec_from_file_location('matrix_acceptance', ROOT / 'spec/acceptance/matrix-tutorial-20261101/run_matrix.py')
matrix = importlib.util.module_from_spec(spec)
spec.loader.exec_module(matrix)


def prepare(out: Path) -> None:
    from core import report_meta, user_materials
    from core.video import excerpt
    out.mkdir(parents=True, exist_ok=False)
    for name in ('config', 'jobs'):
        shutil.copytree(ROOT / name, out / name)
    system_path = out / 'config/system.yaml'
    system = yaml.safe_load(system_path.read_text())
    system['video'].setdefault('storage', {})['dir'] = str(out / 'video-output')
    system['video']['archive'] = {'dir': str(out / 'archive'), 'keep_days': 7,
                                 'autovideo_data': str(out / 'unused-engine-data')}
    system_path.write_text(yaml.safe_dump(system, allow_unicode=True, sort_keys=False))
    production = out / 'config/production/chatgpt_t05_comparison.yaml'
    config = yaml.safe_load(production.read_text())
    config['minutes'] = 3  # focused acceptance; the live five-minute setting is unchanged
    production.write_text(yaml.safe_dump(config, allow_unicode=True, sort_keys=False))
    source = ROOT / 'output/chatgpt_acquisition/2026-11-10_t05_comparison.md'
    title = '开完会谁写纪要？微信输入法、手机系统录音、ChatGPT 语音同题比'
    report = '# T05 素材呈现试片\n\n' + excerpt.item_excerpt(source.read_text(), title)
    report_path = out / 'output/chatgpt_acquisition/2026-11-11_t05_comparison.md'
    report_path.parent.mkdir(parents=True)
    report_path.write_text(report)
    images = []
    descriptions = {
        'input': '来源文章实拍：微信输入法工具栏右下角的 AI 录音笔入口，放大镜突出按钮。用于讲解进入录音的第一步；不是会议内容或生成结果。',
        'output': '来源文章真实生成结果：微信输入法把拍摄规范与设备登记会议生成设备入库流程图，上行新品到手→统一交梁老师入库登记→新办公室专属仓库，下方三列按归属说明，底部今天启动的待办。用于讲解会议输入如何变为可见流程和行动，不是我们的亲测。',
        'vivo': '来源文章 vivo OriginOS 7 实机结果：三个手机页面并排，左为微信录音转写含说话人，中为洞察，右为数码内容排期及选题工作会议总结。用于讲解系统级通话录音输出，与微信输入法比较操作和交付，不是同一场会的盲测。'}
    for name, description in descriptions.items():
        images.append(user_materials.save(str(out / 'state'), 'matrix:chatgpt_acquisition',
            name=name+'.jpg', text=description+' 来源：https://www.ifanr.com/1688410',
            raw=(out.parent / 'source-images' / (name+'.jpg')).read_bytes()))
    context = {'ids': [i['id'] for i in images], 'owner': 'matrix:chatgpt_acquisition', 'mode': 'adapt', 'revision': 1,
               'title': '会议记录：入口与实际结果', 'audience': '需要整理会议的普通用户',
               'goal': '聚焦微信输入法与 vivo 系统录音两种方案。用来源文章的实际入口与两种结果截图解释开始录音、整理输出、适用情境三个差异，主体沿同一个职场会议任务展开。ChatGPT 没有结果素材，保持待实测，不扩展第三条产品线。使用已有截图和原文内容，不把来源体验说成自己实测。'}
    report_meta.META_DIR = str(out / 'state')
    report_meta.META_FILE = str(out / 'state/report_meta.jsonl')
    report_meta.append_record(str(report_path.relative_to(out)), 'chatgpt_acquisition/t05_comparison', extra={'user_materials': context})
    matrix.save(out / 'preparation.json', {'report': str(report_path), 'source_report': str(source),
        'source_article': 'https://www.ifanr.com/1688410', 'material_context': context,
        'image_records': images, 'normal_business_entry': True, 'manual_paragraph_assignments': 0,
        'manual_script_or_storyboard': False, 'automatic_source_images_globally_enabled': False})
    print(json.dumps({'prepared': str(out), 'registered_images': len(images)}), flush=True)


def rewrite(out: Path) -> None:
    from web.settings import get_settings
    from core import report_meta, telemetry
    from core.video import autopilot_store as ap, store
    from web.services import video_executor, video_workflow
    settings = get_settings()
    for name in ('config', 'jobs', 'output', 'state', 'logs'):
        setattr(settings.paths, name + '_dir', str(out / name))
    report_meta.META_FILE = str(out / 'state/report_meta.jsonl')
    telemetry.use_state_dir(settings.paths.state_dir)
    store.use_state_dir(settings.paths.state_dir)
    run_id = json.loads((out / 'location.json').read_text())['run_id']
    batch = ap.get_run(run_id)
    assert batch['status'] == 'DONE'
    item = batch['items'][0]
    script = store.get_script(item['script_id'])
    version = store.get_version(script['id'], script['current_version'])
    matrix.save(out / ('version-v' + str(version['version']) + '.json'), version)
    matrix.save(out / ('batch-v' + str(version['version']) + '.json'), batch)
    matrix.save(out / ('receipt-v' + str(version['version']) + '.json'), json.loads((out / 'receipt.json').read_text()))
    assert video_workflow.run_rewrite(script['id'], started_by='autopilot', previous_status='APPROVED') == 'DRAFT'
    video_executor.advance_auto(store.get_script(script['id']), {}, can_start_script=False, can_start_render=False)
    assert store.get_script(script['id'])['status'] == 'APPROVED'
    ap.update_item(item['id'], state='WORKING', render_id=None, retries={}, note='同一素材重写，检查画面上下文接入')
    ap.update_run(run_id, status='RUNNING')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--run', action='store_true')
    parser.add_argument('--rewrite', action='store_true')
    args = parser.parse_args()
    if args.run:
        if args.rewrite:
            rewrite(args.output.resolve())
        matrix.run(args.output.resolve(), job_name='chatgpt_acquisition/t05_comparison')
    else:
        prepare(args.output.resolve())
