from pathlib import Path
r=Path(__file__).parent/'runtime'
p=r/'web/services/chains.py';s=p.read_text().replace("    for script in scripts:\n        version=store.get_version(script['id'],script.get('current_version',0)) or {}", "    versions=store.current_versions([script['id'] for script in scripts[:100]])\n    for script in scripts[:100]:\n        version=versions.get(script['id']) or {}")
s += '''

def scope(key):
    m=find(key,True)
    ids=set(m.get('outputs',[])+m.get('retired_outputs',[]))
    scripts=store.list_scripts(limit=100000)+store.list_scripts(limit=100000,archived=True)
    return {'key':key,'name':m['name'],'job':m['job'],'outputs':sorted(ids),
            'script_ids':[s['id'] for s in scripts if s.get('production') in ids]}
''';p.write_text(s)
p=r/'web/routes/chains.py';s=p.read_text();s+='''

@router.get('/{key}/scope')
def scope(key:str,user=Depends(require_viewer)):
    return chains.scope(key)
''';p.write_text(s)
p=r/'ui/src/lib/use-chain-scope.ts';p.write_text('''import {useEffect,useState} from 'react';
import {useLocation} from 'react-router-dom';
import {api} from '@/api/client';
type Scope={key:string;name:string;job:string;outputs:string[];script_ids:string[]};
export function useChainScope(){
 const location=useLocation();const key=new URLSearchParams(location.search).get('chain');
 const [data,setData]=useState<Scope|null>(null);
 useEffect(()=>{let alive=true;setData(null);if(key)void api<Scope>('/api/chains/'+encodeURIComponent(key)+'/scope').then(d=>{if(alive)setData(d)}).catch(()=>{});return()=>{alive=false}},[key,location.pathname]);
 return {key,data,job:(job:string)=>!key||data?.job===job,script:(id:string)=>!key||!!data?.script_ids.includes(id),output:(id:string)=>!key||!!data?.outputs.includes(id)};
}
''')
configs={
'Outputs':('OutputsPage','return data.scripts.filter((s) =>','return data.scripts.filter((s) => chainScope.output(s.production) &&'),
'JobsList':('JobsListPage','const visible = jobs.filter((j) => {','const visible = jobs.filter((j) => {\n    if (!chainScope.job(j.name)) return false;'),
'Sources':('SourcesPage','return (catalog?.sources ?? []).filter((s) =>','return (catalog?.sources ?? []).filter((s) => (!chainScope.key || s.used_by.some(u=>chainScope.job(u.job))) &&'),
'Scheduler':('SchedulerPage','const visibleSchedules = jobs.filter((j) =>','const visibleSchedules = jobs.filter((j) => chainScope.job(j.job_name||"" ) &&'),
'VideoScripts':('VideoScriptsPage','const scope = (scripts ?? []).filter((s) =>','const scope = (scripts ?? []).filter((s) => chainScope.script(s.id) &&'),
'PublishQueue':('PublishQueuePage','const scope = (rows ?? []).filter((s) =>','const scope = (rows ?? []).filter((s) => chainScope.script(s.script_id) &&'),
'HomeBoard':('HomeBoardPage','const scope = items.filter((i) =>','const scope = items.filter((i) => chainScope.script(i.script_id) &&'),
}
for name,(func,old,new) in configs.items():
 p=r/f'ui/src/pages/{name}.tsx';s=p.read_text();assert old in s,name;s="import {useChainScope} from '@/lib/use-chain-scope';\n"+s;s=s.replace(f'export function {func}() {{',f'export function {func}() {{\n  const chainScope=useChainScope();');s=s.replace(old,new)
 if name=='Sources':s=s.replace('[catalog, filter, tag, text]','[catalog, filter, tag, text, chainScope.data, chainScope.key]')
 if name=='Outputs':s=s.replace('[data, q, render, range]', '[data, q, render, range, chainScope.data, chainScope.key]')
 p.write_text(s)
p=r/'ui/src/components/Sidebar.tsx';s=p.read_text().replace('const chain = new URLSearchParams(useLocation().search).get("chain");', 'const location=useLocation();\n  const chain = new URLSearchParams(location.search).get("chain") || (/^\\/chains\\/(?!new$)([^/]+)$/.exec(location.pathname)?.[1]);');p.write_text(s)
p=r/'ui/src/components/ChainContext.tsx';s=p.read_text().replace('在此保存的修改会在链路管理中生效。','在此保存的修改会在链路管理中生效。产出、文稿、看板、任务、来源及周期列表已按此链路筛选。');s=s.replace('</span></div>;','</span>{explicit&&<Link data-chain-exit="true" className="underline" to={location.pathname}>退出关联，查看全部</Link>}</div>;');p.write_text(s)
p=r/'ui/src/components/Layout.tsx';s=p.read_text().replace('useNavigationType }','useNavigationType, useNavigate }');s=s.replace('  const location = useLocation();','  const location = useLocation();\n  const navigate=useNavigate();\n  const chain=new URLSearchParams(location.search).get("chain") || /^\\/chains\\/(?!new$)([^/]+)$/.exec(location.pathname)?.[1];')
s=s.replace('<div className="flex h-screen overflow-hidden" onPointerOverCapture', '''<div className="flex h-screen overflow-hidden" onClickCapture={e=>{
      const anchor=e.target instanceof Element?e.target.closest('a[href]'):null;
      if(!chain||!(anchor instanceof HTMLAnchorElement)||anchor.dataset.chainExit||anchor.target||anchor.hasAttribute('download')||e.metaKey||e.ctrlKey||e.shiftKey||e.altKey||e.button!==0)return;
      const to=new URL(anchor.href,window.location.href);
      if(to.origin!==window.location.origin||to.pathname.startsWith('/chains')||to.pathname.startsWith('/api/')||to.searchParams.has('chain'))return;
      to.searchParams.set('chain',chain);e.preventDefault();e.stopPropagation();navigate(to.pathname+to.search+to.hash);handleNavigate();
    }} onPointerOverCapture''');p.write_text(s)
