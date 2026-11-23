import json
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from g2 import caption_alignment, mlx_daemon


def test_daemon_serves_ping_and_shutdown_and_exits_cleanly_on_idle():
    import os
    sock = Path(f"/tmp/test-av-mlx-{os.getpid()}-{int(time.time()*1000)}.sock")
    thread = threading.Thread(target=mlx_daemon.serve, args=(sock,), kwargs={"idle_seconds": 0.6}, daemon=True)
    thread.start()
    deadline = time.monotonic() + 3.0
    while not sock.exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert sock.exists()
    with mlx_daemon._connect(sock, timeout=2.0) as conn:
        conn.sendall(b'{"op":"ping"}\n')
        resp = json.loads(conn.makefile("r", encoding="utf-8").readline())
    assert resp["ok"] is True and resp["version"] == mlx_daemon.code_version()
    thread.join(timeout=3.0)
    assert not thread.is_alive()
    assert not sock.exists()


def test_daemon_disabled_returns_false_for_in_process_fallback(monkeypatch):
    monkeypatch.setenv("AUTOVIDEO_G2_MLX_DAEMON", "0")
    assert mlx_daemon.call_daemon({"op": "ping"}) is False


def test_model_cache_hashes_file_once_per_stat(tmp_path):
    target = tmp_path / "weights.bin"
    target.write_bytes(b"abc123")
    cache = mlx_daemon._ModelCache()
    h1 = cache.get_file_sha256(target)
    h2 = cache.get_file_sha256(target)
    assert h1 == h2 and len(cache.sha_cache) == 1


def test_caption_prepare_reuses_valid_prealigned_track_without_spawning_subprocess(tmp_path):
    audio = tmp_path / "voice.wav"
    audio.write_bytes(b"RIFFfake")
    import hashlib
    audio_sha = hashlib.sha256(audio.read_bytes()).hexdigest()
    ir = {
        "assets": [{"id": "communication_voice", "type": "audio", "path": "voice.wav", "sha256": audio_sha}],
        "narration": {
            "audio_asset_id": "communication_voice",
            "segments": [{"id": "n1", "text": "先看结果。", "start_ms": 0, "end_ms": 1200}],
        },
    }
    plan = {
        "version": 2,
        "narration_sha256": caption_alignment.narration_hash(ir),
        "audio_sha256": audio_sha,
        "cues": [{"segment_id": "n1", "text": "先看结果。", "char_start": 0, "char_end": 5, "start_ms": 0, "end_ms": 1200}],
    }
    (tmp_path / "caption-alignment.json").write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")

    called = []
    pipeline = SimpleNamespace(
        PROJECT=tmp_path,
        ARTIFACTS=tmp_path,
        IR_PATH=tmp_path / "ir.json",
        _run_fixed_command=lambda *a, **k: called.append(a),
    )
    result = caption_alignment.prepare(pipeline, ir)
    assert result == plan
    assert called == []
    assert "先看结果。" in (tmp_path / "captions.srt").read_text(encoding="utf-8")
