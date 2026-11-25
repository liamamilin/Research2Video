"""Exercise real HTTP routes and persistence against the copied live chain."""
import json
import sqlite3
import yaml
from isolated_app import app, HERE, ROOT, BOUNDARIES, guard
from fastapi.testclient import TestClient

trace=[]
findings=[]
checks=[]
JOB='monitoring/ai_flash_discovery'
initial={str(p.relative_to(ROOT)):p.read_bytes() for folder in ('config','jobs') for p in (ROOT/folder).rglob('*') if p.is_file()}
recovery=HERE/'runtime'/'recovery'
recovery.mkdir(exist_ok=True)
for name in ('video.db','schedules.db'):
    with sqlite3.connect(ROOT/'state'/name) as src,sqlite3.connect(recovery/name) as dst: src.backup(dst)

def client(role='admin'):
    c=TestClient(app)
    r=c.post('/api/auth/login',json={'username':'isolation-'+role,'password':'local-isolation-'+role+'-2026'})
    assert r.status_code==200
    c.headers['x-csrf-token']=c.cookies.get('ai_research_csrf','')
    return c
c=client()
def call(label,method,path,body=None,actor=None):
    r=(actor or c).request(method,path,json=body) if body is not None else (actor or c).request(method,path)
    try: data=r.json()
    except ValueError: data={'text':r.text[:1000]}
    trace.append({'label':label,'method':method,'path':path,'status':r.status_code,'request':body,'response':data})
    print(label,r.status_code,flush=True)
    return r.status_code,data
def check(name,ok,detail=None):
    checks.append({'name':name,'passed':bool(ok),'detail':detail})
def finding(key,title,evidence,impact):
    findings.append({'id':key,'title':title,'evidence':evidence,'impact':impact})
def get(path):
    code,data=call('读取 '+path,'GET',path)
    assert code==200,(path,code,data)
    return data
def counts():
    with sqlite3.connect(ROOT/'state/video.db') as db:
        return {'scripts':db.execute("select count(*) from scripts where production in ('flash_daily','flash_each')").fetchone()[0],
                'renders':db.execute("select count(*) from renders where script_id in (select id from scripts where production in ('flash_daily','flash_each'))").fetchone()[0]}

summary={}
completed=False
try:
    production=get('/api/production?job='+JOB)
    matrix=get('/api/matrices/ai_media?reports=false')
    source=get('/api/sources/cg_openai_news')
    job=get('/api/jobs/'+JOB)
    schedules=get('/api/scheduler?scope=managed')
    plan=next(x for x in schedules['jobs'] if x['job_name']==JOB)
    baseline=get('/api/production/flash_daily/settings')
    summary={'job':JOB,'productions':[{k:x.get(k) for k in ('key','name','auto','split','content_mode')} for x in production],
             'matrix':matrix['name'],'schedule':{k:plan[k] for k in ('id','schedule','version','enabled','timezone')},'history':counts(),
             'effective_render':baseline['effective_render'],'source_usages':source['used_by']}
    check('真实链路七个生产引用',len(production)==7)
    check('真实合集依赖栏目身份',baseline['content_mode']=='episode' and baseline['split']=='whole')

    code,data=call('原型全局停用字段','PATCH','/api/sources/cg_openai_news',{'expected_revision':source['revision'],'fields':{'enabled':False}})
    check('来源 enabled 字段被真实接口拒绝',code==422)
    finding('S1','原型的全局来源停用目前没有真实接口',{'status':code,'allowed_fields':['name','spec','tags','note']},'不能把原型开关直接上线，需要确定来源停用模型及其对直接声明、来源组、覆盖、发现的作用。')
    code,changed=call('真实来源名称修改','PATCH','/api/sources/cg_openai_news',{'expected_revision':source['revision'],'fields':{'name':source['name']+' · 隔离检查'}})
    check('真实来源保存',code==200)
    code,_=call('来源过期版本冲突','PATCH','/api/sources/cg_openai_news',{'expected_revision':source['revision'],'fields':{'name':'不应覆盖'}})
    check('来源冲突保护',code==409)
    code,_=call('删除被直接引用的来源','DELETE','/api/sources/cg_openai_news?expected_revision='+changed['revision'])
    check('真实来源引用保护',code==409)

    code,data=call('修改真实周期','PUT','/api/scheduler/'+plan['id'],{'schedule':'30 7,19 * * *','job_name':JOB,'timezone':'Asia/Shanghai','missed_policy':'skip','max_retries':2,'expected_version':plan['version']})
    check('周期保存真实SQLite',code==200)
    current=get('/api/scheduler/'+plan['id'])
    check('周期重新读取生效',current.get('schedule')=='30 7,19 * * *')
    code,_=call('周期过期版本冲突','PUT','/api/scheduler/'+plan['id'],{'schedule':'30 7,20 * * *','job_name':JOB,'expected_version':plan['version']})
    check('调度版本冲突',code==409)
    for key in ('flash_daily','flash_each'):call('暂停输出 '+key,'PATCH','/api/production/'+key,{'auto':0})
    check('输出暂停不暂停共享研究',get('/api/scheduler/'+plan['id'])['enabled'])
    call('停用共享研究','PUT','/api/scheduler/'+plan['id']+'/toggle',{'enabled':False})
    check('共享计划真实停用',not get('/api/scheduler/'+plan['id'])['enabled'])

    code,_=call('修改矩阵默认速度','PUT','/api/matrices/ai_media',{'name':matrix['name'],'domain':matrix['domain'],'keywords':matrix['keywords'],'render':{'video_speed':1.5}})
    after=get('/api/production/flash_daily/settings')
    check('矩阵默认进入实际输出配置',code==200 and after['effective_render'].get('video_speed')==1.5)
    code,_=call('单输出速度覆盖','PATCH','/api/production/flash_each/settings',{'expected_revision':get('/api/production/flash_each/settings')['revision'],'render':{'video_speed':1.0}})
    check('输出覆盖和另一输出隔离',code==200 and get('/api/production/flash_daily/settings')['effective_render'].get('video_speed')==1.5)

    # A real existing script; retain its content and change only the title in the copied DB.
    with sqlite3.connect(ROOT/'state/video.db') as db:
        sid=db.execute("select id from scripts where production='flash_each' and status in ('DRAFT','APPROVED') order by updated_at desc limit 1").fetchone()[0]
    script=get('/api/video/scripts/'+sid)
    (HERE/'script-shape.json').write_text(json.dumps(script,ensure_ascii=False,indent=2))
    version=script.get('version') or script.get('current') or {}
    if not isinstance(version,dict):version={}
    if not version:
        from core.video import store
        version=store.get_version(sid,script['current_version'])
    before_count=counts()
    code,edited=call('编辑真实讲稿副本','PUT','/api/video/scripts/'+sid,{'title':version['title'][:64]+'（隔离检查）','sections':version['sections'],'base_version':version['version']})
    check('讲稿修改保留成片数量',code==200 and counts()['renders']==before_count['renders'])
    summary['edited_script']={'id':sid,'before_version':version['version'],'status':code,'after_version':edited.get('current_version')}

    # New independent science chain, assembled through existing product APIs.
    code,newjob=call('创建科研Job及周期','POST','/api/jobs',{'name':'isolation_science','display_name':'隔离科研链路','category':'research','prompt':'基于订阅材料说明 AI 科学发现与研究方法。','sources':['source:cg_openai_news'],'output':'output/isolation_science/{date}.md','recurrence':{'schedule':'0 10 * * *','timezone':'Asia/Shanghai','missed_policy':'skip','max_retries':2}})
    check('真实新Job加计划创建',code==201)
    nj='research/isolation_science'
    call('新链矩阵归属','POST','/api/matrices',{'key':'isolation_science','name':'隔离科研矩阵','jobs':[nj],'render':{'video_speed':1.25},'content':{'name':'科研演练','role':'domain','status':'configured'}})
    code,p1=call('创建科研第一输出','POST','/api/production',{'job':nj,'recipe':'flash','reverify':False,'auto':1,'name':'科研单条'})
    if code!=201:
        finding('N1','基础Job创建未完成可制作的选题契约',{'response':p1},'需要创建向导显式建立 topics/source 等业务配置，不能只保存名称、来源、时间。')
        raw=get('/api/jobs/'+nj); content=yaml.safe_load(raw['yaml_content']);content['topics']={'alias':'isolation_science','format':'flash','prefix':'SCI','refresh':True}
        call('补齐选题契约','PUT','/api/jobs/'+nj,{'yaml_content':yaml.safe_dump(content,allow_unicode=True),'expected_mtime':raw['yaml_mtime']})
        code,p1=call('补契约后创建单条输出','POST','/api/production',{'job':nj,'recipe':'flash','reverify':False,'auto':1,'name':'科研单条'})
    if code!=201:
        finding('N3','配方仍只接受既有 Flash 选题来源',{'status':code,'response':p1},'新人需要创建专属配方或显式扩展配方接受的来源；不应让用户猜测 alias 与 recipe 的绑定。')
        recipe=yaml.safe_load((ROOT/'config/video_recipes/flash.yaml').read_text())
        recipe['name']='隔离科研快讯';recipe['topic']['sources']=['isolation_science']
        recipe_code,_=call('通过真实配方接口新增科研配方','POST','/api/video/recipes',{'key':'isolation_science','content':yaml.safe_dump(recipe,allow_unicode=True)})
        check('专属科研配方真实API创建',recipe_code==201)
        code,p1=call('专属科研配方后创建输出','POST','/api/production',{'job':nj,'recipe':'isolation_science','reverify':False,'auto':1,'name':'科研单条'})
    assert code==201,p1
    code,p2=call('创建科研合集绑定前输出','POST','/api/production',{'job':nj,'recipe':'isolation_science','reverify':False,'auto':1,'name':'科研合集'})
    assert code==201,p2
    mode_before=p2['content_mode']
    code,col=call('创建合集栏目绑定','POST','/api/content-columns',{'key':'isolation_science_episode','name':'隔离科研合集','matrix':'isolation_science','type':'news','promise':'解释AI科研发现与实用方法','production_ids':[p2['key']],'content_mode':'episode'})
    check('真实栏目创建',code==201)
    mode_after=get('/api/production/'+p2['key']+'/settings')
    check('合集不是输出名称而是栏目契约',mode_before=='individual' and mode_after['content_mode']=='episode')
    jp=yaml.safe_load((ROOT/'config/joint_planning.yaml').read_text())
    pair_exists=any(p.get('episode_production')==p2['key'] for p in jp['pairs'])
    finding('N2','新建两份输出与合集栏目不会自动建立联合规划',{'episode':p2['key'],'single':p1['key'],'pair_exists':pair_exists,'mode_before':mode_before,'mode_after':mode_after['content_mode']},'原型“合集＋单条”还隐含栏目和联合规划两步。真实创建需要统一组装，而非两次添加生产配置。')
    jp['pairs'].append({'episode_production':p2['key'],'single_production':p1['key']})
    (ROOT/'config/joint_planning.yaml').write_text(yaml.safe_dump(jp,allow_unicode=True))
    summary['new_chain']={'job':nj,'outputs':[p1['key'],p2['key']],'column':'isolation_science_episode','manual_intervention':['通过 Job YAML 补齐 topics alias/format/prefix；首次 F 前缀冲突后改为 SCI','通过真实配方 API 提交专属配方 YAML，绑定科研选题来源','隔离副本手工补充 joint_planning pair；没有现成端到端链路创建接口'],'generation':'未调用外部服务，尚未生成科研报告或视频'}

    from core import production_policy
    policy=production_policy.load(str(ROOT/'config'))
    managed=production_policy.manages(policy,str(ROOT/'config'),job=nj)
    finding('N4','新矩阵未自动纳入现有生产策略',{'managed':managed,'configured_matrices':policy.get('matrices',[])},'创建计划与输出不等于继承现有生产窗口及材料收集策略；新链路需要明确策略归属。')
    code,copied=call('现有复制矩阵能力','POST','/api/matrices/ai_media/copy',{'key':'isolation_copy','name':'隔离复制科研','domain':'科研','keywords':['AI科学'],'jobs':[JOB]})
    check('既有矩阵复制可用',code==201)
    copy_job='isolation_copy/flash_discovery'
    copy_detail=get('/api/jobs/'+copy_job)
    copy_outputs=get('/api/production?job='+copy_job)
    summary['existing_copy_capability']={'status':code,'topics':yaml.safe_load(copy_detail['yaml_content']).get('topics'),'output_count':len(copy_outputs),'note':'现有复制会处理选题编号与 Job，但不复制视频输出和完整运行链路，应复用而非重写。'}
    editor=client('editor')
    code,_=call('编辑者读取调度权限','GET','/api/scheduler?scope=managed',actor=editor)
    check('真实角色权限差异',code==403)
    finding('R1','链路管理不能统一展示为所有用户均可操作',{'editor_scheduler_status':code},'来源/生产配置编辑与周期调度/矩阵停计划需要不同角色；原型未表达权限。')

    # Destructive operations are restricted to copied files, then restored below.
    call('删除隔离矩阵ai_media','DELETE','/api/matrices/ai_media')
    deleted_matrix=get('/api/production/flash_daily/settings')
    column_after=get('/api/content-columns/ai_flash')
    finding('D1','删除矩阵可能改变现有合集身份与继承',{'before_mode':baseline['content_mode'],'after_mode':deleted_matrix['content_mode'],'after_split':deleted_matrix['split'],'column_errors':column_after.get('errors'),'after_render':deleted_matrix['effective_render']},'原型声称“只删除分组且保留默认值”不足以描述实际行为，栏目所属矩阵被删会影响栏目有效性。')
    (ROOT/'config/matrices/ai_media.yaml').write_bytes(initial['config/matrices/ai_media.yaml'])
    call('删除隔离Flash合集生产配置','DELETE','/api/production/flash_daily')
    stale_col=yaml.safe_load((ROOT/'config/content_columns/ai_flash.yaml').read_text())
    stale_jp=yaml.safe_load((ROOT/'config/joint_planning.yaml').read_text())
    finding('D2','删除生产配置没有清理栏目和联合规划引用',{'column_still_references':'flash_daily' in stale_col['production_ids'],'pair_still_references':any(x.get('episode_production')=='flash_daily' for x in stale_jp['pairs']),'history_before':before_count,'history_after':counts()},'需要删除影响预览和一致的解除关联；原型中的“自动解除”目前不是后端保证。')
    code,blocked=call('运行中删除隔离共享Job','DELETE','/api/jobs/'+JOB)
    check('运行中的共享Job拒绝删除',code==409 and blocked.get('error',{}).get('code')=='already_running')
    # Explicit fixture transition: no worker is running in this process. Simulate the idle state in copied SQLite only.
    with sqlite3.connect(ROOT/'state/schedules.db') as db:
        released=db.execute("UPDATE schedule_runs SET status='failed',pid=NULL,error='隔离检查：模拟任务结束后的空闲状态' WHERE job_name=? AND status='running'",(JOB,)).rowcount
    summary['idle_fixture']={'copied_run_rows_changed':released,'purpose':'检查空闲 Job 删除；不是实际运行完成'}
    code,_=call('空闲时删除隔离共享Job','DELETE','/api/jobs/'+JOB)
    check('空闲共享Job删除实际成功',code==200)
    broken=get('/api/production?job='+JOB)
    finding('D3','删除共享Job没有拒绝仍被生产配置引用',{'delete_status':code,'remaining_productions':len(broken),'errors':[{ 'key':x['key'],'errors':x['errors']} for x in broken]},'从旧Jobs页删除可绕过链路页的共享保留规则，其他tab必须共同遵守引用生命周期。')
    check('删除Job后生产配置引用失效',code==200 and len(broken)==6 and all(x['errors'] for x in broken))
    check('删除管理对象保留文稿成片',counts()==before_count)
    code,_=call('不存在的链路聚合接口','GET','/api/chains')
    check('尚无统一链路API',code==404)
    code,_=call('禁止真实生成执行','POST','/api/jobs/'+nj+'/run')
    check('执行边界确实阻断',code==409)
    completed=True
finally:
    # Restore copied business configs and DBs; no production path is ever written.
    for folder in ('config','jobs'):
        for p in (ROOT/folder).rglob('*'):
            if p.is_file() and str(p.relative_to(ROOT)) not in initial:p.unlink()
    for rel,data in initial.items():(ROOT/rel).write_bytes(data)
    for name in ('video.db','schedules.db'):
        with sqlite3.connect(recovery/name) as src,sqlite3.connect(ROOT/'state'/name) as dst:src.backup(dst)
    result={'status':'隔离真实接口检查完成' if completed else '隔离检查中断，副本已还原','summary':summary,'checks':checks,'findings':findings,'boundaries':BOUNDARIES,'external_execution':False,'restored_snapshot':True,'requests':len(trace)}
    (HERE/'requests.json').write_text(json.dumps(trace,ensure_ascii=False,indent=2,default=str))
    (HERE/'results.json').write_text(json.dumps(result,ensure_ascii=False,indent=2,default=str))
    print(json.dumps({'checks':len(checks),'failed':[x for x in checks if not x['passed']],'findings':len(findings)},ensure_ascii=False),flush=True)
