"""Offline checks for the failure that previously prevented a useful comparison."""
from types import SimpleNamespace

from acquire import acquire


def test_reuses_search_body_and_moves_past_failed_page(monkeypatch, tmp_path):
    from core import search, source_pages
    from core.video.adapt import common

    marker = '不同来源中的关键操作结果'
    found = [
        search.SearchResult('搜索已有正文', 'https://one.example/article', excerpts=[marker + '已有正文。' * 60]),
        search.SearchResult('不可读', 'https://two.example/blocked', excerpts=['']),
        search.SearchResult('另一可读来源', 'https://three.example/readable'),
    ]
    class Search:
        def __init__(self, config):
            self.calls = 0
        def search(self, queries, objective):
            self.calls += 1
            return found
    monkeypatch.setattr(search, 'SearchClient', Search)
    monkeypatch.setattr(common, 'call_json', lambda client, prompt, body, schema, **kw:
                        schema(ids=['P1','P2','P3'], reasons=['相关内容来自不同来源']))
    def fetch(url, *args, **kwargs):
        if url != found[2].url:
            raise RuntimeError('Blocked page')
        return [search.SearchResult('实际正文', url, excerpts=['可读页面的第二个关键细节'])]
    monkeypatch.setattr(source_pages, 'fetch_page', fetch)
    result = acquire({}, [{'question':'需要什么操作结果','viewer_change':'理解怎么做','queries':['操作结果']}],
                     [], tmp_path, lambda cfg: SimpleNamespace(close=lambda:None), None)
    assert len(result['added']) == 2
    assert marker in result['added'][0]['text']
    assert result['added'][0]['retrieval_mode'] == 'search_returned_body_excerpt'
    assert '可读页面的第二个关键细节' in result['added'][1]['text']
    assert len(result['attempts'][0]['reads']) == 3
    assert result['attempts'][0]['reads'][1]['status'] == 'failed'


def test_irrelevant_results_do_not_become_supplement(monkeypatch, tmp_path):
    from core import search
    from core.video.adapt import common
    class Search:
        def __init__(self, config):
            self.calls = 0
        def search(self, queries, objective):
            return [search.SearchResult('无关目录', 'https://example.com/catalog', excerpts=['只列产品名'])]
    monkeypatch.setattr(search, 'SearchClient', Search)
    monkeypatch.setattr(common, 'call_json', lambda client, prompt, body, schema, **kw:
                        schema(ids=[], reasons=['不能回答问题']))
    result = acquire({}, [{'question':'具体如何操作','viewer_change':'操作结果','queries':['query']}],
                     [], tmp_path, lambda cfg:SimpleNamespace(close=lambda:None), None)
    assert result['added'] == []
