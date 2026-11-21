import {useEffect,useState} from 'react';
import {useLocation} from 'react-router-dom';
import {api} from '@/api/client';
type Scope={key:string;name:string;job:string;outputs:string[];script_ids:string[];matrix_key?:string;recipes?:string[]};
export function useChainScope(){
 const location=useLocation();const key=new URLSearchParams(location.search).get('chain');
 const [data,setData]=useState<Scope|null>(null);
 useEffect(()=>{let alive=true;setData(null);const refresh=()=>{if(key)void api<Scope>('/api/chains/'+encodeURIComponent(key)+'/scope').then(d=>{if(alive)setData(d)}).catch(()=>{})};refresh();const timer=key?window.setInterval(refresh,5000):undefined;return()=>{alive=false;if(timer)clearInterval(timer)}},[key,location.pathname]);
 return {key,data,job:(job:string)=>!key||data?.job===job,script:(id:string)=>!key||!!data?.script_ids.includes(id),output:(id:string)=>!key||!!data?.outputs.includes(id)};
}
