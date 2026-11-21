from pathlib import Path
p=Path('runtime/ui/src/pages/Chains.tsx');s=p.read_text();s=s.replace("  const [voices,setVoices]", "  const [sharedResearch,setSharedResearch]=useState<Chain|null>(null);\n  const [voices,setVoices]")
needle="  useEffect(()=>{localStorage.setItem('chain-wizard-v1',JSON.stringify(wizard));},[wizard]);"
insert="""  const sharedKey=catalog?.chains.find(c=>c.job===wizard.existing_job)?.key;
  useEffect(()=>{let alive=true;setSharedResearch(null);if(key==='new'&&sharedKey)void api<Chain>('/api/chains/'+sharedKey).then(d=>{if(alive)setSharedResearch(d)}).catch(e=>{if(alive)setError(msg(e))});return()=>{alive=false}},[key,sharedKey]);
"""
s=s.replace(needle,needle+'\n'+insert)
s=s.replace('existing_job:e.target.value})','existing_job:e.target.value,schedule:e.target.value?\'\':wizard.schedule})')
s=s.replace('<p>来源沿用共享研究，可在创建后查看和修改影响范围。</p>','<div className="space-y-2 text-sm"><p>沿用共享研究的信息源：</p>{sharedResearch?sharedResearch.sources.map(source=><p key={source.ref}>{source.name} · {source.enabled?\'已启用\':\'已停用\'}</p>):<p>正在读取共享研究…</p>}</div>')
a=s.index('{step===3&&');b=s.index('{step===4&&',a)
old=s[a:b];begin=old.index('<label className="block text-sm">运行方式');end=old.index('<p className="text-sm text-text-muted">定时运行')
old=old[:begin]+'''{wizard.existing_job?<SharedResearchSummary data={sharedResearch}/>:<>'''+old[begin:end]+'''</>}'''+old[end:];s=s[:a]+old+s[b:]
s=s.replace("{wizard.schedule?'每天 '+wizard.schedule.split(' ')[1]+':'+wizard.schedule.split(' ')[0].padStart(2,'0'):'手动运行'}", "{wizard.existing_job?'沿用共享研究':wizard.schedule?'每天 '+wizard.schedule.split(' ')[1]+':'+wizard.schedule.split(' ')[0].padStart(2,'0'):'手动运行'}")
s=s.replace('<p className="text-xs text-text-muted mt-2">独立研究纳入生产策略，范围为最近 {wizard.window_days} 天。</p>',"{wizard.existing_job?<SharedResearchSummary data={sharedResearch}/>:<p className=\"text-xs text-text-muted mt-2\">独立研究纳入生产策略，范围为最近 {wizard.window_days} 天。</p>}")
s += '''\nfunction SharedResearchSummary({data}:{data:Chain|null}){
 if(!data)return <p className="text-sm text-text-muted">正在读取共享研究…</p>;
 return <div className="rounded-lg bg-bg p-3 text-sm space-y-2"><p>共用「{data.name}」的研究安排，创建本链路不会修改它。</p>{data.schedules.length?data.schedules.map(plan=><p key={plan.id}>研究周期：{plan.schedule} · {plan.timezone} · {plan.enabled?'已启用':'已暂停'}</p>):<p>研究周期：手动运行</p>}<p>研究范围：{data.window_mode==='previous_slot'?'相邻研究周期':`最近 ${data.window_days} 天`}</p><p className="text-xs text-text-muted">之后修改来源、目标或周期会同时影响共用这项研究的链路；本链路的视频输出独立管理。</p></div>;
}
'''
p.write_text(s)
