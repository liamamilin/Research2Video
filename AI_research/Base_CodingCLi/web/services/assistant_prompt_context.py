"""Retire consumed list snapshots from prompts, keeping their owned receipts."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import json
import re

from web.services.assistant_context import dumps


SOFT_LIMIT = 64000
KEEP_ROUNDS = 2
READ_LISTS = {
    'list_jobs': {''}, 'query_assistant_tasks': {''}, 'query_operations_overview': {''},
    'query_sources': {'', 'list'}, 'query_schedules': {'', 'list', 'runs'},
    'query_video': {'', 'board', 'list', 'find', 'cells', 'topics', 'quality', 'references'},
    'query_reports': {'', 'list', 'search'}, 'query_publishing': {'catalog', 'candidates'},
}
LIST_FIELDS = {
    'list_jobs': ('items',), 'query_assistant_tasks': ('items',),
    'query_operations_overview': ('matrices',), 'query_sources': ('sources',),
    'query_schedules': ('jobs', 'runs'), 'query_video': ('items', 'scripts', 'cells', 'topics'),
    'query_reports': ('items',), 'query_publishing': ('platforms', 'items'),
    'query_matrices': ('matrices',), 'query_templates': ('templates',),
}
METADATA = frozenset(('ok', 'status', 'total', 'count', 'total_requests', 'total_in_scope',
    'total_indexed', 'page', 'limit', 'offset', 'end_offset', 'next_offset', 'has_more',
    'complete', 'scope', 'scope_basis', 'coverage', 'unavailable', 'warnings', 'failed',
    'stages', 'counts', '_model_view', 'account_uid', 'platform', 'timezone',
    'attention', 'failures', 'references', 'at', 'date', 'summary', 'availability',
    'active', 'problems', 'revision', 'account'))


def _list_read(tool: str, arguments: dict) -> bool:
    if tool == 'query_sources':
        return not arguments.get('id')
    if tool == 'query_matrices':
        return not arguments.get('key')
    if tool == 'query_templates':
        return not arguments.get('key')
    # Unknown/new tools and generic API reads remain full until explicitly scoped.
    return tool in READ_LISTS and arguments.get('action', '') in READ_LISTS[tool]


@dataclass
class Snapshot:
    message: dict
    round: int
    scope: str
    summary: dict
    consumed: bool = False
    retired: bool = False


class PromptContext:
    """Only edit role=tool content after it has reached the model at least once."""

    def __init__(self, message: str, *, limit: int = SOFT_LIMIT):
        self.limit = limit
        self.snapshots: list[Snapshot] = []
        # A comparison needs both observations. Never collapse its A/B evidence.
        self.comparison = bool(re.search(r'对比|比较|差异|前后|变化|compare|before|after', message, re.I))

    def remember(self, message: dict, tool: str, arguments: dict, result: dict,
                 operation_id: str | None, round_index: int, *, ok: bool) -> None:
        if (self.comparison or not ok or not operation_id or not _list_read(tool, arguments)
                or not isinstance(result, dict) or result.get('error') or result.get('authentication_required')
                or result.get('input_card') or len(message.get('content', '')) < 2400):
            return
        if not any(isinstance(result.get(field), list) for field in LIST_FIELDS.get(tool, ())):
            return
        summary = {'_context_receipt': {
            'operation_id': operation_id, 'tool': tool, 'summary_only': True,
            'message': '先前已读列表已移出重复上下文；需要原行或字段时读取回执，不重新执行原操作。',
            'read': {'tool': 'query_operation', 'arguments': {'operation_id': operation_id}}},
            'query': deepcopy(arguments), 'metadata': {
                key: deepcopy(value) for key, value in result.items() if key in METADATA}}
        if tool == 'query_operations_overview' and isinstance(result.get('items'), dict):
            summary['metadata']['items'] = {key: deepcopy(value) for key, value in result['items'].items()
                                            if key != 'rows'}
        if len(dumps(summary)) >= len(message['content']) * .7:
            return
        scope = tool + ':' + json.dumps(arguments, sort_keys=True, ensure_ascii=False, default=str)
        self.snapshots.append(Snapshot(message, round_index, scope, summary))

    def consumed(self) -> None:
        # Called only after a successful model response. Fresh tool results from
        # that response are registered later, so parallel calls stay together.
        for snapshot in self.snapshots:
            snapshot.consumed = True

    def prepare(self, messages: list[dict], round_index: int) -> dict:
        before = len(dumps(messages))
        size, retired = before, 0
        latest = {snapshot.scope: snapshot for snapshot in self.snapshots}
        for snapshot in self.snapshots:
            if snapshot.retired or not snapshot.consumed or round_index - snapshot.round <= KEEP_ROUNDS:
                continue
            superseded = latest[snapshot.scope] is not snapshot
            if size <= self.limit and not superseded:
                continue
            summary = deepcopy(snapshot.summary)
            summary['_context_receipt']['reason'] = 'newer_same_query' if superseded else 'prompt_budget'
            content = dumps(summary)
            size -= len(snapshot.message['content']) - len(content)
            snapshot.message['content'] = content
            snapshot.retired = True
            retired += 1
        return {'message_chars_before': before, 'context_retired': retired,
                'context_saved_chars': before - len(dumps(messages))}
