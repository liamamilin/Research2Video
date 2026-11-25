"""Serial, isolated content experiment. Never submits production tasks."""
from __future__ import annotations

import argparse
from dataclasses import asdict, replace
import hashlib
import json
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[3]
BASE = Path('/tmp/content-value-20261115')
REPORTS = {
    'flash': 'output/flash/2026-11-15_ai_flash_discovery.md',
    't05': 'output/chatgpt_acquisition/2026-11-15_t05_comparison.md',
    't06': 'output/chatgpt_acquisition/2026-11-13_t06_value.md',
}
PRODUCTION = {'flash': 'flash_each', 't05': 'chatgpt_t05_comparison', 't06': 'chatgpt_t06_value'}


def save(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')


def digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def freeze() -> None:
    """Read complete automatic source snapshots, with report identity preserved."""
    for channel, relative in REPORTS.items():
        source = ROOT / relative
        report = source.read_text()
        matches = []
        for path in source.parent.glob(source.stem + '.materials-*.json'):
            value = json.loads(path.read_text())
            if value.get('report_sha256') == digest(report):
                matches.append((path, value['material']))
        if len(matches) != 1:
            raise ValueError(f'{channel}: expected one matching material snapshot, found {len(matches)}')
        path, material = matches[0]
        save(BASE / 'inputs' / f'{channel}.json', {'report_path': relative, 'report': report, 'material': material,
             'report_sha256': digest(report), 'material_sha256': digest(material), 'material_path': str(path)})


def setup(variant: str):
    work = BASE / ('candidate' if variant in ('candidate', 'matched') else 'baseline')
    sys.path.insert(0, str(work))
    os.chdir(work)
    from dotenv import load_dotenv
    # Credentials stay in the existing ignored file, never copied into artifacts.
    load_dotenv(ROOT / '.env', override=False)
    from web.settings import get_settings
    settings = get_settings()
    for name in ('config', 'jobs', 'state', 'logs'):
        setattr(settings.paths, name + '_dir', str(work / name))
    from core import activity, telemetry
    activity.use_state_dir(str(work / 'state'))
    telemetry.use_state_dir(str(work / 'state'))
    from core.llm import LLMConfig
    import yaml
    cfg = LLMConfig.from_system(yaml.safe_load((work / 'config/system.yaml').read_text()), 'ai')
    # Same model/provider in both arms. Limit only this experiment's output/retries.
    return work, replace(cfg, max_tokens=16000, max_retries=1)


def run(variant: str, channel: str, stage: str) -> None:
    work, cfg = setup(variant)
    from core.video import columns, flash, production, topic_store, writer
    from core import llm
    data = json.loads((BASE / 'inputs' / f'{channel}.json').read_text())
    report, material = data['report'], data['material']
    out = BASE / 'results' / channel / variant
    out.mkdir(parents=True, exist_ok=True)
    class RecordedClient(llm.LLMClient):
        def chat(self, messages, **kwargs):
            stamp = str(time.time_ns())
            save(out/'calls'/f'{stamp}-request.json', {'messages': messages, 'options': kwargs,
                 'model': self.config.model})
            started = time.monotonic()
            try:
                response = super().chat(messages, **kwargs)
            except Exception as exc:
                save(out/'calls'/f'{stamp}-error.json', {'type': type(exc).__name__,
                     'seconds': time.monotonic()-started})
                raise
            save(out/'calls'/f'{stamp}-response.json', {'content': response.content,
                 'model': getattr(response, 'model', ''), 'usage': dict(self.usage),
                 'seconds': time.monotonic()-started})
            return response
    llm.LLMClient = RecordedClient  # Only this isolated command's process.
    LLMClient = RecordedClient
    day = Path(data['report_path']).name[:10]
    conf = production.get(str(work/'config'), str(work/'jobs'), PRODUCTION[channel])
    if conf is None or conf.errors:
        raise RuntimeError(f'Invalid production config: {conf}')
    column = columns.get(str(work/'config'), conf.recipe)
    original = flash.parse(report, day) if channel == 'flash' else topic_store.parse_items(report, day, 'CGE' if channel == 't05' else 'CGF')
    save(out/'original-candidates.json', [t.as_dict() for t in original])
    if variant in ('candidate', 'matched'):
        from core.video import audience_strategy as strategy
        packets = strategy.packets(material)
        selection_path = BASE/'results'/channel/'candidate'/'selection.json'
        if not selection_path.exists():
            started = time.monotonic()
            client = LLMClient(cfg)
            try:
                selection = strategy.select(client, channel, packets, report)
                save(selection_path, selection.model_dump())
                save(out/'selection-receipt.json', {'model': cfg.model, 'usage': dict(client.usage),
                     'seconds': time.monotonic()-started, 'material_sha256': digest(material), 'sources': len(packets),
                     'model_switches': list(getattr(client, 'model_switches', []))})
            finally:
                client.close()
        selection = strategy.Selection.model_validate_json(selection_path.read_text())
        chosen = selection.candidates[:2 if variant == 'candidate' else 1]
        inputs = []
        for candidate in chosen:
            value = candidate.model_dump()
            topic = {'title': candidate.title, 'fields': {'目标受众': candidate.audience, '核心角度': candidate.promise}}
            if variant == 'candidate':
                topic['audience_brief'] = value
            inputs.append((topic, candidate.material, strategy.author_material(candidate, packets)))
    else:
        inputs = [(t.as_dict(), report, material) for t in original[:2]]
    if stage == 'select':
        print(json.dumps({'channel': channel, 'variant': variant, 'stage': 'selected', 'titles': [i[0]['title'] for i in inputs]}, ensure_ascii=False), flush=True)
        return
    for index, (topic, research, sources) in enumerate(inputs, 1):
        target = out/f'script-{index}.json'
        if target.exists():
            continue
        style = column.script_style
        if variant == 'candidate':
            style = '面向选题指定的中文观众，以本期观看承诺组织完整口播。根据问题采用比较、事件、工作变化或解释，不强制单个任务。'
        started = time.monotonic()
        save(out/f'author-input-{index}.json', {'topic': topic, 'report': research, 'original_material': sources,
             'material_sha256': digest(sources), 'column_key': column.key, 'target_minutes': column.target_minutes,
             'model': cfg.model, 'column_style': style})
        script = writer.write_for_column(column=column, content_context={}, column_style=style,
                    report=research, citation_check=None, topic=topic, llm_config=cfg, original_material=sources)
        save(target, asdict(script))
        text = script.title + '\n\n' + '\n\n'.join('\n'.join(p['text'] for p in s['paragraphs']) for s in script.sections)
        (out/f'script-{index}.txt').write_text(text + '\n')
        print(json.dumps({'channel':channel, 'variant':variant, 'script':index, 'title':script.title,
                         'seconds':round(time.monotonic()-started,1), 'chars':len(text)}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('variant', choices=['freeze', 'baseline', 'candidate', 'matched'])
    parser.add_argument('--channel', choices=list(REPORTS), default='flash')
    parser.add_argument('--stage', choices=['select', 'write'], default='write')
    args = parser.parse_args()
    if args.variant == 'freeze':
        freeze()
    else:
        run(args.variant, args.channel, args.stage)
