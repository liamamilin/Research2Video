"""Each research run is an immutable input, including same-day reruns between ticks."""
import os
from pathlib import Path
from types import SimpleNamespace

from core import report_identity
from core.engine import ResearchEngine
from core.video import auto, production, store, topic_store
from core.video import autopilot_store as ap
from tests.test_auto_write import _auto_off, _fresh, _pending, prod_env  # noqa: F401
from tests.test_video_scripts import video_env  # noqa: F401
from tests.test_video_flash import DISCOVERY
from web.services import video_autopilot


def save(root: Path, path: Path, text=DISCOVERY):
    engine = ResearchEngine.__new__(ResearchEngine)
    engine.workspace_dir = str(root)
    return Path(engine._save_output(text, str(path)))


def test_same_day_runs_have_distinct_stable_topics_and_old_links_survive(prod_env):
    root = prod_env
    path = root / 'output/flash/2026-10-19_ai_flash_discovery.md'
    # The fixture's original is kept; two new runs are siblings, not replacements.
    original = path.read_bytes()
    first = save(root, path)
    second = save(root, path, DISCOVERY.replace('Opus 5.5', '另一个模型'))
    source = topic_store.resolve(str(root / 'jobs'), 'flash')
    one = topic_store.read_report(source, str(first), str(root))[1]
    two = topic_store.read_report(source, str(second), str(root))[1]
    assert one[0].code == two[0].code == 'F1' and one[0].id != two[0].id
    assert one[0].id == topic_store.read_report(source, str(first), str(root))[1][0].id
    assert path.read_bytes() == original and first.read_bytes() != second.read_bytes()
    video_autopilot.sync_topics()
    assert topic_store.get(one[0].id).title == one[0].title
    assert topic_store.get(two[0].id).title == two[0].title
    assert {t.id for t in topic_store.load('flash')[1]} == {t.id for t in two}


def test_two_new_reports_before_one_tick_are_both_claimed_once(prod_env):
    now = _fresh(prod_env, count=1)
    base = prod_env / 'output/flash/2026-10-19_ai_flash_discovery.md'
    sibling = save(prod_env, base)
    stamp = now - 100
    os.utime(sibling, (stamp, stamp))
    found = _pending(prod_env, now)
    assert {row['report'] for row in found} == {str(base), str(sibling)}
    assert video_autopilot.fire_auto(now) == 2
    assert video_autopilot.fire_auto(now) == 0
    store.init_db()
    assert _pending(prod_env, now) == []
    assert len(ap.list_runs()) == 2


def test_changed_version_never_reuses_an_existing_topic_or_handled_receipt(prod_env):
    now = _fresh(prod_env, count=1)
    base = prod_env / 'output/flash/2026-10-19_ai_flash_discovery.md'
    report_identity.publish(str(base), str(prod_env))
    source = topic_store.resolve(str(prod_env / 'jobs'), 'flash')
    old = topic_store.read_report(source, str(base), str(prod_env))[1][0]
    video_autopilot.sync_topics()
    assert video_autopilot.fire_auto(now) == 1
    # Simulate an external editor changing a completed report despite the engine's append-only policy.
    base.write_text(DISCOVERY.replace('Opus 5.5', '新一代模型'))
    os.utime(base, (now + 100, now + 100))
    new = topic_store.read_report(source, str(base), str(prod_env))[1][0]
    assert new.id != old.id
    video_autopilot.sync_topics()
    assert topic_store.get(old.id).title == old.title
    assert topic_store.load('flash')[1][0].id == new.id
    assert len(_pending(prod_env, now + 200)) == 1


def test_identity_is_portable_and_empty_latest_run_does_not_fall_back(prod_env, tmp_path):
    base = prod_env / 'output/flash/2026-10-19_ai_flash_discovery.md'
    newer = save(prod_env, base, '# 本次没有新的选题\n')
    other = tmp_path / 'another' / newer.relative_to(prod_env)
    other.parent.mkdir(parents=True)
    other.write_bytes(newer.read_bytes())
    assert report_identity.describe(str(newer), str(prod_env))['id'] == report_identity.describe(str(other), str(tmp_path / 'another'))['id']
    video_autopilot.sync_topics()
    assert topic_store.load('flash') == ('2026-10-19', [])


def test_old_worker_receipt_bridges_only_the_manifest_it_actually_claimed(prod_env, monkeypatch):
    now = _fresh(prod_env, count=1)
    monkeypatch.setattr(ap, 'time', SimpleNamespace(time=lambda: now))
    base = prod_env / 'output/flash/2026-10-19_ai_flash_discovery.md'
    report_identity.publish(str(base), str(prod_env))
    manifest = Path(str(base) + '.report.json')
    os.utime(manifest, (now - 90, now - 90))
    found = _pending(prod_env, now)[0]
    legacy = {key: value for key, value in found.items() if key != 'report_identity'}
    run = ap.create_run(legacy, [])
    assert not _pending(prod_env, max(now, run['started_at']) + 100)
    # Neither a replacement body nor a manifest published after that receipt
    # proves that the legacy worker consumed this new input.
    base.write_text(DISCOVERY.replace('Opus 5.5', '后来发布的模型'))
    os.utime(base, (run['started_at'] + 1, run['started_at'] + 1))
    assert len(_pending(prod_env, run['started_at'] + 100)) == 1


def test_batch_never_reads_a_later_body_under_its_original_identity(prod_env):
    now = _fresh(prod_env, count=1)
    base = prod_env / 'output/flash/2026-10-19_ai_flash_discovery.md'
    report_identity.publish(str(base), str(prod_env))
    found = _pending(prod_env, now)[0]
    base.write_text(DISCOVERY.replace('Opus 5.5', '批次之后的新模型'))
    import pytest

    with pytest.raises(ValueError, match='报告已变更'):
        video_autopilot.pick_topics(found)
    batch = ap.create_run(found, [])
    assert video_autopilot._tick_run(batch) == 0
    assert ap.get_run(batch['id'])['status'] == 'DONE'
