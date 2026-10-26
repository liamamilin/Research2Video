"""Directory migration preserves old URLs, failures and in-flight tasks."""
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from web.services import video_storage as storage


@pytest.fixture
def disks(tmp_path, monkeypatch):
    state, data, target, config = [tmp_path / name for name in ('state', 'engine', 'external', 'config')]
    for p in (state, data, target, config):
        p.mkdir(exist_ok=True)
    (config / 'system.yaml').write_text('ai: {}\nvideo:\n  visual_materials:\n    enabled: true\n')
    for db, table in ((state / 'video.db', 'renders'), (data / 'jobs.sqlite3', 'jobs')):
        with sqlite3.connect(db) as conn:
            conn.execute(f'CREATE TABLE IF NOT EXISTS {table} (status TEXT)')
    monkeypatch.setattr(storage, 'paths', lambda: (state, data))
    monkeypatch.setattr(storage, 'get_settings', lambda: SimpleNamespace(paths=SimpleNamespace(config_dir=str(config))))
    monkeypatch.setattr(storage, 'resume', lambda: None)
    monkeypatch.setattr('web.routes.config.system_config_lock', lambda: storage.file_lock(str(config / 'system.yaml')))
    return state, data, target, config


def put(folder, text='video bytes'):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / 'video.mp4').write_text(text)


def test_moves_old_files_and_new_writes_use_external_directory(disks):
    state, data, target, config = disks
    put(state / 'video_renders' / 'render')
    put(data / 'jobs' / 'job' / 'attempts')
    put(data / 'voice-cache')
    put(data / 'launcher')
    storage.request_migration(str(target))
    storage.run_pending()
    record = storage.status()
    assert record['migration']['status'] == 'completed'
    assert record['migration']['moved_bytes'] == 3 * len('video bytes')
    assert (state / 'video_renders').is_symlink() and (data / 'jobs').is_symlink()
    assert (data / 'launcher').is_dir() and not (data / 'launcher').is_symlink()
    assert (data / 'jobs.sqlite3').is_file() and not (data / 'jobs.sqlite3').is_symlink()
    assert (state / 'video_renders' / 'render' / 'video.mp4').read_text() == 'video bytes'
    put(data / 'jobs' / 'new-job')
    assert (target / '制作数据/jobs/new-job/video.mp4').is_file()
    assert not list(data.glob('.*.storage-old-*'))
    assert 'visual_materials' in (config / 'system.yaml').read_text()
    assert not (data / '.storage-migration').exists()


def test_waits_for_running_and_queued_jobs_then_resumes(disks):
    state, data, target, _ = disks
    put(data / 'jobs' / 'busy')
    with sqlite3.connect(data / 'jobs.sqlite3') as db:
        db.execute("INSERT INTO jobs VALUES ('RUNNING')")
    storage.request_migration(str(target))
    storage.run_pending()
    assert storage.status()['migration']['status'] == 'waiting'
    assert not (data / 'jobs').is_symlink()
    assert not (data / '.storage-migration').exists()
    with sqlite3.connect(data / 'jobs.sqlite3') as db:
        db.execute("UPDATE jobs SET status='COMPLETED'")
    storage.run_pending()
    assert storage.status()['migration']['status'] == 'completed'


def test_conflicting_target_keeps_original_files_and_reports_failure(disks):
    state, data, target, _ = disks
    put(state / 'video_renders' / 'render', 'original')
    put(target / '研究成片' / 'render', 'different')
    storage.request_migration(str(target))
    storage.run_pending()
    assert storage.status()['migration']['status'] == 'failed'
    assert (state / 'video_renders' / 'render/video.mp4').read_text() == 'original'
    assert (target / '研究成片/render/video.mp4').read_text() == 'different'
    assert not (data / '.storage-migration').exists()


def test_unplugged_volume_rejected_before_mkdir(disks, monkeypatch):
    _, _, target, _ = disks
    monkeypatch.setattr(storage, 'available', lambda p: False)
    with pytest.raises(ValueError, match='未连接'):
        storage.request_migration(str(target / 'new'))
    assert not (target / 'new').exists()


def test_destination_cannot_be_inside_source_or_its_ancestor(disks):
    state, _, _, _ = disks
    put(state / 'video_renders')
    for path in (state / 'video_renders/recursive', state):
        with pytest.raises(ValueError, match='独立'):
            storage.request_migration(str(path))


def test_second_move_preserves_old_access_paths(disks):
    state, data, target, _ = disks
    put(data / 'jobs/job')
    storage.request_migration(str(target))
    storage.run_pending()
    other = target.parent / 'other-disk'
    storage.request_migration(str(other))
    storage.run_pending()
    assert storage.status()['directory'] == str(other)
    assert (data / 'jobs/job/video.mp4').read_text() == 'video bytes'
    assert not (target / '制作数据/jobs').exists()


def test_picker_lists_folders_only(disks):
    _, _, target, _ = disks
    (target / 'subfolder').mkdir()
    (target / 'file').touch()
    (target / '.hidden').mkdir()
    assert storage.browse(str(target))['folders'] == [{'name': 'subfolder', 'path': str(target / 'subfolder')}]


def test_can_retry_partial_migration_to_the_same_target(disks):
    state, data, target, _ = disks
    put(state / 'video_renders/render')
    put(data / 'jobs/job', 'original')
    put(target / '制作数据/jobs/job', 'conflict')
    storage.request_migration(str(target))
    storage.run_pending()
    assert storage.status()['migration']['status'] == 'failed'
    assert (state / 'video_renders').is_symlink()
    fixed = target / '制作数据/jobs/job/video.mp4'
    fixed.write_text('original')
    import os
    stamp = (data / 'jobs/job/video.mp4').stat().st_mtime + 5
    os.utime(fixed, (stamp, stamp))
    storage.request_migration(str(target))
    storage.run_pending()
    assert storage.status()['migration']['status'] == 'completed'
    assert (data / 'jobs/job/video.mp4').read_text() == 'original'


def test_recovers_interrupted_directory_rename(disks):
    state, _, target, _ = disks
    put(state / 'video_renders/render')
    storage.request_migration(str(target))
    backup = state / '.video_renders.storage-old-interrupted'
    (state / 'video_renders').rename(backup)
    storage.run_pending()
    assert storage.status()['migration']['status'] == 'completed'
    assert (state / 'video_renders/render/video.mp4').read_text() == 'video bytes'
    assert not backup.exists()


def test_api_requires_admin_and_returns_useful_path_error(client, disks):
    assert client.get('/api/config/video-storage').status_code == 401
    client.post('/api/auth/login', json={'username': 'viewer', 'password': 'viewer-pass-123'})
    assert client.get('/api/config/video-storage').status_code == 403
    client.post('/api/auth/login', json={'username': 'admin', 'password': 'admin-pass-123'})
    result = client.put('/api/config/video-storage', json={'directory': 'relative'},
                        headers={'X-CSRF-Token': client.cookies.get('ai_research_csrf')})
    assert result.status_code == 422
    assert result.json()['error']['code'] == 'storage_directory_invalid'
    assert '绝对' in result.json()['error']['message']


def test_finder_recreated_metadata_does_not_abort_a_verified_move(disks, monkeypatch):
    state, data, target, _ = disks
    put(state / 'video_renders/render')
    real_rmtree = storage.shutil.rmtree
    def finder_race(path):
        real_rmtree(path)
        if '.video_renders.storage-old-' in str(path):
            path.mkdir()
            (path / '.DS_Store').write_text('Finder metadata')
            raise OSError(66, 'Directory not empty')
    monkeypatch.setattr(storage.shutil, 'rmtree', finder_race)
    storage.request_migration(str(target))
    storage.run_pending()
    assert storage.status()['migration']['status'] == 'completed'
    assert (state / 'video_renders/render/video.mp4').read_text() == 'video bytes'
    assert (data / 'jobs').is_symlink()


def test_imported_asset_libraries_retain_their_supported_original_paths(disks):
    _, data, target, _ = disks
    for name in ('creative-images', 'music-assets', 'music-jobs', 'music-curation', 'input-documents'):
        put(data / name)
    storage.request_migration(str(target))
    storage.run_pending()
    for name in ('creative-images', 'music-assets', 'music-jobs', 'music-curation', 'input-documents'):
        assert not (data / name).is_symlink()
        assert (data / name / 'video.mp4').is_file()


def test_reselecting_same_directory_with_another_filesystem_spelling_is_a_noop(disks):
    state, data, target, _ = disks
    alias = target.with_name(target.name.upper())
    if not alias.exists() or not alias.samefile(target):
        pytest.skip('case-sensitive test filesystem has no alternate spelling')
    put(state / 'video_renders/render')
    put(data / 'jobs/job')
    storage.request_migration(str(target))
    storage.run_pending()
    before = storage.status()['migration']
    result = storage.request_migration(str(alias))
    assert result['migration'] == before
    assert (data / 'jobs/job/video.mp4').read_text() == 'video bytes'


def test_filesystem_alias_cannot_nest_a_destination_inside_source(disks):
    state, _, _, _ = disks
    source = state / 'video_renders'
    put(source)
    alias = source.with_name(source.name.upper())
    if not alias.exists() or not alias.samefile(source):
        pytest.skip('case-sensitive test filesystem has no alternate spelling')
    with pytest.raises(ValueError, match='独立'):
        storage.request_migration(str(alias / 'recursive'))


def test_background_move_keeps_launcher_active_without_browser_polling(disks, monkeypatch):
    import threading
    from core import activity
    state, _, _, _ = disks
    entered, heartbeat, release = threading.Event(), threading.Event(), threading.Event()
    states = []
    def move():
        entered.set()
        release.wait(timeout=1)
    def pulse(directory):
        states.append(directory)
        heartbeat.set()
    monkeypatch.setattr(storage, 'run_pending', move)
    monkeypatch.setattr(activity, 'mark_activity_background', pulse)
    worker = threading.Thread(target=storage._run_with_activity)
    worker.start()
    try:
        assert entered.wait(timeout=0.5) and heartbeat.wait(timeout=0.5)
        assert states == [str(state)]
    finally:
        release.set()
        worker.join(timeout=1)
    assert not worker.is_alive()


def test_cleanup_retries_nonempty_subdirectory_without_abandoning_other_verified_files(disks, monkeypatch):
    state, _, target, _ = disks
    put(state / 'video_renders/render')
    real = storage.shutil.rmtree
    interrupted = []
    def race_once(path):
        if not interrupted and '.video_renders.storage-old-' in str(path):
            interrupted.append(path)
            (path / 'render/.DS_Store').write_text('metadata')
            raise OSError(66, 'Directory not empty')
        return real(path)
    monkeypatch.setattr(storage.shutil, 'rmtree', race_once)
    storage.request_migration(str(target))
    storage.run_pending()
    assert storage.status()['migration']['status'] == 'completed'
    assert (state / 'video_renders/render/video.mp4').read_text() == 'video bytes'
    assert interrupted and not interrupted[0].exists()
