import {createContext,createElement,useContext,useEffect,useState,useMemo,type ReactNode} from 'react';
import {useLocation} from 'react-router-dom';
import {api} from '@/api/client';
type Scope={key:string;name:string;job:string;outputs:string[];script_ids:string[];matrix_key?:string;recipes?:string[]};
function useLocalChainScope(enabled=true){
 const location=useLocation();const key=enabled?new URLSearchParams(location.search).get('chain'):null;
 const [result,setResult]=useState<Scope|null>(null);const [error,setError]=useState('');
 const data=result?.key===key?result:null;
 useEffect(()=>{
  setResult(null);setError('');
  if(!key)return;
  let alive=true,pending=false;
  const controller=new AbortController();
  const load=()=>{
   if(pending)return;
   pending=true;
   void api<Scope>('/api/chains/'+encodeURIComponent(key)+'/scope',{signal:controller.signal})
    .then(d=>{if(alive){setResult(d);setError('')}})
    .catch(()=>{if(alive)setError('链路范围读取失败，正在重试；暂不显示其他链路内容。')})
    .finally(()=>{pending=false});
  };
  const refresh=()=>{if(!document.hidden)load()};
  load();const timer=window.setInterval(refresh,15000);
  document.addEventListener('visibilitychange',refresh);
  return()=>{alive=false;controller.abort();clearInterval(timer);document.removeEventListener('visibilitychange',refresh)};
 },[key]);
 return useMemo(()=>({key,data,error,loading:!!key&&!data&&!error,job:(job:string)=>!key||data?.job===job,script:(id:string)=>!key||!!data?.script_ids.includes(id),output:(id:string)=>!key||!!data?.outputs.includes(id)}),[key,data,error]);
}
const ChainScopeContext=createContext<ReturnType<typeof useLocalChainScope>|null>(null);
export function ChainScopeProvider({children}:{children:ReactNode}){
 const scope=useLocalChainScope();
 return createElement(ChainScopeContext.Provider,{value:scope},children);
}
export function useChainScope(){
 const shared=useContext(ChainScopeContext);
 const local=useLocalChainScope(!shared);
 return shared??local;
}
