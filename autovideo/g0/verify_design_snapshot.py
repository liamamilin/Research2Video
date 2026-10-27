"""Verify the document package manifest separately from generated G0 output files."""
from pathlib import Path
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))
from g0.design_snapshot import build_snapshot

with tempfile.TemporaryDirectory(prefix="autovideo-g0-design-") as temp:
    snapshot = Path(temp)
    count = build_snapshot(snapshot)
    print(f"Document package manifest: {count} file hashes match", flush=True)
    result = subprocess.run([sys.executable, "-m", "pytest", "tests", "-q"],
                            cwd=snapshot, timeout=120)
    sys.exit(result.returncode)
