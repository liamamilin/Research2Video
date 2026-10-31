"""Export the three selected samples, source PNGs and actual frame evidence."""
import hashlib
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
BASE = ROOT / 'state/validation/static-screenshots-20261028'
DEST = Path('/Volumes/个人资料/2026/视频生成工作流/AI创作/矩阵产品化/AI/20261028-静态截图与分镜首轮')
selected = [('product-v3', 'product', '01-产品功能-n8n'),
            ('opensource-v4', 'opensource', '02-开源项目-MarkItDown'),
            ('code-docs-v3', 'documentation', '03-文档示例-Playwright')]
records, lines = [], ['# 静态截图与分镜 · 首轮样片', '',
    '三条短样片使用公开页面的真实静态截图，复用现有本地中文配音与混合渲染。每条自动选取一张素材并关联到讲稿，分镜安排整体、局部放大和标注。', '',
    '声音为既有 Kokoro zf_xiaoxiao。这批检查画面表达与音画对应，没有录屏、AI 视频生成、事实核验或新增人工确认。', '']
assert not (DEST/'作品目录.md').exists(), 'Keep the completed delivery; choose a fresh destination for another export.'
for batch, key, label in selected:
    result = json.loads((BASE/batch/'results.json').read_text())[key]
    assert result['status'] == 'COMPLETED' and result['images'] and result['mapped_scenes']
    assert set(result['decode']) == {'video.mp4', 'content.mp4'} and all(code == 0 for code in result['decode'].values())
    source = BASE/batch/key
    evidence = json.loads((source/'visual-evidence.json').read_text())
    render = json.loads((source/'render.json').read_text())
    target = DEST/label
    target.mkdir(parents=True, exist_ok=True)
    for name in ('video.mp4', 'content.mp4', 'contact-sheet.png', 'visual-evidence.json', 'qa.json', 'package-qa.json'):
        shutil.copyfile(source/name, target/name)
    shutil.copytree(source/'frames', target/'实际画面', dirs_exist_ok=True)
    (target/'讲稿.txt').write_text('\n\n'.join(p for c in render['request']['chapters'] for p in c['paragraphs']))
    image_dir = target/'真实截图'
    image_dir.mkdir(exist_ok=True)
    for i, material in enumerate(render['visuals']['selection']['materials'], 1):
        # The screenshot is frozen in this render's isolated ledger, even when
        # it reused a previously verified PNG from another isolated sample.
        state = (BASE/'samples-v2/state') if batch in {'code-docs-v3', 'opensource-v4'} else BASE/batch/'state'
        screenshot = state/'video_screenshots'/material['screenshot_id']
        shutil.copyfile(screenshot/'image.png', image_dir/f'{i:02d}.png')
        shutil.copyfile(screenshot/'record.json', image_dir/f'{i:02d}.json')
    record = {**result, 'duration_seconds': round(evidence['duration_seconds'], 3), 'delivery': str(target),
              'content_sha256': hashlib.sha256((target/'content.mp4').read_bytes()).hexdigest(),
              'video_sha256': hashlib.sha256((target/'video.mp4').read_bytes()).hexdigest(),
              'region_cues': sum(any(t.startswith('region:') for t in cue['targets'])
                                 for scene in evidence['scenes'] for cue in (scene.get('shot_plan') or {}).get('cues', []))}
    records.append(record)
    lines += [f'## {label}', '',
        f"{record['duration_seconds']:.2f} 秒；{record['images']} 张真实截图，{record['mapped_scenes']} 个截图场景，{record['region_cues']} 个局部镜头提示。", '',
        f'[播放成片](<{target}/video.mp4>) · [观看实际画面](<{target}/contact-sheet.png>) · [讲稿](<{target}/讲稿.txt>)', '',
        f"[公开来源]({record['source_url']})", '']
lines += ['原始输入、失败与中间尝试保留在工作区本轮隔离验收目录。产品早期一次渲染出现平铺画面，被原媒体检查拒绝；单帧复现正常，原因未确认，最终选用片已通过媒体检查与完整解码。', '',
          '研究侧截图选择和镜头接入代码已修改；本轮未主动重启正式研究服务。既有自动任务、发布记录和历史成片未改动。', '']
(DEST/'作品目录.md').write_text('\n'.join(lines))
(BASE/'delivery.json').write_text(json.dumps({'destination': str(DEST), 'samples': records}, ensure_ascii=False, indent=2))
print(json.dumps({'destination': str(DEST), 'samples': records}, ensure_ascii=False, indent=2))
