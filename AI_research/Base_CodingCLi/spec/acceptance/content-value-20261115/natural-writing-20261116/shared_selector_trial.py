"""Replay T05's saved pre-selection inputs through both real selection calls."""
from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from pathlib import Path
import shutil
import sys
import time

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[4]
BASE = ROOT / 'state/acceptance-content-value-20261115/natural-writing-20261116/shared-selection-soft'


def save(path: Path, value) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def main(variant: str) -> None:
    assert variant in {'control', 'candidate', 'no-source-cap'}
    out = BASE / 't05' / variant
    out.mkdir(parents=True, exist_ok=False)
    work = out / 'isolated'
    for folder in ('core', 'utils', 'config'):
        shutil.copytree(ROOT / folder, work / folder,
                        ignore=shutil.ignore_patterns('__pycache__', '*.pyc', '*.lock', '.DS_Store'))
    if variant == 'control':
        for name in ('core/video/audience_strategy.py', 'config/content_strategy.yaml'):
            shutil.copyfile(BASE / 'before' / name, work / name)
    sys.path.insert(0, str(work))
    from core import config, llm, telemetry
    from core.source_supply import material_block
    from core.video import audience_strategy as strategy

    telemetry.use_state_dir(str(out))
    stamp = ROOT / 'output/chatgpt_acquisition/2026-11-16_t05_comparison.md'
    receipt = json.loads(Path(str(stamp) + '.source-supply.json').read_text())
    archived = json.loads(next(stamp.parent.glob(stamp.stem + '.materials-*.json')).read_text())
    before_selection = json.loads(next(stamp.parent.glob(stamp.name + '.audience-initial-*.json')).read_text())['report']
    prepared = json.loads(next(stamp.parent.glob(stamp.name + '.audience-[0-9a-f]*.json')).read_text())
    assert digest(stamp.read_text()) == archived['report_sha256']
    assert prepared['initial_report'] == before_selection
    separator = '\n\n---\n\n'
    source_prefix, source_suffix = material_block([], receipt).split(
        '已成功读取来源，本次没有新增或修改材料；不要将上次事件重新当作新变化。', 1)
    material = archived['material']
    boundary = source_suffix + separator
    assert material.startswith(source_prefix) and material.count(boundary) == 1
    source_end = material.index(boundary) + len(source_suffix)
    used = []
    for topic in prepared['question_material']['topics']:
        if topic['status'] == 'enriched':
            ids = set(topic['organization']['used_ids'])
            used.extend(item['text'] for item in topic['retrieved_sources'] if item['id'] in ids)
    supplement = separator.join(dict.fromkeys(used)).strip()
    assert supplement and material.endswith(separator + supplement)
    dossier = material[source_end + len(separator):-(len(separator) + len(supplement))]
    material = material[:source_end] + '\n\n' + dossier

    job = config.load_job(str(ROOT / 'jobs'), 'chatgpt_acquisition/t05_comparison')
    assert strategy.job_channel(job) == 't05'
    load_dotenv(ROOT / '.env', override=False)
    cfg = llm.LLMConfig.from_system(config.load_system_config(str(work / 'config')), 'ai')
    assert cfg.base_url == 'https://opencode.ai/zen/go/v1' and cfg.model == 'deepseek-v4.1-flash'
    cfg = replace(cfg, fallback_models=[], fallback_providers=[])
    save(out / 'receipt.json', {
        'report': str(stamp), 'initial_report_sha256': digest(before_selection),
        'selection_supply_sha256': digest(material), 'supply_chars': len(material),
        'strategy_version': strategy.VERSION,
        'interventions': ['saved automatic initial research and material from actual production run',
                          'remove later supplements using receipt', 'real two-stage selection only'],
    })
    save(out / 'status.json', {'status': 'running'})
    call_number = 0

    class CapturedClient(llm.LLMClient):
        def chat(self, messages, **options):
            nonlocal call_number
            call_number += 1
            key = call_number
            save(out / f'{key}-request.json', {'messages': messages, 'options': options})
            result = super().chat(messages, **options)
            save(out / f'{key}-response.json', {'content': result.content, 'usage': dict(self.usage)})
            return result

    client = CapturedClient(cfg)
    started = time.monotonic()
    try:
        items = strategy.packets(material)
        selection = strategy.select(client, 't05', items, before_selection, job=job)
        save(out / 'selection.json', selection.model_dump())
        (out / 'research.txt').write_text(
            strategy.render_report(selection, items, 't05', original_report=before_selection))
        save(out / 'status.json', {'status': 'selected', 'candidates': len(selection.candidates),
                                   'calls': call_number, 'seconds': round(time.monotonic() - started, 1)})
    except Exception as exc:
        save(out / 'status.json', {'status': 'failed', 'error_type': type(exc).__name__, 'calls': call_number})
        raise
    finally:
        client.close()


if __name__ == '__main__':
    variant, = sys.argv[1:]
    main(variant)
