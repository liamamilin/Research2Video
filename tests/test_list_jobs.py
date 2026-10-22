"""Which Job files count as Jobs."""

from __future__ import annotations

from core.config import list_jobs, load_job


def test_the_archive_folder_is_not_part_of_the_job_list(tmp_path):
    for rel in ("monitoring/a.yaml", "archive/video/old.yaml", "_templates/t.yaml", "deep/archive/keep.yaml"):
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("name: x\nprompt: y\n", encoding="utf-8")
    assert list_jobs(str(tmp_path)) == ["deep/archive/keep", "monitoring/a"]  # only the top-level archive/ is put away
    assert load_job(str(tmp_path), "archive/video/old") is not None  # but by its path it still loads
