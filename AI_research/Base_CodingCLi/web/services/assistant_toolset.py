"""Per-request tool exposure. Native dispatch remains the permission boundary."""
from __future__ import annotations

import re
from collections.abc import Callable


LOADER = 'load_assistant_tools'
CORE = frozenset(('query_operation', 'query_background', 'query_assistant_tasks',
    'query_operations_overview', 'application_capabilities', 'application_actions', 'read_application'))
GROUPS = {
    'research': ('list_jobs', 'get_job', 'create_job', 'update_job', 'delete_job', 'run_job',
        'cancel_job', 'job_history', 'clone_job', 'query_run', 'cancel_run', 'query_templates', 'manage_template'),
    'reports': ('query_reports', 'update_report', 'distribute_report'),
    'intelligence': ('query_rounds', 'manage_round', 'query_actions', 'update_action'),
    'video': ('query_video', 'create_video', 'manage_video', 'continue_video',
        'configure_production', 'operate_application'),
    'publishing': ('query_publishing', 'manage_publishing', 'publish_local_video',
        'save_publishing_draft', 'query_video', 'operate_application'),
    'matrices': ('query_matrices', 'manage_matrix', 'inspect_matrix_operations',
        'configure_matrix_task', 'configure_production', 'list_jobs', 'get_job',
        'create_job', 'update_job', 'operate_application'),
    'schedules': ('query_schedules', 'manage_schedule', 'list_jobs', 'get_job', 'query_run'),
    'sources': ('query_sources', 'manage_source', 'get_job', 'update_job'),
    'settings': ('application_overview', 'diagnose_application', 'query_configuration',
        'manage_configuration', 'rebuild_index', 'test_notification', 'manage_users',
        'manage_own_account', 'request_protected_input', 'maintain_service', 'operate_application'),
    'batches': ('prepare_application_batch', 'query_application_batch', 'manage_application_batch'),
}
KEYWORDS = {
    'research': r'研究|任务|运行记录|\bjobs?\b|research',
    'reports': r'报告|研究底稿|report',
    'intelligence': r'轮次|情报|行动项|round|intelligence',
    'video': r'视频|文稿|讲稿|出片|配方|旁白|video|script|render|recipe',
    'publishing': r'发布|投稿|上传|首评|抖音|快手|小红书|B站|bilibili|douyin|publish',
    'matrices': r'矩阵|matrix|matrices',
    'schedules': r'周期|排期|调度|定时|cron|schedule',
    'sources': r'信息源|采集|订阅源|\brss\b|sources?',
    'settings': r'设置|配置|模型|用量|账号|用户|维护|重启|登录|诊断|configuration|settings',
    'batches': r'批量|整套|跨阶段|每个矩阵|所有矩阵|各矩阵|batch',
}
REFERENCE_GROUP = {'job': 'research', 'run': 'research', 'report': 'reports',
    'video': 'video', 'publication': 'publishing', 'schedule': 'schedules',
    'matrix': 'matrices', 'round': 'intelligence', 'action': 'intelligence', 'batch': 'batches'}
LOADER_DEFINITION = {'type': 'function', 'function': {
    'name': LOADER,
    'description': '按需加载工具参数，不执行业务操作。groups可选research/reports/intelligence/video/publishing/matrices/schedules/sources/settings/batches/other/all；也可按names加载。空参数列出当前角色可用目录；all加载全部。需要的工具未出现时先加载，不代表功能缺失。',
    'parameters': {'type': 'object', 'properties': {
        'groups': {'type': 'array', 'items': {'type': 'string'}},
        'names': {'type': 'array', 'items': {'type': 'string'}}}, 'additionalProperties': False}}}


class ToolSet:
    """Expose a small seed, then monotonically load tools within this request."""

    def __init__(self, catalog: Callable[[str], list[dict]], message: str, refs: list[dict]):
        self.catalog = catalog
        self._catalogs: dict[str, list[dict]] = {}
        self.loaded = set(CORE)
        groups = [group for group, pattern in KEYWORDS.items() if re.search(pattern, message, re.I)]
        if not groups and len(message) <= 40 and refs:
            group = REFERENCE_GROUP.get(refs[-1].get('type'))
            if group:
                groups.append(group)
        for group in groups:
            self.loaded.update(GROUPS[group])

    def definitions(self, role: str) -> list[dict]:
        # Always intersect the current role, even after a role change mid-turn.
        return [item for item in self._available(role) if item['function']['name'] in self.loaded] + [LOADER_DEFINITION]

    def _available(self, role: str) -> list[dict]:
        if role not in self._catalogs:
            self._catalogs[role] = self.catalog(role)
        return self._catalogs[role]

    def load(self, arguments: dict, role: str) -> dict:
        if not isinstance(arguments, dict) or set(arguments) - {'groups', 'names'}:
            raise ValueError('工具加载只接受 groups 和 names 数组')
        groups, names = arguments.get('groups', []), arguments.get('names', [])
        if any(not isinstance(items, list) or any(not isinstance(item, str) for item in items)
               for items in (groups, names)):
            raise ValueError('groups 和 names 必须是字符串数组')
        available = {item['function']['name'] for item in self._available(role)}
        grouped = {group: sorted(available.intersection(tools)) for group, tools in GROUPS.items()}
        grouped['other'] = sorted(available - CORE - set().union(*map(set, GROUPS.values())))
        unknown_groups = set(groups) - set(grouped) - {'all'}
        unknown_names = set(names) - available
        if unknown_groups or unknown_names:
            # No partial load on invalid input, and no extra permissions.
            raise ValueError('未知或当前角色不可用的工具/分组：' + ', '.join(sorted(unknown_groups | unknown_names)))
        requested = set(names)
        for group in groups:
            requested.update(available if group == 'all' else grouped[group])
        self.loaded.update(requested)
        result = {'loaded_tools': sorted(self.loaded & available), 'business_operation': False,
                  'message': '工具参数将在下一次思考中提供；尚未执行任何业务操作。'}
        if not groups and not names:
            result['catalog'] = {group: tools for group, tools in grouped.items() if tools}
            result['common'] = sorted(CORE & available)
        return result

    def observe(self, name: str, arguments: dict) -> None:
        # A known call omitted from the seed still uses the original handler;
        # related tools become available for its next step without another load.
        self.loaded.add(name)
        for group, tools in GROUPS.items():
            if name in tools and name != 'operate_application':
                self.loaded.update(tools)
                break
        if name in ('application_actions', 'read_application', 'operate_application'):
            domain = str(arguments.get('domain') or arguments.get('action_id') or '')
            for marker, group in (('video', 'video'), ('recipes', 'video'), ('production', 'video'),
                                  ('matrices', 'matrices'), ('jobs', 'research'),
                                  ('sources', 'sources'), ('platforms', 'publishing')):
                if marker in domain:
                    self.loaded.update(GROUPS[group])
