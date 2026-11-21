"""Validate the live selector on the first automatically discovered warm item."""
from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
import json
from pathlib import Path
import sys
from types import SimpleNamespace

from dotenv import load_dotenv

from flash_live_entry import BASE, ROOT, save

sys.path.insert(0, str(ROOT))


def main():
    from core import config, llm, telemetry
    from core.video import audience_strategy as strategy

    warm = BASE / 'flash-live/latest-warm'
    requests = sorted((warm / 'calls').glob('*-request.json'))
    captured = next(p for p in requests if '现在读取所选原文' in json.loads(
        p.read_text())['messages'][0]['content'])
    user = json.loads(captured.read_text())['messages'][-1]['content']
    prefix, remainder = user.split('\n初选：\n', 1)
    candidates, originals = remainder.split('\n原文：\n', 1)
    scope = json.loads(prefix.split('其中报告排版要求不改变本步骤的JSON输出格式）：\n', 1)[1].splitlines()[0])
    initial = strategy.Selection.model_validate_json(candidates)
    first = initial.candidates[0]
    assert 'DeepSeek' in first.title and '额度' in first.title
    items = [p for p in json.loads(originals) if p['id'] in first.source_ids]
    first_pass = strategy.Selection(candidates=[first])
    strategy.validate_sources(first_pass, items)

    out = BASE / 'warm-selection-entry'
    out.mkdir(parents=True, exist_ok=False)
    telemetry.use_state_dir(str(out))
    save(out / 'items.json', items)
    save(out / 'receipt.json', {
        'state': 'one item from the actual first automatic selection of fresh sources',
        'original_capture': str(captured), 'file_sha256': sha256(captured.read_bytes()).hexdigest(),
        'job': 'monitoring/ai_flash_discovery', 'selector_version': strategy.VERSION,
        'selector_sha256': sha256((ROOT / 'core/video/audience_strategy.py').read_bytes()).hexdigest(),
        'initial_title': first.title, 'source_ids': first.source_ids,
        'interventions': ['replay the first-ranked automatic candidate and its actual original documents',
                          'perform the real selected-source pass using the current implementation']})
    save(out / 'status.json', {'status': 'reading_selected_sources'})
    load_dotenv(ROOT / '.env', override=False)
    cfg = llm.LLMConfig.from_system(config.load_system_config(str(ROOT / 'config')), 'ai')
    assert cfg.model == 'deepseek-v4.1-flash' and cfg.base_url == 'https://opencode.ai/zen/go/v1'
    cfg = replace(cfg, fallback_models=[], fallback_providers=[])

    class CapturedClient(llm.LLMClient):
        calls = 0

        def chat(self, messages, **options):
            self.calls += 1
            if self.calls == 1:
                return SimpleNamespace(content=first_pass.model_dump_json())
            save(out / f'{self.calls}-request.json', {'messages': messages, 'options': options})
            reply = super().chat(messages, **options)
            save(out / f'{self.calls}-response.json', {'content': reply.content, 'usage': dict(self.usage)})
            return reply

    client = CapturedClient(cfg)
    try:
        result = strategy.select(client, 'flash', items, '', job=scope)
        if not result.candidates:
            raise RuntimeError('No supported item after reading the selected original sources.')
        save(out / 'selection.json', result.model_dump())
        save(out / 'status.json', {'status': 'selected', 'network_calls': client.calls - 1})
    except Exception as exc:
        save(out / 'status.json', {'status': 'failed', 'error_type': type(exc).__name__})
        raise
    finally:
        client.close()


if __name__ == '__main__':
    main()
