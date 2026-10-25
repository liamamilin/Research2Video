"""Isolated real-browser capture → image upload → mapped IR → real MP4 smoke.

Run with the research Python environment. No provider, real account, production
database or existing task is used. Audio is an explicit silent timing fixture.
"""
import argparse
import copy
import hashlib
import json
import shutil
import subprocess
import sys
import wave
from pathlib import Path
from types import SimpleNamespace

RESEARCH = Path(__file__).resolve().parents[3]
WORKSPACE = RESEARCH.parents[1]
AUTOVIDEO = WORKSPACE / "autovideo"
sys.path[:0] = [str(RESEARCH), str(AUTOVIDEO)]

import httpx
from fastapi.testclient import TestClient

from core.video import screenshots, store
from core.video.autovideo import AutoVideoClient
from g1.video_ir import validate_video_ir
from g2 import authored_script, creative_forms
from g2.content_video import content_structure
from g3.app import create_app
from g3.config import Settings


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def command(argv, cwd, log):
    with log.open("w") as handle:
        subprocess.run([str(arg) for arg in argv], cwd=cwd, stdout=handle, stderr=subprocess.STDOUT, check=True, timeout=300)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source", type=Path, help="Reuse a previously captured PNG plus source.json")
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    source = output / "source"
    source.mkdir()
    if args.source:
        image_path = args.source / "page.png"
        raw = (image_path if image_path.exists() else args.source / "playwright-article.png").read_bytes()
        meta = json.loads((args.source / "source.json").read_text())
    else:
        raw, meta = screenshots.capture("https://playwright.dev/python/docs/screenshots", mode="crop",
                                        clip={"x": 320, "y": 120, "width": 1040, "height": 640})
    (source / "page.png").write_bytes(raw)
    write(source / "source.json", meta)
    store.set_db_path(str(output / "research-state/video.db"))
    store.init_db()
    script = store.create_script("deep_dive", {"title": "来源截图段落配图验收"})
    paragraphs = ["先介绍来源和适用范围。", "现在展示官方的网页截图。页面说明了整页与元素截图方式。", "最后总结本期重点。"]
    sections = [{"title": "截图与讲解", "paragraphs": [{"text": text, "citations": [], "evidence": "UNCITED"} for text in paragraphs]}]
    store.add_version(script["id"], title="网页截图段落配图", sections=sections, length_check={}, edit_type="EDITED")
    version = store.get_version(script["id"], 1)
    asset = screenshots.save(script["id"], 1, raw, meta, created_by="isolated-acceptance")
    with TestClient(create_app(Settings(data_dir=output / "engine-data"), start_worker=False,
                               preflight=lambda _: {"ready": True, "checks": {}, "blockers": []})) as api:
        def transport(request):
            response = api.request(request.method, request.url.raw_path.decode(), content=request.content,
                                   headers=dict(request.headers))
            return httpx.Response(response.status_code, content=response.content, headers=response.headers)

        client = AutoVideoClient("http://127.0.0.1:8011", transport=httpx.MockTransport(transport))
        screenshots.register(script["id"], asset["id"], client)
        results = []
        for form in ("object_showcase", "dynamic_collage"):
            screenshots.select(script["id"], version, form, [{"screenshot_id": asset["id"], "caption": "Playwright 官方截图说明页",
                "role": "detail", "placements": [{"chapter_index": 0, "paragraph_index": 1}]}], client.base_url)
            payload = {"title": "网页截图段落映射", "input_mode": "narration", "duration_mode": "content",
                       "chapters": screenshots.chapters(version), "packaging": {"enabled": False},
                       **screenshots.render_materials(script["id"], version, client.base_url)}
            response = api.post("/api/jobs", json=payload)
            assert response.status_code == 202, response.text
            jid = response.json()["job_id"]
            inp = api.app.state.store.source_path(jid)
            saved = json.loads(inp.with_name("input.json").read_text())
            write(output / form / "request.json", payload)
            root = output / form
            fixture = root / "fixtures"
            (fixture / "assets").mkdir(parents=True)
            artifacts = root / "artifacts"
            artifacts.mkdir()
            pipeline = SimpleNamespace(PROJECT_INPUT=inp, ASSETS=fixture / "assets", ARTIFACTS=artifacts,
                MAX_TOTAL_CALLS=16, MAX_INPUT_TOKENS_PER_CALL=10000, MAX_TOKENS_PER_CALL=10000,
                G2Fault=ValueError, write_json=write, write_fact_ledger=lambda *args: None)
            narration = authored_script.plan(pipeline, authored_script.script_text(payload["chapters"]))
            images = creative_forms.prepare_images(pipeline, narration)
            board = creative_forms.plan_storyboard(pipeline, narration, form)
            board = creative_forms.attach_materials(board, images, saved)
            assert [scene["component"] for scene in board["scenes"]] == ["kinetic_text", creative_forms.FORMS[form], creative_forms.FORMS[form], "kinetic_text"]
            assert [scene["props"]["text"] for scene in board["scenes"]] == [s["text"] for s in narration["segments"]]
            write(artifacts / "assignment.json", board)
            segment_ms = 1600
            duration = segment_ms * len(narration["segments"])
            audio = fixture / "assets/communications_noise.wav"
            with wave.open(str(audio), "wb") as wav:
                wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(24000)
                wav.writeframes(b"\0\0" * (duration * 24))
            scenes = copy.deepcopy(board["scenes"])
            segments = []
            for i, (segment, scene) in enumerate(zip(narration["segments"], scenes)):
                segments.append({"id": segment["id"], "text": segment["text"], "start_ms": i * segment_ms + 100,
                                 "end_ms": (i + 1) * segment_ms - 100})
                scene.update(start_ms=i * segment_ms, duration_ms=segment_ms)
            text = authored_script.script_text(payload["chapters"])
            ir = {"schema_version": "0.2", "video": {"title": payload["title"], "width": 1920, "height": 1080,
                  "fps": 30, "duration_ms": duration}, "source": {"language": "zh-CN", "original_text": text,
                  "text_sha256": hashlib.sha256(text.encode()).hexdigest()}, "style": {"preset": "minimal_light", "seed": 7},
                  "presentation": {"preset": form}, "content_structure": content_structure(narration),
                  "narration": {"text": "".join(s["text"] for s in segments), "audio_asset_id": "audio", "segments": segments,
                                "alignment": {"method": "provider", "granularity": "phrase"}},
                  "assets": [{"id": "audio", "type": "audio", "path": "assets/communications_noise.wav",
                              "origin": "silent acceptance timing fixture; not generated speech",
                              "sha256": hashlib.sha256(audio.read_bytes()).hexdigest()}, *images], "scenes": scenes}
            compiled = validate_video_ir(ir, fixture)
            write(artifacts / "video-ir.json", ir)
            write(artifacts / "props.json", {"ir": {**compiled, "audio_public_file": "assets/communications_noise.wav"}})
            renderer = root / "renderer"
            shutil.copytree(AUTOVIDEO / "g1/renderer/src", renderer / "src")
            (renderer / "node_modules").symlink_to(AUTOVIDEO / "g0/vendor/remotion-audiogram/node_modules", target_is_directory=True)
            cli = AUTOVIDEO / "g0/vendor/remotion-audiogram/node_modules/@remotion/cli/remotion-cli.js"
            video = root / "video.mp4"
            command([shutil.which("node"), cli, "render", "src/index.ts", "G1CommunicationNoise", video,
                     f"--props={artifacts / 'props.json'}", f"--public-dir={fixture}", "--codec=h264", "--audio-codec=aac",
                     "--concurrency=2", "--crf=18", "--pixel-format=yuv420p", "--bundle-cache=false",
                     "--browser-executable=/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"], renderer, root / "render.log")
            ffmpeg = AUTOVIDEO / "g0/darwin_arm64/ffmpeg"
            ffprobe = AUTOVIDEO / "g0/darwin_arm64/ffprobe"
            command([ffmpeg, "-v", "error", "-xerror", "-i", video, "-map", "0:v:0", "-map", "0:a:0", "-f", "null", "-"], root, root / "decode.log")
            probe = json.loads(subprocess.check_output([str(ffprobe), "-v", "error", "-count_frames", "-show_streams", "-show_format", "-of", "json", str(video)]))
            stream = next(s for s in probe["streams"] if s["codec_type"] == "video")
            assert (stream["width"], stream["height"], int(stream["nb_read_frames"])) == (1920, 1080, compiled["total_frames"])
            for name, second in (("unassigned-opening", .8), ("mapped-screenshot", 2.4), ("unassigned-ending", 5.6)):
                command([ffmpeg, "-v", "error", "-ss", second, "-i", video, "-frames:v", "1", root / f"{name}.png"], root, root / f"{name}.log")
            result = {"form": form, "job_id": jid, "video": str(video), "sha256": hashlib.sha256(video.read_bytes()).hexdigest(),
                      "duration_seconds": float(probe["format"]["duration"]), "frames": int(stream["nb_read_frames"]),
                      "decode": "PASS", "assignment": "PASS", "audio": "silent timing fixture", "real_llm": False}
            results.append(result)
            print(json.dumps(result, ensure_ascii=False), flush=True)
        write(output / "qa.json", {"source": meta, "results": results})


if __name__ == "__main__":
    main()
