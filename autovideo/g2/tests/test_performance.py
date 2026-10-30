import json
import threading

import pytest

from g2 import performance, run_g2 as pipeline


@pytest.mark.parametrize("cpus,gib,expected", [(12, 48, 2), (8, 16, 2), (4, 16, 2),
    (1, 8, 1), (12, 8, 2), (12, 6, 1), (12, None, 2)])
def test_auto_render_budget_reserves_cpu_and_memory(monkeypatch, cpus, gib, expected):
    monkeypatch.delenv("AUTOVIDEO_G2_RENDER_CONCURRENCY", raising=False)
    monkeypatch.setattr(performance.os, "cpu_count", lambda: cpus)
    monkeypatch.setattr(performance, "physical_memory_bytes", lambda: gib * 1024 ** 3 if gib else None)
    assert performance.render_resources()["concurrency"] == expected


@pytest.mark.parametrize("platform,form,expected", [("darwin", "information_motion", 1),
    ("darwin", "process_diagram", 2), ("linux", "information_motion", 2)])
def test_auto_uses_only_the_measured_platform_and_form(monkeypatch, platform, form, expected):
    monkeypatch.setattr(performance.sys, "platform", platform)
    monkeypatch.setattr(performance.os, "cpu_count", lambda: 12)
    monkeypatch.setattr(performance, "physical_memory_bytes", lambda: 48 * 1024 ** 3)
    monkeypatch.delenv("AUTOVIDEO_G2_RENDER_CONCURRENCY", raising=False)
    assert performance.render_resources(video_form=form)["concurrency"] == expected


@pytest.mark.parametrize("form,expected", [("information_motion", 1), ("process_diagram", 2), ("auto", 1), (None, 1)])
def test_internal_explanation_preset_does_not_hide_the_resolved_form(monkeypatch, form, expected):
    monkeypatch.setattr(performance.sys, "platform", "darwin")
    monkeypatch.setattr(performance.os, "cpu_count", lambda: 12)
    monkeypatch.setattr(performance, "physical_memory_bytes", lambda: 48 * 1024 ** 3)
    monkeypatch.delenv("AUTOVIDEO_G2_RENDER_CONCURRENCY", raising=False)
    assert performance.render_resources(video_form=form, presentation_preset="explanation")["concurrency"] == expected


@pytest.mark.parametrize("value", ["0", "-1", "1.5", "80%", "13", "", "２"])
def test_invalid_render_override_is_rejected(monkeypatch, value):
    monkeypatch.setattr(performance.os, "cpu_count", lambda: 12)
    monkeypatch.setattr(performance, "physical_memory_bytes", lambda: None)
    monkeypatch.setenv("AUTOVIDEO_G2_RENDER_CONCURRENCY", value)
    with pytest.raises(ValueError, match="RENDER_CONCURRENCY"):
        performance.render_resources()


def test_render_override_preserves_quality_and_records_actual_resources(monkeypatch, tmp_path):
    monkeypatch.setenv("AUTOVIDEO_G2_RENDER_CONCURRENCY", "2")
    monkeypatch.setattr(performance.os, "cpu_count", lambda: 12)
    monkeypatch.setattr(performance, "physical_memory_bytes", lambda: 48 * 1024 ** 3)
    monkeypatch.setattr(pipeline, "ARTIFACTS", tmp_path / "artifacts")
    monkeypatch.setattr(pipeline, "package_request_from_intent", lambda: None)
    monkeypatch.setattr(pipeline, "packaging_record", lambda: None)
    commands = []
    def command(label, argv, *args, **kwargs):
        commands.append(argv)
        from pathlib import Path
        Path(argv[5]).touch()
    monkeypatch.setattr(pipeline, "_run_fixed_command", command)
    pipeline.render({"total_frames": 900}, renderer_root=tmp_path / "renderer")
    argv = commands[0]
    assert {"--concurrency=2", "--crf=18", "--width=1920", "--height=1080", "--fps=30",
            "--codec=h264", "--audio-codec=aac", "--pixel-format=yuv420p", "--bundle-cache=false"} <= set(argv)
    assert json.loads((tmp_path / "artifacts/render-resources.json").read_text())["concurrency"] == 2


def setup_planning(monkeypatch, tmp_path, content=True):
    monkeypatch.setattr(pipeline, "CONTENT_MODE", content)
    monkeypatch.setattr(pipeline, "ARTIFACTS", tmp_path / "artifacts")
    monkeypatch.setattr(pipeline, "COMMANDS_PATH", tmp_path / "logs/commands.jsonl")
    monkeypatch.delenv("AUTOVIDEO_G2_PARALLEL_PLANNING", raising=False)


def test_measured_visual_planning_receives_finished_speech_before_one_model_pass(monkeypatch, tmp_path):
    setup_planning(monkeypatch, tmp_path)
    calls, aligned = [], {'rows': [{'id': 'n1', 'start_ms': 0, 'end_ms': 4000}]}
    def voice(narration):
        calls.append('voice')
        return narration, aligned
    def storyboard(narration, measured):
        assert calls == ['voice'] and measured is aligned
        calls.append('storyboard')
        return {'scenes': []}
    monkeypatch.setattr(pipeline, 'fit_voiceover_to_duration', voice)
    pipeline.run_planning_stages({}, storyboard, measured_storyboard=True)
    report = json.loads((tmp_path/'artifacts/generation-schedule.json').read_text())
    assert report['mode'] == 'sequential' and report['measured_storyboard']
    assert calls == ['voice', 'storyboard']


def test_content_voice_and_storyboard_actually_overlap(monkeypatch, tmp_path):
    setup_planning(monkeypatch, tmp_path)
    barrier = threading.Barrier(2)
    original, aligned, plan = {"segments": ["定稿"]}, {"rows": ["实测"]}, {"scenes": ["分镜"]}

    def voice(narration):
        assert narration is original
        barrier.wait(timeout=3)
        return narration, aligned

    def storyboard(narration):
        assert narration is original
        barrier.wait(timeout=3)
        return plan

    monkeypatch.setattr(pipeline, "fit_voiceover_to_duration", voice)
    assert pipeline.run_planning_stages(original, storyboard) == (original, aligned, plan)
    report = json.loads((tmp_path / "artifacts/generation-schedule.json").read_text())
    assert report["mode"] == "parallel"
    assert {s["status"] for s in report["steps"].values()} == {"SUCCEEDED"}
    log = [json.loads(line) for line in (tmp_path / "logs/commands.jsonl").read_text().splitlines()]
    assert {r["label"] for r in log} == {"tts-and-align", "storyboard-plan"}


@pytest.mark.parametrize("content,override", [(False, "1"), (True, "0")])
def test_sequential_planning_uses_the_final_duration_fitted_narration(monkeypatch, tmp_path, content, override):
    setup_planning(monkeypatch, tmp_path, content)
    monkeypatch.setenv("AUTOVIDEO_G2_PARALLEL_PLANNING", override)
    calls = []
    final = {"segments": ["适配时长后"]}

    def voice(narration):
        calls.append("voice")
        return final, {"measured": True}

    def storyboard(narration):
        assert narration is final
        assert calls == ["voice"]
        calls.append("storyboard")
        return {"scenes": []}

    monkeypatch.setattr(pipeline, "fit_voiceover_to_duration", voice)
    assert pipeline.run_planning_stages({"segments": ["原稿"]}, storyboard)[0] is final
    report = json.loads((tmp_path / "artifacts/generation-schedule.json").read_text())
    assert report["mode"] == "sequential" and report["overlap_seconds"] == 0


@pytest.mark.parametrize("failing", ["voice", "storyboard"])
def test_parallel_failure_keeps_finished_evidence_and_leaves_no_background_task(monkeypatch, tmp_path, failing):
    setup_planning(monkeypatch, tmp_path)
    barrier = threading.Barrier(2)
    failure_raised, survivor_finished = threading.Event(), threading.Event()

    def task(name, result):
        barrier.wait(timeout=3)
        if name == failing:
            failure_raised.set()
            raise pipeline.G2Fault("ACCEPTANCE_FAILURE", name)
        assert failure_raised.wait(3)
        (tmp_path / "surviving-artifact.json").write_text(name)
        survivor_finished.set()
        return result

    monkeypatch.setattr(pipeline, "fit_voiceover_to_duration", lambda n: task("voice", (n, {})))
    with pytest.raises(pipeline.G2Fault, match=failing):
        pipeline.run_planning_stages({}, lambda n: task("storyboard", {}))
    assert survivor_finished.is_set() and (tmp_path / "surviving-artifact.json").is_file()
    report = json.loads((tmp_path / "artifacts/generation-schedule.json").read_text())
    assert {s["status"] for s in report["steps"].values()} == {"FAILED", "SUCCEEDED"}
