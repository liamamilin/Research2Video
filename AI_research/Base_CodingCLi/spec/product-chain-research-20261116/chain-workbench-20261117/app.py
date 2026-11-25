"""Start the isolated, functional chain workbench on port 9252."""
from pathlib import Path
from contextlib import asynccontextmanager
import os,sys,json,secrets,logging,sqlite3,faulthandler,signal
import yaml
HERE=Path(__file__).resolve().parent
ROOT=HERE/'runtime'
SOURCE=HERE.parents[2]
os.chdir(ROOT);sys.path.insert(0,str(ROOT));sys.dont_write_bytecode=True
os.environ['TMPDIR']=str(ROOT/'tmp');os.environ['PYTHONDONTWRITEBYTECODE']='1'
os.environ['AREC_NO_AUTOPILOT_LOOP']='1'
os.environ['AI_RESEARCH_WEB_CONFIG']=str(ROOT/'config/web.yaml')
os.environ['AI_RESEARCH_LOG_FILE']=str(ROOT/'logs/workbench.log')
from dotenv import dotenv_values
for k,v in dotenv_values(SOURCE/'.env').items():
    if v is not None:os.environ[k]=v
os.environ['AI_RESEARCH_WEB_CONFIG']=str(ROOT/'config/web.yaml')
web=yaml.safe_load((ROOT/'config/web.yaml').read_text())
web['paths']={n+'_dir':str(ROOT/n) for n in ('jobs','config','output','state','logs')}
web['gateway']={'enabled':False};web['server']={'host':'127.0.0.1','port':9252}
(ROOT/'config/web.yaml').write_text(yaml.safe_dump(web))
system=yaml.safe_load((ROOT/'config/system.yaml').read_text())
system.setdefault('video',{})['autovideo_base_url']='http://127.0.0.1:9253'
system['video']['require_mounts']=[]
system['video']['storage']={'dir':str(ROOT/'state')}
system['video'].setdefault('archive',{})['autovideo_data']=str(ROOT/'renderer/g3/data')
system['video']['archive']['enabled']=False
system.setdefault('logging',{})['file']=str(ROOT/'logs/workbench.log')
(ROOT/'config/system.yaml').write_text(yaml.safe_dump(system,allow_unicode=True))

STORAGE_ROOTS=[(ROOT/n).resolve() for n in ('state','output','logs','tmp','cache')]+[(ROOT/'renderer/g3/data').resolve()]

def inside(p):
    if isinstance(p,int) or p is None:return True
    try:
        target=Path(os.fsdecode(p)).resolve()
        return target.is_relative_to(HERE) or any(target.is_relative_to(root) for root in STORAGE_ROOTS)
    except (TypeError,ValueError):return False

def guard(event,args):
    if event=='open':
        p,mode,flags=args
        writing=(isinstance(mode,str) and any(c in mode for c in 'wax+')) or (isinstance(flags,int) and flags & (os.O_WRONLY|os.O_RDWR|os.O_CREAT|os.O_TRUNC))
        if writing and not inside(p) and str(p)!='/dev/null':raise PermissionError('隔离写入拒绝：'+str(p))
    if event=='sqlite3.connect' and str(args[0])!=':memory:' and not inside(args[0]):raise PermissionError('数据库必须位于隔离目录')
    if event in ('os.remove','os.rmdir','os.mkdir','os.chmod','os.truncate') and not inside(args[0]):raise PermissionError('隔离目录之外不能修改')
    if event in ('os.rename','os.link','os.symlink') and not all(inside(x) for x in args[:2]):raise PermissionError('隔离目录之外不能建立或改写路径')
    if event=='socket.connect':
        address=args[1]
        if isinstance(address,tuple) and address[0] in ('127.0.0.1','localhost','::1') and address[1] in (8011,8765,8766,8770):
            raise PermissionError('禁止连接正式应用执行端口')
    if event=='subprocess.Popen':
        argv=args[1]
        if isinstance(argv,(list,tuple)) and argv and 'radar' in str(argv[0]) and all(str(x) not in ('delete','update','install') for x in argv):return
        raise PermissionError('隔离研究服务不启动外部进程；视频由独立渲染服务处理')
sys.addaudithook(guard)
faulthandler.register(signal.SIGUSR1,all_threads=True)
# Retain idle connections so short requests do not checkpoint/unlink WAL files
# after every operation on the external trial volume. No transaction is held.
DB_ANCHOR_PATHS={ROOT/"state"/name for name in ("video.db","users.db","reports.db","schedules.db","chains.db","platforms.db","douyin.db")}
DB_ANCHOR_PATHS.update((ROOT/"state/platforms").glob("*/queue.db"))
DB_ANCHORS=[sqlite3.connect(str(path),check_same_thread=False) for path in sorted(DB_ANCHOR_PATHS)]
for connection in DB_ANCHORS:
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("SELECT name FROM sqlite_master LIMIT 1").fetchall()
from core import state,report_meta,lock,activity
from core.video import store
from web.auth import db as users
from web.indexer import db as index_db
from web.services import managed_scheduler,scheduler,launchd_agents
from core import schedule_host
state.use_state_dir(str(ROOT/'state'));activity.use_state_dir(str(ROOT/'state'))
report_meta.META_FILE=str(ROOT/'state/report_meta.jsonl');lock._LOCK_DIR=str(ROOT/'state/locks')
store.use_state_dir(str(ROOT/'state'));store.init_db()
users._DB_PATH_OVERRIDE=str(ROOT/'state/users.db');index_db._DB_PATH_OVERRIDE=str(ROOT/'state/reports.db')
from core.schedule_store import ScheduleStore
managed_scheduler.ensure_online=lambda:ScheduleStore(str(ROOT/'state')).health()
schedule_host.install=managed_scheduler.ensure_online
schedule_host.status=lambda *_a,**_k:{**managed_scheduler.ensure_online(),'installed':False,'detail':'隔离应用内链路执行器；不安装系统调度'}
scheduler._get_crontab=lambda:[];launchd_agents.agent_dir=lambda:ROOT/'no-launchd-agents'
scheduler.all_jobs=lambda:[]
from web.routes import scheduler as scheduler_routes
from web.services import schedule_overview
scheduler_routes._matrix_trigger_warnings=lambda *_args:[]
launchd_agents.list_deleted=lambda *_args:[]
schedule_overview.rows=lambda:[]
logging.basicConfig(level=logging.INFO,format='%(asctime)s %(levelname)s %(name)s %(message)s',handlers=[logging.FileHandler(ROOT/'logs/workbench.log')],force=True)
logging.getLogger('httpx').setLevel(logging.WARNING)
from web.server import create_app
from web.routes.chains import router
from web.services import chain_runner
app=create_app()
# Put API routes before the SPA catch-all.
app.router.routes[:0]=list(router.routes)
from fastapi.responses import JSONResponse
@app.middleware('http')
async def isolation(request,call_next):
    if request.method not in ('GET','HEAD','OPTIONS') and any(p in request.url.path for p in ('/publications','/publish','/social-publishing','/deploy','/gateway')):
        return JSONResponse({'error':'隔离应用只生成并管理成片，不向真实平台发布'},status_code=409)
    response=await call_next(request)
    response.headers['X-Chain-Environment']='isolated-functional-workbench'
    return response
@asynccontextmanager
async def lifespan(app):
    chain_runner.start_loop()
    import threading
    def reconcile_reports():
        try:
            from web.services.chain_reports import reconcile
            reconcile()
        except Exception:logging.exception("Report association recovery failed")
    threading.Thread(target=reconcile_reports,daemon=True,name="chain-report-index").start()
    yield
    chain_runner.STOP.set()
    for connection in DB_ANCHORS:connection.close()
app.router.lifespan_context=lifespan
if __name__=='__main__':
    import uvicorn
    uvicorn.run(app,host='127.0.0.1',port=9252,access_log=False)
