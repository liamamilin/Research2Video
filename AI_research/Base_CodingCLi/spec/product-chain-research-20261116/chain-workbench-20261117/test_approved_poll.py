from pathlib import Path
import sys
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parent/'runtime'))
from web.services import video_autopilot as ap
item={'id':'i','state':'WORKING','script_id':'s','gate':{'story_selection':{'selected':'原选题','skipped':2}},'retries':{}}
script={'id':'s','status':'APPROVED','topic':{},'archived':False}
out={'took':{'script':False,'render':False},'item':None,'hold':None,'bump':None}
with patch.object(ap.store,'get_script',return_value=script),patch.object(ap.story_pool,'selection',side_effect=AssertionError('approved polling must not scan selection history')),patch.object(ap.video_executor,'advance_auto',return_value=out),patch.object(ap.ap,'update_item') as update:
 assert ap.advance(item,can_start_script=False,can_start_render=False)==out['took'];update.assert_not_called()
with patch.object(ap.store,'get_script',return_value={**script,'status':'WRITING'}),patch.object(ap.story_pool,'selection',return_value={'selected':'新的主事件','skipped':0}) as selection,patch.object(ap.video_executor,'advance_auto',return_value=out),patch.object(ap.ap,'update_item') as update:
 ap.advance(item,can_start_script=False,can_start_render=False);selection.assert_called_once();assert update.called
with patch.object(ap.store,'get_script',return_value={**script,'archived':True}),patch.object(ap.story_pool,'selection',return_value={}) as selection,patch.object(ap.ap,'update_item') as update:
 ap.advance(item,can_start_script=False,can_start_render=False);selection.assert_called_once();assert update.call_args.kwargs['state']=='CANCELLED'
print('PASS: approved video polling preserves frozen selection without rescanning; writing and archival still read current decisions')
