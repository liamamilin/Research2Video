import app as module
from validation_env import setup
setup(module,copy_video=True)
from web.services import chains,chain_runner,video_executor
from core.video import store,autopilot_store as ap
import json
sid='4fd059213d4643cf9ea0d39b00e72687'
script=store.get_script(sid);assert script['status']=='APPROVED'
batch=ap.get_run('7c45b81dd54147edb9e5d9258fd2ec6c')
item=next(i for i in batch['items'] if i['script_id']==sid)
ap.update_item(item['id'],state='HELD',retries={},note='出片失败 2 次：测试历史失败恢复')
row={'id':'recovery-test','chain_id':'research_updates','job':'chains/research_updates','outputs':['research_updates_daily'],'status':'HELD','stage':'VIDEO','batch_id':batch['id'],'attempt':1}
chains.save_run(row)
before=video_executor._render_auto(script,False,{},{});assert 'hold' in before
chain_runner.retry(row['id'],{'username':'test','role':'admin'})
item=next(i for i in ap.get_run(batch['id'])['items'] if i['id']==item['id'])
after=video_executor._render_auto(script,False,{},item['retries']);assert 'hold' not in after,(after,item)
(module.HERE/'failure-recovery-tests.json').write_text(json.dumps({'before':before,'after':after,'retries':item['retries'],'existing_script':sid},ensure_ascii=False,indent=2))
print('保留原报告、原文稿和失败记录；重试后历史失败不再阻止新出片')
