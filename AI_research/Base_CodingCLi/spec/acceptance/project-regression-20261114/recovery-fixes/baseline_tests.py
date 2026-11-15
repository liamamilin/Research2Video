"""Run new regression tests against the recorded pre-fix implementation in memory."""
import importlib
from pathlib import Path
import subprocess
import sys

root = Path('/Users/milin/2026/软件开发/视频workflow')
revision = 'f8541778a77a2c59e253fc31d2a0fb9de971aa80'
mode = sys.argv[1]
base = 'AI_research/Base_CodingCLi' if mode == 'research' else 'autovideo'
sys.path.insert(0, str(root / base))
modules = (['web.runner.executor', 'web.runner.log_bus', 'web.routes.jobs'] if mode == 'research'
           else ['g3.store', 'g3.worker', 'g3.app'])
for name in modules:
    relative = base + '/' + name.replace('.', '/') + '.py'
    source = subprocess.check_output(['git', 'show', revision + ':' + relative], cwd=root, text=True)
    module = importlib.import_module(name)
    exec(compile(source, str(root / relative), 'exec'), module.__dict__)

import pytest
tests = (['tests/test_executor.py::test_invalid_config_at_worker_start_finishes_the_registered_task',
          'tests/test_job_stream.py'] if mode == 'research' else ['g3/tests/test_worker_recovery.py'])
raise SystemExit(pytest.main([*tests, '-q']))
