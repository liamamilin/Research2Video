"""Reproduce a failed rendered frame independently from video encoding."""
import json
import subprocess
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[3]
AUTO = ROOT.parents[1] / 'autovideo'
run = AUTO / 'g3/data/acceptance-static-screenshots-20261028/884df5f7251b4588a5edec0c4f6f8a23/jobs/df38f610-339c-4587-8a2c-d3cb448b4dd2/attempts/1/g2-run'
records = [json.loads(line) for line in (run/'logs/commands.jsonl').read_text().splitlines()]
record = next(row for row in records if row['label'] == 'remotion-hybrid-001')
out = ROOT / 'state/validation/static-screenshots-20261028/diagnosis' / ('replay-' + uuid4().hex[:8] + '.png')
args = record['argv'][:2] + ['still', 'src/index.ts', 'G2CommunicationNoise', str(out)]
args += [arg for arg in record['argv'] if arg.startswith(('--props=', '--public-dir=', '--browser-executable='))]
args += ['--frame=523', '--bundle-cache=false']
subprocess.run(args, cwd=record['cwd'], check=True)
print(str(out), flush=True)
