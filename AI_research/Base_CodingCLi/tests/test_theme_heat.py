"""Observed Bilibili counters, historical growth, category coverage and recommendation ordering."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from core import theme_discovery, theme_ranking
from core.theme_metrics import BilibiliMetrics, MetricStore, video_id
from core.theme_store import ThemeStore
from tests.test_theme_workbench import theme_env, candidate  # noqa: F401
from tests.test_video_scripts import video_env  # noqa: F401

BV = 'BV1bGqUYSEw4'
URL = f'https://www.bilibili.com/video/{BV}/'
NOW = 1793112000


class Client:
    cookies = {'SESSDATA': 'test-only'}
    def __init__(self):
        self.calls = 0
        self.fail = False
        self.views = 1000
    def request(self, path, **params):
        self.calls += 1
        if self.fail:
            from web.services.bilibili import BilibiliError
            error = BilibiliError('private transport details must not be displayed')
            error.http_status = 412
            raise error
        return {'bvid': params['bvid'], 'title': '实际公开视频', 'pubdate': NOW - 86400,
                'stat': {'view': self.views, 'like': 0, 'favorite': 10, 'coin': False, 'share': '7'}}, None


def test_actual_counters_cache_and_growth_are_different_from_missing_values(tmp_path):
    now = [NOW]
    client = Client()
    collector = BilibiliMetrics(str(tmp_path), client=client, clock=lambda: now[0], spacing=0)
    first = collector.collect(URL)
    assert first['metrics'] == {'view': 1000, 'like': 0, 'favorite': 10}
    assert first['metrics_status'] == 'fresh' and 'growth' not in first
    now[0] += 60
    second = collector.collect(URL)
    assert client.calls == 1 and second['metrics_status'] == 'cached'
    assert second['metrics_observed_at'] == NOW
    now[0] += 21600
    client.views = 1600
    third = collector.collect(URL)
    assert third['growth']['views'] == 600 and third['growth']['hours'] > 6
    assert third['metrics_observed_at'] == now[0] and client.calls == 2


def test_rejected_connection_retains_old_counts_and_backs_off(tmp_path):
    now = [NOW]
    client = Client()
    collector = BilibiliMetrics(str(tmp_path), client=client, clock=lambda: now[0], spacing=0)
    collector.collect(URL)
    now[0] += 21601
    client.fail = True
    old = collector.collect(URL)
    assert old['metrics'] and old['metrics_status'] == 'stale'
    assert old['metrics_observed_at'] == NOW and 'private' not in old['metrics_error']
    unavailable = collector.collect('https://www.bilibili.com/video/BV11cUaBQEor/')
    assert unavailable['metrics'] == {} and unavailable['metrics_status'] == 'unavailable'
    assert client.calls == 2


def test_only_supported_video_urls_are_read(tmp_path):
    client = Client()
    collector = BilibiliMetrics(str(tmp_path), client=client, spacing=0)
    for url in ['https://bilibili.com.evil.test/video/'+BV, 'https://bilibili.com/read/cv123', 'file:///video/'+BV]:
        assert video_id(url) is None and collector.collect(url)['metrics'] == {}
    assert client.calls == 0 and video_id(URL+'?p=2') == BV


def signal(key, views, likes, favorites, days=3, **fields):
    return {'id': key, 'category': 'one', 'url': 'https://example.org/'+key, 'metrics_status': 'fresh',
            'metrics_observed_at': NOW, 'video_published_at': NOW - days*86400,
            'metrics': {'view': views, 'like': likes, 'favorite': favorites}, **fields}


def test_heat_rewards_recent_engaged_work_and_does_not_claim_unobserved_growth():
    old = signal('old', 200000, 1000, 500, days=700)
    recent = signal('new', 10000, 700, 1000)
    assert theme_ranking.heat(recent, NOW)['heat_score'] > theme_ranking.heat(old, NOW)['heat_score']
    first = theme_ranking.rank([recent], now=NOW)
    assert first['primary']['speed_kind'] == 'lifetime_average'
    measured = {**recent, 'growth': {'views_per_day': 7000}}
    assert theme_ranking.rank([measured], now=NOW)['primary']['speed_kind'] == 'observed_growth'
    assert theme_ranking.heat({**recent, 'metrics_status': 'stale'}, NOW)['heat_score'] < first['primary']['heat_score']
    assert theme_ranking.rank([{'id':'x','metrics':{}}], now=NOW)['primary'] is None


def test_multiple_candidates_are_ranked_within_category_and_irrelevant_viral_work_is_ignored():
    rows = [signal('small', 100, 2, 1), signal('strong', 50000, 3000, 5000), signal('irrelevant', 2000000, 100000, 200000)]
    candidates = [{'category':'one','title':title,'reason':'观众有明确问题','relevance':relevance,'source_ids':[sid]}
                  for title,relevance,sid in [('小样本作品的普通问题',90,'small'),('高收藏作品的具体操作问题',90,'strong'),('完全无关的热门娱乐问题',20,'irrelevant')]]
    result = theme_discovery.select(json.dumps({'themes':candidates}), [{'key':'one','audience':'普通用户'}], rows)
    assert len(result) == 1 and result[0]['title'] == '高收藏作品的具体操作问题'


def test_same_day_refresh_updates_evidence_but_preserves_edits_dismissals_and_started_work(tmp_path):
    store = ThemeStore(str(tmp_path))
    rows = store.publish('demo','2026-10-27', [candidate('one','免费用户的升级判断问题'), candidate('two','怎样整理工作表与报告材料'),
        candidate('three','订阅失败的常见处理步骤'),candidate('four','图片输入应该怎样操作')])
    store.edit(rows[1]['id'],title='我已编辑的主题需要保留',angle='自己的角度',audience='新用户',notes='个人材料')
    store.attach(rows[2]['id'],'already-started',True)
    store.dismiss(rows[3]['id'],True)
    new = [candidate(r['category'],f'分类{r["category"]}全新的推荐角度是什么') for r in rows]
    new[0]['ranking']={'score':80}
    updated = store.publish('demo','2026-10-27',new,refresh=True)
    assert len(updated) == 1 and updated[0]['id'] == rows[0]['id'] and updated[0]['ranking']['score'] == 80
    assert store.get(rows[1]['id'])['title'] == '我已编辑的主题需要保留'
    assert store.get(rows[2]['id'])['script_id'] == 'already-started'
    assert store.get(rows[3]['id'])['status'] == 'dismissed'


def test_every_category_collects_bilibili_metrics_even_after_the_first_24_signals(theme_env, monkeypatch):
    from core.engine import ResearchEngine
    from core.search import SearchResult
    from web.services import theme_workbench
    import yaml
    (theme_env / 'config/topic_discovery').mkdir(exist_ok=True)
    (theme_env / 'config/topic_discovery/demo.yaml').write_text('platforms: [bilibili]\n')
    for index in range(5):
        (theme_env / f'config/content_columns/extra{index}.yaml').write_text(yaml.safe_dump({'name':f'栏目{index}','matrix':'demo',
            'type':'explainer','promise':f'需要讲清楚的问题{index}','audience':'普通用户','production_ids':[]}))
    calls = []
    class Metrics:
        requests = 0
        hits = 0
        blocked = ''
        def __init__(self, *args): pass
        def collect(self,url):
            calls.append(url);self.requests+=1
            return {'metrics':{'view':1000,'like':100,'favorite':50},'bvid':url,'metrics_status':'fresh','metrics_observed_at':NOW}
    class Search:
        calls = 0
        def __init__(self,*args,**kwargs):pass
        def search(self, queries, **kwargs):
            assert len(queries) == 1
            self.calls+=1
            domain=queries[0].split()[0].removeprefix('site:')
            return [SearchResult(f'视频标题{i}',f'https://{domain}/video/entry{i}',excerpts=['内容']) for i in range(8)]
    class Model:
        usage = {}
        def __init__(self,*args,**kwargs):pass
        def chat(self,messages,**kwargs):
            payload=json.loads(messages[1]['content'])
            assert len(payload['categories']) == 6
            for cat in payload['categories']:
                assert sum(s['category']==cat['key'] and bool(s['metrics']) for s in payload['signals']) == 6
            return SimpleNamespace(content='{"themes": []}')
        def close(self):pass
    monkeypatch.setattr(theme_discovery,'BilibiliMetrics',Metrics)
    monkeypatch.setattr(theme_discovery,'SearchClient',Search)
    monkeypatch.setattr(theme_discovery,'LLMClient',Model)
    engine=ResearchEngine(config_dir=str(theme_env/'config'),jobs_dir=str(theme_env/'jobs'),workspace_dir=str(theme_env))
    assert engine.run_job(theme_workbench.ensure_job('demo'))
    assert len(calls)==36
