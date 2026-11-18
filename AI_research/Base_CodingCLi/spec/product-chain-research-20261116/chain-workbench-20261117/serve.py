"""Launch the persistent isolated workbench without touching production services."""
from pathlib import Path
import argparse,subprocess,socket,sys,time,os,fcntl
from storage_layout import locations
HERE=Path(__file__).resolve().parent
SERVICES=[('renderer',9253,'renderer_app.py'),('app',9252,'app.py')]
def listening(port):
    try:
        with socket.create_connection(('127.0.0.1',port),timeout=1):return True
    except OSError:return False
def alive(pid):
    try:os.kill(pid,0);return True
    except (OSError,ValueError):return False
parser=argparse.ArgumentParser();parser.add_argument('action',choices=('start','status'),nargs='?',default='start');args=parser.parse_args()
locations()
with (HERE/'launcher.lock').open('w') as lock:
    fcntl.flock(lock,fcntl.LOCK_EX)
    for name,port,script in SERVICES:
        if listening(port):print(name,'正在运行',port,flush=True);continue
        if args.action=='status':print(name,'未就绪',port,flush=True);continue
        pid_file=HERE/'runtime'/f'{name}.pid'
        pid=int(pid_file.read_text()) if pid_file.exists() else 0
        process=None
        if pid and alive(pid):print(name,'正在初始化，等待已有实例',flush=True)
        else:
            with (HERE/(name+'-server.log')).open('a') as log:
                process=subprocess.Popen([sys.executable,str(HERE/script)],cwd=HERE,stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
            pid=process.pid;pid_file.write_text(str(pid))
        for _ in range(600):
            if listening(port):break
            if process and process.poll() is not None or not alive(pid):raise SystemExit(f'{name} 启动失败，请查看 {name}-server.log')
            time.sleep(.2)
        else:raise SystemExit(f'{name} 仍在初始化，请查看日志；再次 start 会等待同一实例')
        print(name,'已启动',port,flush=True)
print('http://chain-workbench.localhost:9252/chains',flush=True)
