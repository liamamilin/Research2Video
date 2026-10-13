"""Verify the document package manifest separately from generated G0 output files."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[1]
manifest = json.loads((root / "PACKAGE_MANIFEST.json").read_text())
records = manifest["files"]
for item in records:
    file = root / item["path"]
    assert file.stat().st_size == item["bytes"], item["path"]
    assert hashlib.sha256(file.read_bytes()).hexdigest() == item["sha256"], item["path"]
print(f"Document package manifest: {len(records)} file hashes match", flush=True)
with tempfile.TemporaryDirectory(prefix="autovideo-g0-design-") as temp:
    snapshot = Path(temp)
    for rel in [r["path"] for r in records] + ["PACKAGE_MANIFEST.json"]:
        target = snapshot / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(root / rel, target)
    result = subprocess.run([sys.executable, "-m", "pytest", "tests", "-q"],
                            cwd=snapshot, timeout=120)
    sys.exit(result.returncode)
