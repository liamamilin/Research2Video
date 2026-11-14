"""The assistant reads A/B experiments (spec/ab-testing.md §9a step ⑤). Administrators only, read-only."""
from pydantic import Field

from . import Arguments, Tool


class ExperimentQuery(Arguments):
    query: str = Field(default='', max_length=200)


def query(a, request, user):
    try:
        from core.experiments import records
        from core.experiments.store import ExperimentStore
        from web.settings import get_settings
        store = ExperimentStore(get_settings().paths.state_dir)
        current = [{'id': e['id'], 'name': e['definition'].get('name'), 'status': e['status'],
                    'kind': e['definition'].get('kind'),
                    'changes': e['definition'].get('arms', {}).get('B', {}).get('overrides', {}),
                    'metric': e['definition'].get('metric', {}).get('primary')}
                   for e in store.list() if e['status'] in ('draft', 'queued', 'running', 'paused')]
        done = records.listing(store)
    except Exception as exc:  # noqa: BLE001 - the A/B module is optional
        return {'available': False, 'reason': f'A/B 模块暂时读不到：{type(exc).__name__}'}
    if a.query:
        needle = a.query.casefold()
        current = [e for e in current if needle in str(e).casefold()]
        done = [e for e in done if needle in str(e).casefold()]
    return {'available': True, 'enabled': store.enabled(), 'current': current, 'records': done,
            'href': '/analytics?tab=experiments',
            'scope': 'A/B 实验的定义、状态和已存档结论。只读；新建、开始、采用都要用户在「A/B 测试」tab 里操作。'}


TOOLS = {'query_experiments': Tool(ExperimentQuery, '查询 A/B 测试：进行中和草稿的实验（改了哪个开关、看哪个指标），以及已结束实验的结论、能测出的最小差异和用户的采用选择。query 可按名称或开关筛选。只读，只对管理员开放。',
                                   query, role='admin', label='查询 A/B 实验')}
