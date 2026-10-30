"""Verify the completed recovery without modifying the production artifacts."""
import hashlib
import json
from pathlib import Path
import subprocess


def main():
    out = Path(__file__).resolve().parent
    root = out.parents[2]
    result = json.loads((out / "result.json").read_text())
    assert result["status"] == "COMPLETED"
    attempts = root / "g3/data/jobs" / result["engine_job"] / "attempts"
    previous = attempts / "1/g2-run"
    current = attempts / str(result["attempt_count"]) / "g2-run"
    digest = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
    identical = {}
    for name in ("fixtures/video-ir-v0.2.json", "fixtures/assets/communications_noise.wav",
                 "artifacts/final-narration-plan.json", "artifacts/caption-alignment.json",
                 "artifacts/captions.srt", "artifacts/storyboard-plan.json"):
        old, new = digest(previous / name), digest(current / name)
        assert old == new, name
        identical[name] = new
    read = lambda name: json.loads((current / "artifacts" / name).read_text())
    reuse, resources = read("render-checkpoint-reuse.json"), read("render-resources.json")
    assert reuse["new_model_calls"] == reuse["new_voice_calls"] == 0
    assert read("model-cost.json")["calls"] == []
    assert resources["concurrency"] == 1 and resources["video_form"] == "information_motion"
    commands = [json.loads(line) for line in (current / "logs/commands.jsonl").read_text().splitlines()]
    assert not any("voice" in c.get("label", "") for c in commands)
    assert read("qa.json")["status"] == "PASS"
    video = Path(result["video"])
    tools = root / "g0/darwin_arm64"
    probe = subprocess.run([str(tools / "ffprobe"), "-v", "error", "-show_streams", "-show_format",
                            "-of", "json", str(video)], check=True, capture_output=True, text=True)
    media = json.loads(probe.stdout)
    stream = next(s for s in media["streams"] if s["codec_type"] == "video")
    assert (stream["width"], stream["height"], stream["r_frame_rate"]) == (1920, 1080, "30/1")
    assert float(media["format"]["duration"]) >= 186.8
    with (out / "decode.log").open("w") as log:
        decoded = subprocess.run([str(tools / "ffmpeg"), "-v", "error", "-xerror", "-i", str(video),
                                  "-f", "null", "-"], stdout=log, stderr=log)
    assert decoded.returncode == 0, "See decode.log"
    proof = {"status": "PASS", "identical_files": identical, "reuse": reuse, "resources": resources,
             "media": {"width": stream["width"], "height": stream["height"], "fps": stream["r_frame_rate"],
                       "duration": float(media["format"]["duration"]), "size": video.stat().st_size},
             "full_decode_exit_code": decoded.returncode, "original_attempt_preserved": previous.exists()}
    (out / "verification.json").write_text(json.dumps(proof, ensure_ascii=False, indent=2))
    print(json.dumps(proof, ensure_ascii=False))


if __name__ == "__main__":
    main()
