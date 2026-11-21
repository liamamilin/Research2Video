import pytest

from g2.workspace import project_path


def moved_workspace(tmp_path):
    project = tmp_path / "project"
    data = project / "g3/data"
    data.mkdir(parents=True)
    external = tmp_path / "外接盘/制作数据/jobs"
    external.mkdir(parents=True)
    (data / "jobs").symlink_to(external, target_is_directory=True)
    return project, data / "jobs", external


def test_task_inputs_and_outputs_keep_project_manifest_names_after_relocation(tmp_path):
    project, alias, external = moved_workspace(tmp_path)
    for rel in ("job/input/source.txt", "job/attempts/2/g2-run/fixtures/video-ir.json",
                "job/attempts/2/g2-run/artifacts/captions.json"):
        assert project_path(project, external / rel) == alias / rel
        assert project_path(project, alias / rel) == alias / rel
        assert project_path(project, external / rel).relative_to(project).as_posix() == f"g3/data/jobs/{rel}"


def test_local_fixtures_still_work(tmp_path):
    project = tmp_path / "project"
    assert project_path(project, project / "g2/fixtures/source.txt") == project / "g2/fixtures/source.txt"


def test_default_run_directory_can_be_linked_to_external_storage(tmp_path):
    project, _, external = moved_workspace(tmp_path)
    alias = project / "g2/runs"
    alias.parent.mkdir()
    target = external.parent / "g2-runs"
    target.mkdir()
    alias.symlink_to(target, target_is_directory=True)
    assert project_path(project, alias / "run") == alias / "run"
    assert project_path(project, target / "run") == alias / "run"


def test_an_unconfigured_external_directory_is_rejected(tmp_path):
    project, _, external = moved_workspace(tmp_path)
    with pytest.raises(ValueError):
        project_path(project, external.parent / "unrelated/source.txt")


def test_nested_link_cannot_escape_the_configured_storage(tmp_path):
    project, alias, external = moved_workspace(tmp_path)
    other = tmp_path / "unrelated"
    other.mkdir()
    (external / "escape").symlink_to(other, target_is_directory=True)
    with pytest.raises(ValueError):
        project_path(project, alias / "escape/source.txt")


def test_offline_storage_is_not_replaced_by_a_local_folder(tmp_path):
    project, alias, external = moved_workspace(tmp_path)
    external.rmdir()
    with pytest.raises(ValueError):
        project_path(project, alias / "job/input/source.txt")
    assert alias.is_symlink() and not external.exists()
