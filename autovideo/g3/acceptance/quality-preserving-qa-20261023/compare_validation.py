"""Run the old and new validation on one immutable real MP4; no generation."""
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import time

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[2]
sys.path.insert(0, str(ROOT))
from g2 import package_stage as current

spec = importlib.util.spec_from_file_location("package_stage_before", OUT / "package_stage_before.py")
before = importlib.util.module_from_spec(spec)
spec.loader.exec_module(before)
job = ROOT / "g3/data/jobs/47bd614a-b7cd-4227-a110-3bbd15283ddb/attempts/1/g2-run"
video = job / "artifacts/package/video.mp4"
package = json.loads((video.parent / "package.json").read_text())
ffmpeg, ffprobe = [ROOT / f"g0/darwin_arm64/{name}" for name in ("ffmpeg", "ffprobe")]
content_probe = current.probe(current.default_runner, ffprobe, job / "artifacts/G2_自动生成.mp4")
source_hash = hashlib.sha256(video.read_bytes()).hexdigest()
results = {}
for name, module in (("before", before), ("after", current)):
    work = OUT / f"validation-{name}"
    work.mkdir(exist_ok=True)
    calls = []

    def runner(label, argv):
        start = time.monotonic()
        result = current.default_runner(label, argv)
        calls.append({"label": label, "seconds": round(time.monotonic() - start, 4),
                      "argv": [str(arg) for arg in argv]})
        return result

    start = time.monotonic()
    checks = module.verify_package(
        runner, ffmpeg, ffprobe, video, content_probe=content_probe,
        intro_frames=package["intro"]["frames"], content_frames=package["content"]["frames"],
        outro_frames=package["outro"]["frames"], source_frames=package["source_card"]["frames"],
        voice_wav=job / "fixtures/assets/communications_noise.wav", work=work,
        cue_names={key: package[key]["sound"]["sound"] for key in ("intro", "outro")})
    qa_seconds = time.monotonic() - start
    target = work / "contact-sheet.jpg"
    start = time.monotonic()
    module.contact_sheet(runner, ffmpeg, video, target, package["intro"]["frames"],
                         package["content"]["frames"], package["outro"]["frames"], work,
                         package["source_card"]["frames"])
    results[name] = {"checks": checks, "qa_seconds": round(qa_seconds, 4),
                     "preview_seconds": round(time.monotonic() - start, 4),
                     "contact_sheet_sha256": hashlib.sha256(target.read_bytes()).hexdigest(), "calls": calls}
assert results["before"]["checks"] == results["after"]["checks"]
assert results["before"]["contact_sheet_sha256"] == results["after"]["contact_sheet_sha256"]
assert hashlib.sha256(video.read_bytes()).hexdigest() == source_hash
results["source_video_sha256"] = source_hash
results["identical_checks"] = results["identical_contact_sheet"] = results["source_untouched"] = True
(OUT / "validation-comparison.json").write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n")
print(json.dumps({key: {field: value for field, value in result.items() if field in
                       ("qa_seconds", "preview_seconds", "contact_sheet_sha256")}
                  for key, result in results.items() if isinstance(result, dict)}, indent=2))
