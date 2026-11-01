"""Coverage and editorial adoption do not depend on a particular vendor or report format."""
from dataclasses import replace
import json

from core import source_coverage, source_supply as supply, sources
from core.search import SearchResult
from core.source_supply_store import SupplyStore
from tests.test_source_supply import setup, JOB, item  # noqa: F401


def test_living_state_uses_old_published_page_and_current_context(setup, monkeypatch):
    engine, job, profile, storage = setup
    spec = 'page:https://new-vendor.example/plans'
    profile['coverage'] = [{'key': 'vendor', 'subject': 'New vendor', 'jobs': [JOB], 'topics': ['price', 'limits'],
                            'sources': [{'spec': spec, 'role': 'state'}]}]
    job['_collection_window'] = {'days': 1, 'start': '2026-10-30T00:00:00+00:00', 'end': '2026-10-31T00:00:00+00:00', 'timezone': 'UTC'}
    def page(url, **k):
        return [SearchResult('Plan', url, '2020-01-01', ['Monthly $20; annual $17'], 'page')]
    monkeypatch.setattr(supply, 'read_page', page)
    got, _, pending, receipt = supply.collect(engine, job, profile)
    assert any('首次观察当前状态' in str(row.excerpts) for row in got)
    assert pending['sources'][spec]['delivered'] == 1
    assert receipt['coverage'][0]['status'] == 'current'
    storage.commit(pending, 'background', 'one.md')
    got = supply.collect(engine, job, profile)[0]
    assert any('当前背景' in str(row.excerpts) and row.url == spec[5:] for row in got)


def test_detail_budget_counts_successful_reads_and_limits_total_attempts(setup, monkeypatch):
    engine, job, profile, _ = setup
    job.update(sources=['rss:https://news.example/feed'], sources_limit=5)
    profile.update(detail_pages=2, detail_attempts=4)
    monkeypatch.setattr(sources, 'fetch_source', lambda *a, **k: [replace(item(i, provider='rss'), url=f'https://news.example/a{i}') for i in range(5)])
    def read(url, **k):
        if url.endswith(('a0', 'a1')):
            raise ValueError('403')
        return [SearchResult('Body', url, excerpts=['usable full body'])]
    monkeypatch.setattr(supply, 'read_page', read)
    got, _, _, receipt = supply.collect(engine, job, profile)
    assert receipt['detail_budget']['attempts'] == 4
    assert receipt['detail_budget']['successful'] == 2
    assert sum('usable full body' in str(r.excerpts) for r in got) == 2


def test_offer_and_report_adoption_are_independent_with_bounded_retry(setup, monkeypatch):
    engine, job, profile, storage = setup
    monkeypatch.setattr(supply, 'read_page', lambda *a, **k: [item(1), item(2)])
    _, _, pending, _ = supply.collect(engine, job, profile)
    first, second = [r['id'] for r in pending['candidates']]
    storage.commit(pending, '<!-- material-use: ' + json.dumps({'selected': [first, 'Munknown'], 'deferred': [second]}) + ' -->', 'one.md')
    records = storage.candidates(JOB)
    assert records[first]['status'] == 'reported' and records[second]['status'] == 'deferred'
    assert 'Munknown' not in records
    assert len(storage.baseline(JOB)['items']) == 2  # observation/delivery baseline still advances
    assert storage.retry_candidates(JOB, now=pending['at'] + 100, limit=3) == []
    retry = storage.retry_candidates(JOB, now=pending['at'] + 90000, limit=3)
    assert [r['id'] for r in retry] == [second]
    assert storage.retry_candidates(JOB, now=pending['at'] + 15 * 86400, limit=3) == []


def test_reference_urls_do_not_prove_editorial_adoption(setup, monkeypatch):
    engine, job, profile, storage = setup
    monkeypatch.setattr(supply, 'read_page', lambda *a, **k: [item()])
    pending = supply.collect(engine, job, profile)[2]
    storage.commit(pending, '## 来源\nhttps://source.example/notes', 'one.md')
    assert next(iter(storage.candidates(JOB).values()))['status'] == 'offered'
    lines = (storage.root / 'attempts.jsonl').read_text().splitlines()
    assert all(isinstance(json.loads(line), dict) for line in lines)


def test_coverage_failure_and_staleness_remain_visible_and_drive_discovery(tmp_path):
    profile = {'key': 'p', 'jobs': {'j': {}}, 'coverage': [
        {'key': 'prices', 'queries': ['vendor plans'], 'max_age_hours': 24,
         'sources': [{'spec': 'page:https://vendor.example/plans', 'role': 'state'}]},
        {'key': 'cases', 'queries': ['real cases'], 'sources': []}]}
    store = source_coverage.CoverageStore(tmp_path, 'p')
    assert store.status(profile, now=100000)[0]['status'] == 'missing'
    store.record({'page:https://vendor.example/plans': {'scanned': 1}}, at=100000)
    assert store.status(profile, now=100001)[0]['status'] == 'current'
    assert store.status(profile, now=200000)[0]['status'] == 'stale'
    store.record({'page:https://vendor.example/plans': {'error': '403'}}, at=200000)
    row = store.status(profile, now=200001)[0]
    assert row['status'] == 'unavailable' and row['read_at'] == 100000
    assert set(source_coverage.discovery_queries(profile, tmp_path)) == {'vendor plans', 'real cases'}


def test_discovered_source_can_fill_configured_coverage_goal(tmp_path):
    profile = {'key': 'p', 'jobs': {'j': {}}, 'coverage': [{'key': 'pricing', 'sources': []}]}
    storage = SupplyStore(tmp_path, 'p')
    storage.add({'spec': 'page:https://vendor.example/plans', 'jobs': ['j'], 'coverage': ['pricing'], 'role': 'state'})
    rows = source_coverage.CoverageStore(tmp_path, 'p').status(profile)
    assert rows[0]['cell'] == 'pricing' and rows[0]['role'] == 'state'


def test_snapshot_volume_has_separate_material_quota(setup, monkeypatch):
    engine, job, profile, _ = setup
    job.update(sources=['hf_models:trending'], sources_limit=20)
    profile['jobs'][JOB]['mode'] = 'context'
    monkeypatch.setattr(sources, 'fetch_source', lambda *a, **k: [item(i, provider='hf_models') for i in range(50)])
    got, _, _, receipt = supply.collect(engine, job, profile)
    assert len(got) == 2 and receipt['sources'][job['sources'][0]]['role'] == 'snapshot'


def test_assistant_can_find_adopted_material_and_its_report(client, web_env, monkeypatch):
    import yaml
    from core.llm import ChatResponse
    from tests.test_assistant_api import login, fake_llm, tool
    root = web_env[0]
    folder = root / 'config/source_supply'
    folder.mkdir(parents=True, exist_ok=True)
    (folder / 'pilot.yaml').write_text(yaml.safe_dump({'jobs': {'job': {}}}))
    storage = SupplyStore(root / 'state', 'pilot')
    row = {'id': 'M1', 'source': 'page:https://vendor.example/plans', 'role': 'state',
           'key': 'k', 'fingerprint': 'f', 'item': {'title': 'Vendor pricing', 'url': 'https://vendor.example/plans', 'excerpts': ['conditions']}}
    storage.commit({'job': 'job', 'at': 100000, 'delivered': {}, 'sources': {}, 'candidates': [row]},
                   '<!-- material-use: {"selected":["M1"]} -->', 'output/one.md')
    headers = login(client)
    response = client.get('/api/sources/materials', params={'status': 'reported', 'text': 'pricing'})
    assert response.status_code == 200
    assert response.json()['items'][0]['report'] == 'output/one.md'
    fake_llm(monkeypatch, [tool('query_sources', action='materials', text='pricing', status='reported'), ChatResponse(content='已取得采用记录。')])
    reply = client.post('/api/assistant', headers=headers, json={'message': '查看采集素材采用记录'}).json()
    assert reply['actions'][0]['ok'], reply
