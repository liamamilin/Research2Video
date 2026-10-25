"""Hand a durable application batch to the worker without model polling."""
class BatchHandoffs:
    def __init__(self, refs=()):
        self.pending = {r['id'] for r in refs if r.get('type') == 'application_batch'}
        self.observed = set()

    def waiting(self, name, args):
        return name == 'query_application_batch' and args.get('id') in self.observed & self.pending

    def record(self, name, args, result):
        batch = result.get('batch') or {}
        if batch.get('active'):
            self.pending.add(batch['id'])
            if name == 'query_application_batch':
                self.observed.add(batch['id'])
        elif batch.get('id'):
            self.pending.discard(batch['id'])

    def response(self, actions):
        return {'answer': '批次已交给后台，研究、制作或投稿会按逐项进度继续；可以在[任务中心](/ask?tasks=1)查看结果。',
                'actions': actions, 'error': None}
