from pathlib import Path
import sys
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parent/'runtime'))
from web.services import video_executor as executor
from core.video import content_guard
script={'id':'s','status':'APPROVED','current_version':2,'approved_version':2}
sentinel={'followed':True}
for state,capacity in [('RUNNING',True),('COMPLETED',True),('FAILED',False)]:
    rows=[{'id':'r','version':2,'status':state}]
    with patch.object(executor,'column_of',return_value=object()),patch.object(executor.store,'list_renders',return_value=rows),patch.object(executor,'_render_auto',return_value=sentinel) as follow,patch.object(content_guard,'with_policy',side_effect=AssertionError('Unnecessary archive scan')):
        assert executor.advance_auto(script,{},can_start_script=False,can_start_render=capacity)==sentinel
        follow.assert_called_once()
with patch.object(executor,'column_of',return_value=object()),patch.object(executor.store,'list_renders',return_value=[{'version':2,'status':'FAILED'}]),patch.object(executor,'get_settings',return_value=type('S',(),{'paths':type('P',(),{'jobs_dir':'unused'})()})()),patch.object(content_guard,'with_policy',side_effect=RuntimeError('new attempt must retain existing check')):
    try:executor.advance_auto(script,{},can_start_script=False,can_start_render=True)
    except RuntimeError as exc:assert str(exc)=='new attempt must retain existing check'
    else:raise AssertionError('new render skipped existing check')
print('PASS: progress polling and capacity waiting skip repeated archive scans; new attempt retains existing check')
