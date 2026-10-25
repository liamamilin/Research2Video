"""Exercise approved screen copy and original PCM without another planning call."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from g1.video_ir import validate_video_ir
from g2 import fast_cards

SOURCE = ROOT / "g3/data/jobs/2bd645c6-6595-474c-8431-9bccabadce0f/attempts/1/g2-run"
probe_root = ROOT / "g3/data/quality-probes"
probe_root.mkdir(exist_ok=True)
OUT = Path(tempfile.mkdtemp(prefix="card-quality-", dir=probe_root))
for name in ("card-editorial.json", "caption-alignment.json", "captions.srt"):
    shutil.copy2(SOURCE / "artifacts" / name, OUT / name)
ir_path = SOURCE / "fixtures/video-ir-v0.2.json"
ir = json.loads(ir_path.read_text())
saved = json.loads((SOURCE.parents[2] / "input/input.json").read_text())


class Fault(RuntimeError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def runner(label, argv, cwd, timeout=900):
    result = subprocess.run([str(a) for a in argv], cwd=cwd, capture_output=True, text=True, timeout=timeout)
    (OUT / f"{label}.log").write_text(result.stdout + result.stderr)
    if result.returncode:
        raise Fault("COMMAND_FAILED", f"{label}: {result.stderr[-500:]}")
    return result.stdout


p = SimpleNamespace(PACKAGING_INPUT=saved, IR_PATH=ir_path, ARTIFACTS=OUT, PROJECT=ROOT,
    ASSETS=SOURCE / "fixtures/assets", FFMPEG=ROOT / "g0/darwin_arm64/ffmpeg", VIDEO_PATH=OUT / "content.mp4",
    packaging_record=lambda: saved["packaging_resolved"], G2Fault=Fault, _run_fixed_command=runner,
    write_json=lambda path, value: path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n"))
compiled = validate_video_ir(ir, SOURCE / "fixtures")
fast_cards.render(p, compiled)
qa = fast_cards.verify_captions(p, {})
qa["probe_directory"] = str(OUT)
runner("full-decode", [p.FFMPEG, "-nostdin", "-v", "error", "-xerror", "-i", p.VIDEO_PATH,
                       "-map", "0:v:0", "-map", "0:a:0", "-f", "null", "-"], ROOT)
(Path(__file__).parent / "render-probe-qa.json").write_text(json.dumps(qa, ensure_ascii=False, indent=2) + "\n")
print(json.dumps(qa, ensure_ascii=False))
