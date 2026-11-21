import {useEffect,useRef,useState} from 'react';
import {Link,useLocation} from 'react-router-dom';
import {api} from '@/api/client';
import {useChainScope} from '@/lib/use-chain-scope';
type Row={key:string;name:string;job:string;matrix_key:string;archived?:boolean};
const CHAIN_LIST_TTL_MS=30000;
let cachedRows:Row[]|null=null;
let cachedAt=0;
let inFlight:Promise<Row[]>|null=null;
function fetchChainRows(force=false):Promise<Row[]>{
 if(!force&&cachedRows!==null&&Date.now()-cachedAt<CHAIN_LIST_TTL_MS)return Promise.resolve(cachedRows);
 if(!inFlight){
  inFlight=api<{items:Row[];archived?:Row[]}>('/api/chains')
   .then(x=>{
    const next=[...x.items,...(x.archived||[]).map(row=>({...row,archived:true}))];
    cachedRows=next;cachedAt=Date.now();return next;
   })
   .finally(()=>{inFlight=null;});
 }
 return inFlight;
}
export function ChainContext(){
 const scope=useChainScope();
 const location=useLocation();const explicit=new URLSearchParams(location.search).get('chain');const [rows,setRows]=useState<Row[]>(()=>cachedRows??[]);
 const wasOnChains=useRef(location.pathname.startsWith('/chains'));
 useEffect(()=>{
  const onChains=location.pathname.startsWith('/chains');
  if(onChains){wasOnChains.current=true;return;}
  const force=wasOnChains.current;
  wasOnChains.current=false;
  let alive=true;
  void fetchChainRows(force).then(next=>{if(alive)setRows(next);}).catch(()=>{});
  return()=>{alive=false;};
 },[location.pathname]);
 const exitQuery=new URLSearchParams(location.search);exitQuery.delete('chain');const exitPath=location.pathname+(exitQuery.size?'?'+exitQuery.toString():'');
 if(location.pathname.startsWith('/chains'))return null;
 const relevant=rows.filter(x=>explicit?x.key===explicit:location.pathname==='/jobs/'+encodeURIComponent(x.job)||location.pathname==='/matrices/'+x.matrix_key);
 if(!relevant.length&&!explicit)return null;
 return <div className="rounded-lg border border-accent/30 bg-accent/5 px-4 py-3 mb-4 flex flex-wrap gap-3 text-xs"><span className="text-text-muted">关联链路</span>{scope.loading&&<span role="status">正在读取链路范围…</span>}{scope.error&&<span role="alert">{scope.error}</span>}{relevant.length?relevant.map(x=><Link key={x.key} className="text-accent font-medium" to={'/chains/'+x.key}>← {x.name}</Link>):<Link className="text-accent" to={'/chains/'+explicit}>← 返回链路</Link>}<span className="text-text-muted">{relevant.some(x=>x.archived)?'此链路已删除，当前查看保留的历史记录。':explicit?'在此保存的修改会在链路管理中生效。矩阵、报告、产出、文稿、看板、任务、来源、配方及周期列表已按此链路筛选。':'这些链路使用当前研究或矩阵；选择链路可统一管理，其他列表保持全局视图。'}</span>{explicit&&<Link data-chain-exit="true" className="underline" to={exitPath}>退出关联，查看全部</Link>}</div>;
}
