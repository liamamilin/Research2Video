import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from g2 import render_checkpoint


def example(tmp_path, monkeypatch):
    job = tmp_path / "jobs/job"
    previous = job / "attempts/1/g2-run"
    current = job / "attempts/2/g2-run"
    for path in (previous / "artifacts", previous / "logs", previous / "fixtures/assets", job / "input", current / "artifacts"):
        path.mkdir(parents=True)
    source = "使用固定讲稿和音轨继续渲染。"
    sha = hashlib.sha256(source.encode()).hexdigest()
    def save(path, value):
        path.write_text(json.dumps(value))
    artifacts = previous / "artifacts"
    save(artifacts / "input-brief.json", {"source_sha256": sha, "selected_form": "information_motion"})
    save(artifacts / "alignment.json", {"voice": "voxcpm_anchor"})
    ir = {"source": {"text_sha256": sha}, "presentation": {"preset": "explanation"},
          "video": {"title": "固定视频"}, "content_structure": {"chapters": [{"title": "章节"}]}, "playback": {"speed": 1.25}}
    save(previous / "fixtures/video-ir-v0.2.json", ir)
    save(job / "input/input.json", {"title": "固定视频", "chapters": [{"title": "章节", "paragraphs": [source]}]})
    for name in ("final-narration-plan.json", "caption-alignment.json", "tts-metrics.json", "model-cost.json"):
        save(artifacts / name, {})
    (artifacts / "captions.srt").write_text(source)
    (previous / "fixtures/assets/communications_noise.wav").write_bytes(b"original audio")
    (previous / "logs/commands.jsonl").write_text('\n'.join(json.dumps(v) for v in [
        {"label": "compile-ir", "exit_code": 0}, {"label": "render", "exit_code": 1}]))
    monkeypatch.setenv("AUTOVIDEO_G2_PREVIOUS_RUN_DIR", str(previous))
    monkeypatch.setattr("g1.video_ir.validate_video_ir", lambda ir, folder: {"scenes": [{"component": "comparison"}], "total_frames": 60})
    monkeypatch.setattr("g2.authored_script.verify_outputs", lambda p, s: {"status": "PASS"})
    stages = []
    pipeline = SimpleNamespace(CONTENT_MODE=True, LOCK_NARRATION=True, PROJECT=tmp_path, RUN_ROOT=current,
        PROJECT_INPUT=job / "input/source.txt", VOICE="voxcpm_anchor", OUTPUT_SPEED=1.25,
        project_path=lambda root, path: path, ARTIFACTS=current / "artifacts", FIXTURE_ROOT=current / "fixtures",
        ASSETS=current / "fixtures/assets", IR_PATH=current / "fixtures/video-ir-v0.2.json", write_json=save,
        digest=lambda path: hashlib.sha256(path.read_bytes()).hexdigest(),
        skip_step=lambda name, reason: stages.append((name, "reused")),
        run_step=lambda name, fn, *args: stages.append((name, "ran")) or fn(*args),
        render=lambda *a, **k: None, verify_media=lambda *a: {"status": "PASS"},
        add_narration_check=lambda q: None, write_call_summary=lambda: None, write_manifest=lambda **kw: None)
    return pipeline, source, previous, stages


def test_render_failure_reuses_original_audio_captions_and_storyboard(tmp_path, monkeypatch):
    pipeline, source, previous, stages = example(tmp_path, monkeypatch)
    qa = render_checkpoint.resume(pipeline, source)
    assert qa["status"] == "PASS" and qa["recovery"]["new_model_calls"] == qa["recovery"]["new_voice_calls"] == 0
    assert (pipeline.ASSETS / "communications_noise.wav").read_bytes() == b"original audio"
    assert (pipeline.ARTIFACTS / "captions.srt").read_text() == source
    assert (previous / "fixtures/assets/communications_noise.wav").read_bytes() == b"original audio"
    assert not (pipeline.ARTIFACTS / "model-cost.json").exists()
    assert [name for name, state in stages if state == "ran"] == ["render", "verify-media"]


@pytest.mark.parametrize("changed", ["source", "voice", "speed", "job", "stage"])
def test_different_inputs_or_no_finished_compile_use_normal_generation(tmp_path, monkeypatch, changed):
    pipeline, source, previous, _ = example(tmp_path, monkeypatch)
    if changed == "source":
        source += "新稿。"
    elif changed == "voice":
        pipeline.VOICE = "another"
    elif changed == "speed":
        pipeline.OUTPUT_SPEED = 1
    elif changed == "job":
        pipeline.RUN_ROOT = tmp_path / "other/attempts/2/g2-run"
    else:
        (previous / "logs/commands.jsonl").write_text(json.dumps({"label": "compile-ir", "exit_code": 1}))
    assert render_checkpoint.checkpoint(pipeline, source) is None
