"""Run local music on the existing single worker, then register validated assets."""
from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import subprocess
import threading
import time

from g2.music_engine import GenerationError, digest, model_root, readiness
from g2.music_engine.runtime import file_hash
from g3 import music_assets
from g3.music_jobs import MusicJobs

SCRIPT = Path(__file__).resolve().parents[1] / 'g2/music_engine/generate.py'


def stop(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=5)
    except ProcessLookupError:
        pass
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL); process.wait(timeout=5)


class MusicGeneration:
    def __init__(self, settings):
        self.settings = settings
        self.jobs = MusicJobs(settings.data_dir)

    def recover(self) -> None:
        """Called after acquiring the single-worker lock; never silently regenerate."""
        for row in self.jobs.running():
            pid = row['runner_pid']
            if isinstance(pid, int) and pid > 1:
                try:
                    command = subprocess.check_output(['ps','-p',str(pid),'-o','command='],text=True,timeout=2)
                    if str(SCRIPT) in command and str(self.jobs.work(row['id']) / 'request.json') in command and os.getpgid(pid)==pid:
                        os.killpg(pid,signal.SIGTERM)
                except (OSError,subprocess.SubprocessError):
                    pass
            try:
                meta, _ = music_assets.load(self.settings.data_dir,row['id'])
                if meta.get('generation_job_id') != row['id']:
                    raise ValueError('Unrelated asset')
                self.jobs.update(row['id'],status='COMPLETED',stage='COMPLETED',asset_id=row['id'],runner_pid=None)
            except ValueError:
                self.jobs.update(row['id'],status='FAILED',stage='INTERRUPTED',runner_pid=None,
                                 error_code='MUSIC_INTERRUPTED',error_message='音乐制作被中断，原输出已保留；可手动重新生成')

    def run_one(self, stop_event: threading.Event) -> bool:
        job = self.jobs.claim()
        if job is None:
            return False
        try:
            self.execute(job,stop_event)
        except Exception as exc:
            self.jobs.update(job['id'],status='FAILED',runner_pid=None,
                             error_code=getattr(exc,'code','MUSIC_GENERATION_FAILED'),error_message=str(exc)[-400:])
        return True

    def execute(self, job: dict, stop_event: threading.Event) -> None:
        identity = job['id']; work = self.jobs.work(identity); work.mkdir()
        request_file = work / 'request.json'
        request_file.write_text(json.dumps(job['settings'],ensure_ascii=False,indent=2))
        if not readiness()['ready']:
            raise GenerationError('MUSIC_NOT_READY',readiness()['message'])
        env = {**os.environ,'PYTHONDONTWRITEBYTECODE':'1','AUTOVIDEO_MUSIC_MODEL_ROOT':str(model_root())}
        with (work / 'generation.log').open('w') as log:
            process = subprocess.Popen([str(model_root() / 'venv/bin/python'),'-u','-B',str(SCRIPT),str(request_file)],
                cwd=self.settings.project_root,env=env,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
            self.jobs.update(identity,runner_pid=process.pid)
            started, stage = time.monotonic(), ''
            try:
                while process.poll() is None:
                    state = self.jobs.get(identity)
                    if stop_event.is_set() or state['cancel_requested']:
                        stop(process)
                        self.jobs.update(identity,status='CANCELLED' if state['cancel_requested'] else 'FAILED',
                            stage='CANCELLED' if state['cancel_requested'] else 'INTERRUPTED',runner_pid=None,
                            error_code=None if state['cancel_requested'] else 'MUSIC_INTERRUPTED',
                            error_message=None if state['cancel_requested'] else '服务已停止，原输出已保留')
                        return
                    if time.monotonic()-started > 900:
                        stop(process); raise GenerationError('MUSIC_TIMEOUT','音乐生成超过 15 分钟，已停止并保留日志')
                    # The log is bounded by one 30-second inference. Only stage markers become UI status.
                    markers = [line[12:] for line in (work/'generation.log').read_text(errors='replace').splitlines() if line.startswith('MUSIC_STAGE ')]
                    if markers and markers[-1] != stage:
                        stage=markers[-1]; self.jobs.update(identity,stage=stage)
                    stop_event.wait(.3)
            finally:
                stop(process)
        self.jobs.update(identity,runner_pid=None)
        if process.returncode:
            errors = [line[12:] for line in (work/'generation.log').read_text(errors='replace').splitlines() if line.startswith('MUSIC_ERROR ')]
            failure = json.loads(errors[-1]) if errors else {'code':'MUSIC_GENERATION_FAILED','message':'音乐生成失败，请查看本机任务日志'}
            raise GenerationError(failure['code'],failure['message'])
        if self.jobs.get(identity)['cancel_requested']:
            self.jobs.update(identity,status='CANCELLED',stage='CANCELLED'); return
        self.publish(job,work)

    def publish(self, job: dict, work: Path) -> None:
        if not self.jobs.start_publish(job['id']):
            return
        report = json.loads((work/'result.json').read_text())
        path = work / report['file']
        if (work/'output').is_symlink() or path.is_symlink() or not path.resolve().is_relative_to((work/'output').resolve()) or report.get('status')!='PASS' or file_hash(path)!=report.get('sha256'):
            raise GenerationError('MUSIC_OUTPUT_INVALID','生成音乐未通过文件完整性校验')
        if path.stat().st_size > music_assets.MAX_BYTES:
            raise GenerationError('MUSIC_OUTPUT_INVALID','生成音乐文件超过本阶段大小上限')
        raw = path.read_bytes()
        # Full decode and all normal import checks run before the asset becomes visible.
        extra = {'kind':'generated','name':job['settings']['name'],'source':'ACE-Step 1.5 本机 AI 生成的无词配乐；听感待试听确认。',
                 'generation_job_id':job['id'],'generation':{**report,'request_sha256':digest(job['settings']),
                 'request':job['settings']},'topics':job['settings'].get('topics',[])}
        meta = music_assets._save(self.settings.data_dir,job['id'],raw,job['settings']['name']+'.wav',extra,
                                  expected_duration=job['settings']['duration_seconds'])
        self.jobs.update(job['id'],status='COMPLETED',stage='COMPLETED',asset_id=meta['id'])
