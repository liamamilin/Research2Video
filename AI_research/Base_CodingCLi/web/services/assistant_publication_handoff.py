"""Stop repeated model polling once the publishing workers own a submission."""
from urllib.parse import quote


class PublicationHandoffs:
    def __init__(self, refs=()):
        self.pending = {}
        for ref in refs:
            if ref.get('type') == 'upload' and ':' in ref.get('id', ''):
                platform, key = ref['id'].split(':', 1)
                self.pending[(platform, key)] = {'platform': platform, 'id': key}
        self.observed = set()

    @staticmethod
    def probe(name, args):
        if name == 'query_publishing' and args.get('action') == 'task':
            return (args.get('platform', 'bilibili'), args.get('task_id'))
        return None

    def waiting(self, name, args):
        target = self.probe(name, args)
        return target in self.pending and target in self.observed

    def record(self, name, args, result):
        if name == 'manage_publishing':
            for target in result.get('publication_tasks', []):
                self.pending[(target['platform'], target['id'])] = target
        target = self.probe(name, args)
        if target in self.pending:
            status = (result.get('task') or {}).get('status')
            if status in ('QUEUED', 'RUNNING', 'UPLOADING', 'PUBLISHING', 'VERIFYING', 'PROCESSING', 'SUBMITTED') or bool(
                    ((result.get('task') or {}).get('verification') or {}).get('automatic')):
                self.observed.add(target)
            else:
                self.pending.pop(target, None)

    def response(self, actions):
        platforms = sorted({key[0] for key in self.pending})
        links = '、'.join(f'[{p} 发布进度](/platforms?platform={quote(p)}&tab=uploads)' for p in platforms)
        return {'answer': '投稿已交给平台发布队列，当前尚未确认公开发布。工作区会自动跟踪每条任务；需要处理时可打开对应投稿。\n\n' + links,
                'actions': actions, 'error': None}
