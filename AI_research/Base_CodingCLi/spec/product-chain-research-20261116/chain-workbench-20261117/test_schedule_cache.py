from pathlib import Path
import sys,tempfile,json
from unittest.mock import patch
root=Path(__file__).resolve().parent;sys.path.insert(0,str(root/'runtime'))
from core.schedule_store import ScheduleStore
folder=Path(tempfile.mkdtemp(prefix='schedule-init-',dir=root/'runtime/tmp'))
s=ScheduleStore(str(folder))
with patch.object(ScheduleStore,'_initialize_schema',side_effect=AssertionError('read path repeated schema migration')):
 for _ in range(4):assert ScheduleStore(str(folder)).list()==[]
old=folder/'schedules.db';old.rename(folder/'previous.db')
assert ScheduleStore(str(folder)).list()==[]
(root/'schedule-cache-tests.json').write_text(json.dumps({'repeated_construction_without_migration':4,'recreated_database_initialized':True},indent=2))
print('Repeated reads do not migrate; a replaced database initializes normally')
