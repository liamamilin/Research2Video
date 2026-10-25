import React, {useEffect, useRef, useState} from 'react';

type MusicJob = {id:string;status:string;stage:string;asset_id?:string;request:{name:string;style:string;duration_seconds:number};settings:{seed:number};error_message?:string;parent_id?:string;cancel_requested?:boolean};
type EngineStatus = {ready:boolean;message:string;duration_seconds:number[];styles:{id:string;name:string;topics:string[]}[]};
const STAGE:Record<string,string> = {QUEUED:'等待制作',VERIFYING:'校验模型',LOADING:'加载模型',GENERATING:'生成音乐',CHECKING:'校验并入库',COMPLETED:'已入库',CANCELLED:'已取消',CANCELLING:'正在取消',INTERRUPTED:'已中断'};
async function request<T>(url:string, body?:object):Promise<T> {
  const response=await fetch(url,body?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}:undefined);
  const value=await response.json();
  if(!response.ok) throw new Error(value.detail?.message || (Array.isArray(value.detail)?value.detail.map((d:{msg:string})=>d.msg).join('；'):'音乐请求失败'));
  return value;
}

export function MusicGenerator({onLibraryChanged,onUse}:{onLibraryChanged:()=>void;onUse:(id:string)=>void}) {
  const [engine,setEngine]=useState<EngineStatus|null>(null),[jobs,setJobs]=useState<MusicJob[]>([]),[name,setName]=useState('我的背景配乐'),[style,setStyle]=useState('ambient');
  const [description,setDescription]=useState(''),[duration,setDuration]=useState(24),[seed,setSeed]=useState(''),[busy,setBusy]=useState(false),[error,setError]=useState('');
  const submitted=useRef<{body:string;key:string}|null>(null),known=useRef(new Set<string>()),changed=useRef(onLibraryChanged);
  changed.current=onLibraryChanged;
  useEffect(()=>{
    let disposed=false;
    async function refresh(){try{
      const value=await request<{items:MusicJob[]}>('/api/music-jobs?limit=6');
      if(disposed)return;
      setJobs(value.items);
      const completed=value.items.filter(j=>j.status==='COMPLETED'&&!known.current.has(j.id));
      value.items.filter(j=>j.status==='COMPLETED').forEach(j=>known.current.add(j.id));
      if(completed.length)changed.current();
    }catch(e){if(!disposed)setError(e instanceof Error?e.message:'无法读取音乐任务');}}
    void request<EngineStatus>('/api/music-generation/status').then(value=>{if(!disposed)setEngine(value);}).catch(e=>{if(!disposed)setError(e.message);});
    void refresh();const timer=window.setInterval(()=>void refresh(),2500);
    return()=>{disposed=true;window.clearInterval(timer);};
  },[]);
  async function generate(){
    setBusy(true);setError('');
    const body={name,style,description,duration_seconds:duration,seed:seed===''?null:Number(seed)};
    const signature=JSON.stringify(body);
    if(submitted.current?.body!==signature)submitted.current={body:signature,key:crypto.randomUUID()};
    try{const job=await request<MusicJob>('/api/music-jobs',{...body,idempotency_key:submitted.current!.key});setJobs(current=>[job,...current.filter(j=>j.id!==job.id)].slice(0,6));submitted.current=null;}
    catch(e){setError(e instanceof Error?e.message:'生成请求失败');}finally{setBusy(false);}
  }
  async function action(job:MusicJob,kind:'cancel'|'retry'){
    setError('');setBusy(true);try{const value=await request<MusicJob>(`/api/music-jobs/${job.id}/${kind}`,{});setJobs(current=>[value,...current.filter(j=>j.id!==value.id)].slice(0,6));}
    catch(e){setError(e instanceof Error?e.message:'操作失败');}finally{setBusy(false);}
  }
  const active=jobs.some(j=>['QUEUED','RUNNING'].includes(j.status));
  return <section className="music-generator" aria-label="AI音乐生成"><h2>AI 音乐生成</h2><p>描述想要的无词配乐，完成后自动保存到创作资源库，可用于多个视频。</p>
    {engine&&<p className={engine.ready?'quiet-note':'revision-error'}>{engine.message}</p>}
    <div className="settings-grid"><label className="setting"><span className="setting-label">音乐名称</span><input className="text-input" aria-label="生成音乐名称" value={name} maxLength={80} onChange={e=>setName(e.target.value)}/></label>
    <label className="setting"><span className="setting-label">风格方向</span><select className="text-input" aria-label="生成音乐风格" value={style} onChange={e=>setStyle(e.target.value)}>{(engine?.styles||[{id:'ambient',name:'轻柔氛围'},{id:'piano',name:'温暖钢琴'},{id:'electronic',name:'轻快电子'}]).map(s=><option key={s.id} value={s.id}>{s.name}</option>)}</select></label>
    <label className="setting"><span className="setting-label">生成时长</span><select className="text-input" aria-label="生成音乐时长" value={duration} onChange={e=>setDuration(Number(e.target.value))}>{[10,16,24,30].map(n=><option key={n} value={n}>{n} 秒</option>)}</select></label>
    <label className="setting"><span className="setting-label">随机种子（可选）</span><input className="text-input" aria-label="音乐随机种子" type="number" min={0} max={2147483647} step={1} value={seed} onChange={e=>setSeed(e.target.value)} placeholder="留空自动选择"/></label></div>
    <label className="setting"><span className="setting-label">音乐描述（可选）</span><textarea className="text-input" aria-label="生成音乐描述" maxLength={220} rows={3} value={description} onChange={e=>setDescription(e.target.value)} placeholder="例如：温暖、舒缓，节奏轻一些，为科普旁白留出空间"/></label>
    <button type="button" className="primary-button" disabled={!engine?.ready||busy||active||name.trim().length<2||(seed!==''&&(!Number.isInteger(Number(seed))||Number(seed)<0||Number(seed)>2147483647))} onClick={()=>void generate()}>{busy?'正在提交…':active?'音乐正在制作，可离开此页':'生成音乐并入库'}</button>
    <small>本机生成 10–30 秒无词配乐，使用时可循环。与视频制作依次执行，关闭页面不影响后台任务；生成后可先试听。</small>
    {error&&<p role="alert" className="revision-error">{error}</p>}
    {jobs.length>0&&<div className="music-job-list" aria-label="音乐制作记录">{jobs.map(job=><article key={job.id} className="music-job"><div><b>{job.request.name}</b><span role="status">{job.status==='FAILED'?'生成失败':STAGE[job.stage]||'制作中'} · {job.request.duration_seconds} 秒</span></div>
      {job.error_message&&<p className="revision-error">{job.error_message}</p>}
      {job.status==='COMPLETED'&&job.asset_id&&<><audio controls preload="none" src={`/api/music-assets/${job.asset_id}/preview`} aria-label={`${job.request.name}生成结果试听`}/><button type="button" className="secondary-button" onClick={()=>onUse(job.asset_id!)}>使用这首音乐</button></>}
      {['QUEUED','RUNNING'].includes(job.status)&&<button type="button" className="text-link" disabled={busy||job.cancel_requested===true||job.stage==='CHECKING'} onClick={()=>void action(job,'cancel')}>取消音乐制作</button>}
      {['FAILED','CANCELLED'].includes(job.status)&&<button type="button" className="text-link" disabled={busy||active||!engine?.ready} onClick={()=>void action(job,'retry')}>按原设置重新生成</button>}
    </article>)}</div>}
  </section>;
}
