from pathlib import Path
import sys,sqlite3
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parent/'runtime'))
from web.services import chain_runner as runner
for status in ('RUNNING','CANCELLING'):
    row={'id':'isolated-busy','status':status,'note':'原任务继续'}
    with patch.object(runner,'advance',side_effect=sqlite3.OperationalError('database is locked')),patch.object(runner,'put') as put:
        runner.advance_safely(row)
        assert row['status']==status and row['note']=='原任务继续'
        put.assert_not_called()
row={'id':'isolated-bad-schema','status':'RUNNING'}
with patch.object(runner,'advance',side_effect=sqlite3.OperationalError('no such table')),patch.object(runner,'put') as put:
    runner.advance_safely(row)
    assert row['status']=='FAILED' and 'no such table' in row['note']
    put.assert_called_once_with(row)
print('PASS: transient database contention keeps chain running/cancelling; non-transient errors remain visible')
