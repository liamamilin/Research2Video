"""Restart only this isolated research app; renderer and active jobs stay alive."""
from pathlib import Path
import os,signal,subprocess,sys,time
HERE=Path(__file__).resolve().parent
pid=int((HERE/'runtime/app.pid').read_text())
command=subprocess.check_output(['ps','-p',str(pid),'-o','command='],text=True).strip()
if str(HERE/'app.py') not in command:raise SystemExit('PID does not belong to this isolated app')
started=time.monotonic();os.kill(pid,signal.SIGTERM)
print('Requested graceful isolated-app restart:',pid,flush=True)
for n in range(1200):
 status=subprocess.run(['ps','-p',str(pid),'-o','stat='],capture_output=True,text=True).stdout.strip()
 if not status or status.startswith('Z'):break
 if n and n%120==0:print('Waiting for existing requests to finish',round(time.monotonic()-started),flush=True)
 time.sleep(.25)
else:raise SystemExit('Still shutting down; no duplicate instance was created')
print('Previous process exited after',round(time.monotonic()-started,2),flush=True)
subprocess.run([sys.executable,str(HERE/'serve.py'),'start'],cwd=HERE,check=True)
print('Restart complete',round(time.monotonic()-started,2),flush=True)
