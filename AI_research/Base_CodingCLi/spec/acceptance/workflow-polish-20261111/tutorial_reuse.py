"""Reuse the saved actual HTML conversion inputs through the normal tutorial workflow."""
import argparse
import importlib.util
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv
load_dotenv(ROOT / '.env')
spec = importlib.util.spec_from_file_location('matrix_acceptance', ROOT / 'spec/acceptance/matrix-tutorial-20261101/run_matrix.py')
matrix = importlib.util.module_from_spec(spec)
spec.loader.exec_module(matrix)


def prepare(out):
    from core import report_meta, user_materials
    matrix.prepare(out)
    evidence = ROOT / 'spec/acceptance/tutorial-io-20261101/evidence'
    owner = 'matrix:ai_media'
    descriptions = {
        'input': '本地真实输入 weekly.html 网页。项目周报标题，已完成列表：整理项目资料、完成脚本联调；下一步任务与状态表：转换文档进行中、整理结果待开始。',
        'output': '本地 MarkItDown 0.1.9 实际命令生成的 weekly.md 原文。项目周报的 Markdown 一级标题、已完成二级标题和两项列表；下一步 Markdown 表格保留任务和状态，以及转换文档进行中、整理结果待开始。'}
    images = [user_materials.save(str(out / 'state'), owner, name=name+'.png', text=text,
                                 raw=(evidence / (name+'.png')).read_bytes())
              for name, text in descriptions.items()]
    context = {'ids': [r['id'] for r in images], 'owner': owner, 'mode': 'adapt', 'revision': 1,
               'title': '复用真实周报转换素材', 'audience': '需要整理资料的工具使用者',
               'goal': '围绕同一份周报，用实际输入截图、转换命令、实际输出截图讲清三步操作；最后将输入输出并排对照。素材来自已完成的本地操作，命令和输出保持原样。'}
    report_meta.META_DIR = str(out / 'state')
    report_meta.META_FILE = str(out / 'state/report_meta.jsonl')
    report_meta.append_record('output/radar/2026-11-01_ai_opensource_radar.md', 'ai_media/daily_opensource_radar',
                              extra={'user_materials': context})
    matrix.save(out / 'reuse.json', {'source_evidence': str(evidence), 'material_context': context,
                                   'original_conversion_reused': True, 'manual_paragraph_assignments': 0})


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--run', action='store_true')
    args = parser.parse_args()
    (matrix.run if args.run else prepare)(args.output.resolve())
