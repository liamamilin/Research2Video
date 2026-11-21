"""Run the authorized comparison serially; resume only missing completed artifacts."""
from pathlib import Path
import subprocess
import sys

HERE = Path(__file__).resolve().parent
BASE = Path('/tmp/content-value-20261115')

if __name__ == '__main__':
    for stage, variants in [('select', ['candidate']), ('write', ['baseline', 'candidate', 'matched'])]:
        for channel in ('flash', 't05', 't06'):
            for variant in variants:
                with (BASE/f'{channel}-{variant}-{stage}.log').open('a') as log:
                    subprocess.run([sys.executable, str(HERE/'pilot.py'), variant, '--channel', channel,
                                    '--stage', stage], stdout=log, stderr=subprocess.STDOUT, check=True)
                print(f'{channel} {variant} {stage} complete', flush=True)
