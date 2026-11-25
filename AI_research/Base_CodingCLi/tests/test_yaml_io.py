"""Atomic writes must not share a scratch file between concurrent callers."""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from web.services import yaml_io


def test_backups_in_the_same_second_preserve_each_original(tmp_path, monkeypatch):
    monkeypatch.setattr(yaml_io, "_backup_dir", lambda: str(tmp_path))
    monkeypatch.setattr(yaml_io.time, "strftime", lambda *_: "20261115_120000")
    paths = [yaml_io.save_backup("jobs/a/job.yaml", "name: first\n"),
             yaml_io.save_backup("jobs/b/job.yaml", "name: second\n"),
             yaml_io.save_backup("jobs/a/job.yaml", "name: third\n")]
    assert len(set(paths)) == 3
    assert [Path(path).read_text() for path in paths] == ["name: first\n", "name: second\n", "name: third\n"]


def test_parallel_yaml_requests_keep_their_own_parser_state():
    def round_trip(index):
        payload = {"name": f"job-{index}", "prompt": "line\n" * 100, "keywords": list(range(100))}
        return yaml_io.parse_yaml(yaml_io.dump_yaml(payload)) == payload
    with ThreadPoolExecutor(max_workers=8) as pool:
        assert all(pool.map(round_trip, range(32)))


def test_duplicate_yaml_keys_are_reported_as_validation_errors():
    with pytest.raises(ValueError, match="YAML"):
        yaml_io.parse_yaml("name: first\nname: second\n")


def test_sub_millisecond_edit_is_still_a_conflict(tmp_path):
    import os

    target = tmp_path / "job.yaml"
    target.write_text("name: original\n")
    original = target.stat()
    target.write_text("name: external\n")
    os.utime(target, ns=(original.st_atime_ns, original.st_mtime_ns + 500_000))
    with pytest.raises(IOError, match="外部修改"):
        yaml_io.save_job_yaml(str(target), "name: stale\n", expected_mtime=original.st_mtime, backup=False)
    assert target.read_text() == "name: external\n"


def test_concurrent_versioned_saves_accept_only_one_writer(tmp_path, monkeypatch):
    import os

    target = tmp_path / "job.yaml"
    target.write_text("name: original\n")
    os.utime(target, (1_700_000_000, 1_700_000_000))
    expected = target.stat().st_mtime
    monkeypatch.setattr(yaml_io, "_backup_dir", lambda: str(tmp_path / "backups"))
    barrier = threading.Barrier(4)

    def save(index):
        barrier.wait(timeout=5)
        try:
            yaml_io.save_job_yaml(str(target), f"name: writer-{index}\n", expected_mtime=expected)
            return index
        except IOError as exc:
            assert "外部修改" in str(exc)
            return None

    with ThreadPoolExecutor(max_workers=4) as pool:
        winners = [value for value in pool.map(save, range(4)) if value is not None]
    assert len(winners) == 1
    assert target.read_text() == f"name: writer-{winners[0]}\n"
    backups = list((tmp_path / "backups").glob("*.yaml"))
    assert len(backups) == 1 and backups[0].read_text() == "name: original\n"


def test_concurrent_saves_both_land_and_neither_names_a_missing_file(tmp_path):
    """Two writers to one path must not share a temp file.

    `atomic_write` used `file_path + ".tmp"`, so both writers opened the same
    file: one truncated what the other was writing, and the first rename pulled
    it out from under the second, which then raised "No such file or directory"
    naming a path the caller had never mentioned. One save was silently lost.
    `atomic_create` already avoided this with a per-call temp name.
    """
    target = tmp_path / "job.yaml"
    target.write_text("name: original\n", encoding="utf-8")

    payloads = [f"name: a\nvalue: {'A' * 200000}\n",
                f"name: b\nvalue: {'B' * 200000}\n"]
    errors: list[Exception] = []
    barrier = threading.Barrier(2)

    def save(content: str) -> None:
        try:
            barrier.wait(timeout=5)
            yaml_io.atomic_write(str(target), content)
        except Exception as exc:  # noqa: BLE001 - collected for the assertion
            errors.append(exc)

    threads = [threading.Thread(target=save, args=(c,)) for c in payloads]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=20)

    assert not errors, f"a concurrent save failed: {[str(e) for e in errors]}"
    final = target.read_text(encoding="utf-8")
    assert final in payloads, "the file is neither writer's content -- interleaved"
    leftovers = [p.name for p in tmp_path.iterdir() if ".tmp" in p.name]
    assert not leftovers, f"temp files left behind: {leftovers}"


def test_a_failed_write_leaves_no_scratch_file(tmp_path, monkeypatch):
    """A partial file next to the target is the thing a reader might pick up."""
    target = tmp_path / "job.yaml"
    target.write_text("name: original\n", encoding="utf-8")

    def boom(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr(yaml_io.os, "replace", boom)
    try:
        yaml_io.atomic_write(str(target), "name: new\n")
    except IOError:
        pass
    else:
        raise AssertionError("the failure was not reported")

    assert target.read_text(encoding="utf-8") == "name: original\n", \
        "the original file was damaged by a failed write"
    assert [p.name for p in tmp_path.iterdir() if ".tmp" in p.name] == []


def test_a_plain_write_still_works(tmp_path):
    target = tmp_path / "job.yaml"
    yaml_io.atomic_write(str(target), "name: written\n")
    assert target.read_text(encoding="utf-8") == "name: written\n"


def test_an_external_edit_blocks_the_save_and_warns_it_costs_the_unsaved_text(tmp_path):
    """A stale `expected_mtime` must refuse, not quietly overwrite.

    Checked by hand on a real job editor: editing the YAML in the browser,
    changing the same line from a terminal, then saving kept the terminal's
    version. The refusal is the load-bearing part -- the wording is what keeps
    it useful. The old text was "请刷新后重试", which reads as advice; acting
    on it reloads the page and silently discards whatever the user had typed
    but not yet saved. A warning that does not mention its own cost is not a
    warning.
    """
    import os

    target = tmp_path / "job.yaml"
    target.write_text("name: original\n", encoding="utf-8")
    stale_mtime = os.path.getmtime(target)

    # Make the external version's timestamp deterministic across filesystems.
    later = stale_mtime + 60
    target.write_text("name: terminal\n", encoding="utf-8")
    os.utime(target, (later, later))

    with pytest.raises(IOError) as excinfo:
        yaml_io.save_job_yaml(str(target), "name: browser\n", expected_mtime=stale_mtime)

    assert target.read_text(encoding="utf-8") == "name: terminal\n", \
        "the external edit was overwritten"

    message = str(excinfo.value)
    assert "外部修改" in message, message
    assert "未保存" in message and "复制" in message, \
        f"the warning does not say the refresh costs the unsaved edit: {message}"


def test_a_matching_mtime_saves_normally(tmp_path):
    """The conflict guard must not block the ordinary save it guards."""
    import os

    target = tmp_path / "job.yaml"
    target.write_text("name: original\n", encoding="utf-8")

    result = yaml_io.save_job_yaml(
        str(target), "name: browser\n", expected_mtime=os.path.getmtime(target)
    )

    assert target.read_text(encoding="utf-8") == "name: browser\n"
    assert result is not None
