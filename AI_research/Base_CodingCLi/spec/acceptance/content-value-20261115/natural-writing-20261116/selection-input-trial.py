"""Isolate the handoff into the selected-source reading pass."""
from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
import json
from pathlib import Path
import sys
import time

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[4]
BASE = ROOT / 'state/acceptance-content-value-20261115/natural-writing-20261116'
sys.path.insert(0, str(ROOT))


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def selected_request(case):
    if case == 't05':
        return BASE / 'shared-selection-soft/t05/no-source-cap/3-request.json'
    paths = sorted((BASE / 'flash-live/same-supply-full-sources/calls').glob('*-request.json'))
    return next(p for p in paths if '现在读取所选原文' in json.loads(p.read_text())['messages'][0]['content'])


def main(case):
    from core import config, llm, telemetry
    from core.video import audience_strategy
    from core.video.adapt.common import call_json

    request = selected_request(case)
    payload = json.loads(request.read_text())
    system, user = (m['content'] for m in payload['messages'])
    prefix, shortlist = user.split('\n初选：\n', 1)
    first, sources = shortlist.split('\n原文：\n', 1)
    candidates = json.loads(first)['candidates']
    chosen = json.loads(sources)
    fields = ('title', 'audience', 'source_ids', 'series', 'topic_kind')
    brief = [{key: c[key] for key in fields if c.get(key)} for c in candidates]
    user = prefix + '\n初选：\n' + json.dumps({'candidates': brief}, ensure_ascii=False) + '\n原文：\n' + sources
    prompt = [{'role': 'system', 'content': system}, {'role': 'user', 'content': user}]
    out = BASE / 'selection-input-trial' / case
    out.mkdir(parents=True, exist_ok=False)
    telemetry.use_state_dir(str(out))
    save(out / 'receipt.json', {'control_request': str(request), 'control_sha256': sha256(request.read_bytes()).hexdigest(),
        'sources_sha256': sha256(sources.encode()).hexdigest(),
        'kept_fields': fields, 'initial_candidate_count': len(candidates), 'source_count': len(chosen),
        'scope': 'existing second selection pass only; same model, system, job context, untouched original source bodies'})
    load_dotenv(ROOT / '.env', override=False)
    cfg = llm.LLMConfig.from_system(config.load_system_config(str(ROOT / 'config')), 'ai')
    assert cfg.model == 'deepseek-v4.1-flash' and cfg.base_url == 'https://opencode.ai/zen/go/v1'
    cfg = replace(cfg, fallback_models=[], fallback_providers=[])
    calls = 0
    started = time.monotonic()
    save(out / 'status.json', {'status': 'running'})

    class Capture(llm.LLMClient):
        def chat(self, messages, **kwargs):
            nonlocal calls
            calls += 1
            save(out / f'{calls}-request.json', {'messages': messages, 'options': kwargs})
            if calls == 1:
                assert messages == prompt and kwargs == payload['options']
            response = super().chat(messages, **kwargs)
            save(out / f'{calls}-response.json', {'content': response.content, 'usage': dict(self.usage)})
            return response

    client = Capture(cfg)
    channel = 'flash' if case == 'flash' else 't05'
    try:
        result = call_json(client, system, user, audience_strategy.Selection, **payload['options'],
                           validator=lambda s: audience_strategy.validate_sources(s, chosen))
        save(out / 'selection.json', result.model_dump())
        (out / 'research.txt').write_text(audience_strategy.render_report(result, chosen, channel))
        save(out / 'status.json', {'status': 'selected', 'calls': calls, 'topics': len(result.candidates),
                                   'seconds': round(time.monotonic() - started, 1)})
    except Exception as exc:
        save(out / 'status.json', {'status': 'failed', 'error_type': type(exc).__name__, 'calls': calls})
        raise
    finally:
        client.close()


if __name__ == '__main__':
    case, = sys.argv[1:]
    assert case in {'flash', 't05'}
    main(case)
