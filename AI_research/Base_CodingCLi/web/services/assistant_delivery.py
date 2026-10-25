"""Deliver durable workflow handoffs and links independently of model phrasing."""
from __future__ import annotations

import re
from urllib.parse import parse_qs, urlsplit, urlunsplit


_URL = re.compile(r'https?://[^\s<>\[\]()"\x27]+', re.IGNORECASE)


def canonical_links(text: str, refs: list[dict]) -> str:
    """Use the verified relative href for a known application object.

    Source URLs with other paths stay external. Unknown placeholder domains
    become plain text rather than a clickable, nonexistent destination.
    """
    known = {}
    native_batches = {ref['id']: ref['href'] for ref in refs if ref.get('type') == 'batch' and ref.get('href', '').startswith('/')}
    for ref in refs:
        href = ref.get('href') or ''
        if href.startswith('/') and not href.startswith('//'):
            parts = urlsplit(href)
            known[(parts.path or '/', parts.query)] = href

    def replace(match: re.Match) -> str:
        raw = match.group(0)
        try:
            parts = urlsplit(raw)
        except ValueError:
            return raw
        href = known.get((parts.path or '/', parts.query))
        if href:
            target = urlsplit(href)
            return urlunsplit(('', '', target.path or '/', target.query, parts.fragment or target.fragment))
        return raw

    result = _URL.sub(replace, text)
    def batch_link(match):
        raw = match.group(0)
        parts = urlsplit(raw)
        key = (parse_qs(parts.query).get('batch') or [''])[0]
        return native_batches.get(key, raw)
    # Native video workflows and application batches have separate identities.
    # Repair only IDs proven by this conversation's native receipts.
    result = re.sub(r'(?:https?://[^/\s<>`()]*)?/ask\?[^\s<>`()]+', batch_link, result)
    # An unverified placeholder cannot be repaired by guessing an application ID.
    result = re.sub(r'(?<!!)\[([^\]\n]+)\]\(<?https?://[^/\s)<>]*\.invalid(?=[:/\s)>]|$)(?:[/:][^\s)<>]*)?>?\)',
                    r'\1（入口未核实，请使用操作卡片）', result, flags=re.IGNORECASE)
    return re.sub(r'https?://[^/\s)<>]*\.invalid(?=[:/\s)>]|$)(?:[/:][^\s)<>]*)?',
                  '（入口未核实，请使用操作卡片）', result, flags=re.IGNORECASE)


class VideoHandoffs:
    """Allow a fresh observation after handoff, then let the workspace poll.

    Only videos successfully delegated by this conversation are affected. Other
    objects, version reads, pagination and failure recovery remain available.
    """

    def __init__(self, refs: list[dict] | None = None) -> None:
        self.pending: dict[str, dict] = {}
        self.observed: set[str] = set()
        # A later "keep checking" request must not restart a polling loop for
        # a workflow already delegated by this conversation.
        for ref in refs or []:
            if ref.get('type') != 'batch':
                continue
            from core.video import autopilot_store
            run = autopilot_store.get_run(ref['id'])
            if not run or run['status'] != 'RUNNING':
                continue
            for item in run['items']:
                script_id = item.get('script_id')
                if script_id and item['state'] == 'WORKING':
                    self.pending[script_id] = {'script_id': script_id, 'batch_id': run['id'],
                                               'href': '/video/' + script_id}

    @staticmethod
    def probe(name: str, arguments: dict) -> str | None:
        if name == 'query_video' and arguments.get('action') == 'get':
            return arguments.get('script_id')
        if (name == 'read_application' and arguments.get('action_id') == 'GET /api/video/scripts/{script_id}'
                and not (arguments.get('query') or {}).get('version') and not arguments.get('result_offset')):
            return (arguments.get('path') or {}).get('script_id')
        return None

    def waiting(self, name: str, arguments: dict) -> bool:
        script_id = self.probe(name, arguments)
        return bool(script_id and script_id in self.pending and script_id in self.observed)

    def record(self, name: str, arguments: dict, result: dict) -> None:
        if name == 'continue_video' and result.get('status') == 'started' and result.get('batch_id'):
            script_id = result['script_id']
            self.pending.setdefault(script_id, {k: result[k] for k in ('script_id', 'batch_id', 'href')})
            return
        script_id = self.probe(name, arguments)
        if script_id not in self.pending:
            return
        data = result.get('result', {}) if name == 'read_application' else result
        if not isinstance(data, dict) or not data.get('status'):
            return
        current = data.get('current_version')
        renders = [r for r in data.get('renders', []) if r.get('version') == current]
        latest = renders[0] if renders else {}
        complete = data.get('status') == 'APPROVED' and latest.get('status') == 'COMPLETED'
        failed = data.get('status') in ('FAILED', 'PLAN_READY') or data.get('error') or (
            latest.get('status') in ('FAILED', 'CANCELLED'))
        if complete or failed:
            self.pending.pop(script_id, None)
            self.observed.discard(script_id)
        else:
            self.observed.add(script_id)

    def response(self, actions: list[dict]) -> dict:
        links = '、'.join(f'[文稿与成片]({item["href"]})' for item in self.pending.values())
        answer = '视频制作已交给 workflow，后台会继续推进，当前进度和版本会在工作区自动更新，无需再次发送“继续”来推进。'
        if links:
            answer += '\n\n查看：' + links + '。'
        if any(not action['ok'] for action in actions):
            answer += '\n\n有操作未成功，请查看操作卡片中的具体结果。'
        return {'answer': answer, 'actions': actions, 'error': None,
                'background_tasks': list(self.pending.values())}
