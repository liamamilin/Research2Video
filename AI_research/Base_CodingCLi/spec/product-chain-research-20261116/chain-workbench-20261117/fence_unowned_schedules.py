"""Make copied, unowned plans visibly paused as well as non-executing."""
from pathlib import Path
import sys,json,yaml
HERE=Path(__file__).resolve().parent;ROOT=HERE/'runtime'
sys.path.insert(0,str(ROOT))
from core.schedule_store import ScheduleStore
owned={m['job'] for f in (ROOT/'config/chains').glob('*.yaml') if (m:=yaml.safe_load(f.read_text())).get('schedule_armed')}
store=ScheduleStore(str(ROOT/'state'),config_dir=str(ROOT/'config'),jobs_dir=str(ROOT/'jobs'));changes=[]
evidence=ROOT/'evidence/unowned-plan-isolation.json'
if evidence.exists():changes=json.loads(evidence.read_text()).get('before',[])
for plan in store.list():
 if plan['enabled'] and plan['job_name'] not in owned:
  changes.append(plan)
  evidence.write_text(json.dumps({'before':changes},ensure_ascii=False,indent=2))
  store.toggle(plan['id'],False)
(HERE/'runtime/evidence/unowned-plan-isolation.json').write_text(json.dumps({'reason':'Copied production plans remain available for explicit activation, but never appear enabled in the isolated UI before ownership.','before':changes},ensure_ascii=False,indent=2))
print('Paused',len(changes),'unowned copied plans; expressions preserved')
