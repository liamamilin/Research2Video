import {useEffect,useState,useMemo} from 'react';
import {useLocation} from 'react-router-dom';
import {api} from '@/api/client';
type Scope={key:string;name:string;job:string;outputs:string[];script_ids:string[];matrix_key?:string;recipes?:string[]};
export function useChainScope(){
 const location=useLocation();const key=new URLSearchParams(location.search).get('chain');
 const [result,setResult]=useState<Scope|null>(null);const [error,setError]=useState('');
 const data=result?.key===key?result:null;
 useEffect(()=>{let alive=true;setResult(null);setError('');const refresh=()=>{if(key)void api<Scope>('/api/chains/'+encodeURIComponent(key)+'/scope').then(d=>{if(alive){setResult(d);setError('')}}).catch(()=>{if(alive)setError('链路范围读取失败，正在重试；暂不显示其他链路内容。')})};refresh();const timer=key?window.setInterval(refresh,5000):undefined;return()=>{alive=false;if(timer)clearInterval(timer)}},[key,location.pathname]);
 return useMemo(()=>({key,data,error,loading:!!key&&!data&&!error,job:(job:string)=>!key||data?.job===job,script:(id:string)=>!key||!!data?.script_ids.includes(id),output:(id:string)=>!key||!!data?.outputs.includes(id)}),[key,data,error]);
}
