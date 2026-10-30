"""The ordinary test entry point must not import saved acceptance snapshots."""
from pathlib import Path
import shutil
import subprocess
import sys


def test_default_collection_ignores_runtime_copies(tmp_path):
    root = Path(__file__).resolve().parents[1]
    shutil.copyfile(root / 'pytest.ini', tmp_path / 'pytest.ini')
    tests = tmp_path / 'tests'
    tests.mkdir()
    (tests / 'test_current.py').write_text('def test_current():\n    assert True\n')
    saved = tmp_path / 'state/validation/old/tests'
    saved.mkdir(parents=True)
    (saved / 'test_current.py').write_text('raise RuntimeError("historical snapshot imported")\n')
    result = subprocess.run([sys.executable, '-m', 'pytest', '--collect-only', '-q'],
                            cwd=tmp_path, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert '1 test collected' in result.stdout
    assert 'state/validation' not in result.stdout
