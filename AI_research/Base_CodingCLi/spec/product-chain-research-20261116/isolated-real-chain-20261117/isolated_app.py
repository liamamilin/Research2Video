"""Unmodified business routes against copied data; external execution is disabled."""
import os
from pathlib import Path
import sys
import json
from contextlib import asynccontextmanager

HERE = Path(__file__).resolve().parent
ROOT = HERE / 'runtime'
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))
sys.dont_write_bytecode = True
for key in list(os.environ):
    if any(word in key.upper() for word in ('API_KEY','SECRET_KEY','ACCESS_TOKEN')):
        os.environ.pop(key, None)
os.environ.update(AI_RESEARCH_WEB_CONFIG=str(ROOT/'config/web.yaml'), AREC_NO_AUTOPILOT_LOOP='1',
                  AI_RESEARCH_LOG_FILE=str(ROOT/'logs/isolation.log'), AI_RESEARCH_ENV='development')
BOUNDARIES = []

def under(path):
    if isinstance(path, int) or path is None:
        return True
    try:
        return Path(os.fsdecode(path)).resolve().is_relative_to(HERE)
    except (TypeError, ValueError):
        return False

def guard(event, args):
    if event == 'open':
        path, mode, flags = args
        if (isinstance(mode,str) and any(x in mode for x in 'wax+')) or (isinstance(flags,int) and flags & (os.O_WRONLY|os.O_RDWR|os.O_CREAT|os.O_TRUNC)):
            if not under(path) and str(path) != '/dev/null':
                raise PermissionError('Isolation denied a write outside the snapshot')
    if event == 'sqlite3.connect' and str(args[0]) != ':memory:' and not under(args[0]):
        raise PermissionError('Isolation denied a database outside the snapshot')
    if event in ('os.remove','os.rmdir','os.mkdir','os.chmod','os.truncate') and not under(args[0]):
        raise PermissionError('Isolation denied a filesystem change outside the snapshot')
    if event in ('os.rename','os.link','os.symlink') and not all(under(x) for x in args[:2]):
        raise PermissionError('Isolation denied an external filesystem link/change')
    if event in ('subprocess.Popen','os.system','os.posix_spawn','os.fork','socket.connect','socket.getaddrinfo'):
        BOUNDARIES.append({'event':event,'blocked':True})
        raise PermissionError('Isolation blocks external execution and outbound networking')

sys.addaudithook(guard)
from web.settings import get_settings
from core import state, report_meta, lock
from core.video import store
from web.indexer import db as index_db
from web.auth import db as users
from web.auth.password import hash_password
from web.services import managed_scheduler, scheduler, launchd_agents
from core import schedule_host
state.use_state_dir(str(ROOT/'state'))
report_meta.META_FILE = str(ROOT/'state/report_meta.jsonl')
lock._LOCK_DIR = str(ROOT/'state/locks')
store.use_state_dir(str(ROOT/'state'))
index_db._DB_PATH_OVERRIDE = str(ROOT/'state/reports.db')
users._DB_PATH_OVERRIDE = str(ROOT/'state/users.db')
users.init_db()
for role in ('admin','editor','viewer'):
    if not users.get_user_by_username('isolation-'+role):
        users.create_user('isolation-'+role,hash_password('local-isolation-'+role+'-2026'),role)
store.init_db()
def no_dispatcher():
    BOUNDARIES.append({'event':'dispatcher-install','replaced':'offline snapshot only'})
    return {'online':False,'isolated':True}
managed_scheduler.ensure_online = no_dispatcher
schedule_host.install = no_dispatcher
schedule_host.status = lambda *_a,**_k: {'online':False,'installed':False,'detail':'隔离环境未安装执行器'}
scheduler._get_crontab = lambda: []
launchd_agents.agent_dir = lambda: ROOT/'no-launchd-agents'

from web.server import create_app
from fastapi.responses import JSONResponse, HTMLResponse
@asynccontextmanager
async def inert_lifespan(app):
    yield
app = create_app()
app.router.lifespan_context = inert_lifespan

@app.middleware('http')
async def isolate(request, call_next):
    path = request.url.path
    if path == '/isolation':
        return HTMLResponse((HERE/'inspection.html').read_text())
    if path == '/api/isolation/results':
        report = HERE/'results.json'
        return JSONResponse(json.loads(report.read_text()) if report.exists() else {'status':'检查进行中'})
    if request.method not in ('GET','HEAD','OPTIONS'):
        prefixes = ('/api/auth/','/api/matrices','/api/sources','/api/production','/api/content-columns','/api/scheduler','/api/jobs','/api/video/scripts/','/api/video/recipes')
        forbidden = ('/run','/cancel','/write','/render','/restyle','/rerun','/recheck','/approve','/confirm','/redo','/dispatcher','/publications')
        if not path.startswith(prefixes) or any(x in path for x in forbidden):
            return JSONResponse({'error':'隔离检查停在外部执行边界；未提交真实生成或发布任务'},status_code=409)
    response = await call_next(request)
    response.headers['X-Chain-Isolation'] = 'copied-data-no-external-workers'
    return response

if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app,host='127.0.0.1',port=9251,access_log=False)
