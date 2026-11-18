"""Isolate the Flash research-prompt change over the same previously collected inputs."""
from __future__ import annotations

from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import sys
import time

from dotenv import load_dotenv
import yaml

ROOT = Path(__file__).resolve().parents[4]
BASE = ROOT / 'state/acceptance-content-value-20261115/natural-writing-20261116'
JOB = 'monitoring/ai_flash_discovery'
STAMP = '2026-11-16'
REL = Path('output/flash') / f'{STAMP}_ai_flash_discovery'

sys.path.insert(0, str(ROOT))


def dump(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def run(variant):
    from core import events, llm, matrices, research, state, telemetry
    from core.config import load_job
    from core.engine import ResearchEngine
    from core.provenance import extract_urls
    from core.source_supply import material_block

    out = BASE / 'flash-research-prompt-pair' / variant
    out.mkdir(parents=True, exist_ok=False)
    scratch = out / 'isolated'
    scratch.mkdir()
    for mod in (events, state, telemetry):
        mod.use_state_dir(str(scratch))
    snapshot = next((ROOT / REL.parent).glob(REL.name + '.materials-*.json'))
    report = ROOT / (str(REL) + '.md')
    receipt = json.loads(Path(str(report) + '.source-supply.json').read_text())
    material = json.loads(snapshot.read_text())
    sha = lambda value: hashlib.sha256(value.encode()).hexdigest()
    assert sha(report.read_text()) == material['report_sha256']
    archived = material['material']
    # Recover the original supply block from before separate expanded/supplementary
    # material was appended to the report's material snapshot.
    sentinel = '已成功读取来源，本次没有新增或修改材料；不要将上次事件重新当作新变化。'
    prefix, suffix = material_block([], receipt).split(sentinel, 1)
    assert archived.startswith(prefix) and suffix
    boundary = suffix + '\n\n---\n\n'
    assert archived.count(boundary) == 1
    sources = archived[:archived.index(boundary) + len(suffix)]
    (out / 'source-block.txt').write_text(sources)
    load_dotenv(ROOT / '.env', override=False)
    config = str(ROOT / 'config')
    jobs = str(ROOT / 'jobs')
    job = load_job(jobs, JOB)
    if variant == 'control':
        before = BASE / 'wrap-rollout/before/jobs' / (JOB + '.yaml')
        job['prompt'] = yaml.safe_load(before.read_text())['prompt']
    if variant == 'candidate':
        before_fields = BASE / 'research-field-soft/before/jobs' / (JOB + '.yaml')
        if before_fields.exists():
            job['prompt'] = yaml.safe_load(before_fields.read_text())['prompt']
    job = matrices.for_run(job, config)
    engine = ResearchEngine(config_dir=config, jobs_dir=jobs, workspace_dir=str(scratch),
                            state_dir=str(scratch), date_override=STAMP)
    prompt = engine._build_prompt(job)
    (out / 'job-prompt.txt').write_text(prompt)
    stats = {k: row['delivered'] for k, row in receipt['sources'].items()}
    dump(out / 'input-receipt.json', {'snapshot': str(snapshot), 'report_sha256': material['report_sha256'],
        'source_chars': len(sources), 'source_sha256': sha(sources), 'job_prompt_sha256': sha(prompt),
        'scope': 'actual first research method on prior source supply; no refetch, dossier expansion, selection, supplements or writing'})
    cfg = llm.LLMConfig.from_system(engine.sys, 'ai')
    assert cfg.base_url == 'https://opencode.ai/zen/go/v1' and cfg.model == 'deepseek-v4.1-flash'
    calls = out / 'calls'
    calls.mkdir()

    class CapturedClient(llm.LLMClient):
        def chat(self, messages, **kw):
            t = time.time_ns()
            dump(calls / f'{t}-request.json', {'messages': messages, 'options': kw,
                 'endpoint': self.config.base_url, 'model': self.config.model})
            result = super().chat(messages, **kw)
            dump(calls / f'{t}-response.json', {'text': result.content, 'usage': dict(self.usage)})
            return result

    research.LLMClient = CapturedClient
    agent = research.ResearchAgent(llm_config=replace(cfg, fallback_models=[], fallback_providers=[]),
        search_config=research.SearchConfig.from_system(engine.sys), workspace_dir=str(scratch))
    t = time.monotonic()
    dump(out / 'status.json', {'status': 'researching', 'pid': os.getpid()})
    try:
        report_text = agent.run_fetch_only(prompt, fetched_urls=extract_urls(sources), stats=stats, sources=sources)
        if not report_text.strip():
            raise ValueError('empty report')
        (out / 'research.txt').write_text(report_text)
        dump(out / 'status.json', {'status': 'RESEARCHED', 'seconds': time.monotonic() - t})
    except BaseException as exc:
        dump(out / 'status.json', {'status': 'failed', 'error': type(exc).__name__})
        raise
    finally:
        agent.llm.close()


if __name__ == '__main__':
    variant, = sys.argv[1:]
    assert variant in {'control', 'candidate', 'flexible_fields'}
    run(variant)
