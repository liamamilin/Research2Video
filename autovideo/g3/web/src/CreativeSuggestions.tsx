import React, {useEffect, useState} from 'react';
import type {CreativeImage} from './SlideshowImages';
import type {VideoForm} from './CreativeResources';

type Suggestions = {forms:{id:VideoForm;name:string;reason:string}[];music:{id:string;name:string;reason:string;preview_url:string}[];music_note:string};
export function CreativeSuggestions({source,recipe,images,onForm,onMusic}: {source:string;recipe:string;images:CreativeImage[];onForm:(form:VideoForm)=>void;onMusic:(id:string)=>void}) {
  const [value,setValue]=useState<Suggestions|null>(null),[error,setError]=useState('');
  const body=JSON.stringify({source_text:source,production_recipe:recipe||null,image_materials:images.map(i=>({image_id:i.image_id,caption:i.caption||'',role:i.role||'reference'}))});
  useEffect(()=>{
    const controller=new AbortController();
    setValue(null);setError('');
    const timer=setTimeout(()=>{
      void fetch('/api/creative-recommendations',{method:'POST',headers:{'Content-Type':'application/json'},body,signal:controller.signal})
        .then(async response=>{const result=await response.json();if(!response.ok)throw new Error(result.detail?.message||'暂时无法读取推荐');return result;})
        .then(result=>{if(!controller.signal.aborted)setValue(result);})
        .catch(e=>{if(!controller.signal.aborted)setError(e.message);});
    },450);
    return()=>{clearTimeout(timer);controller.abort();};
  },[body]);
  return <section className="creative-suggestions" aria-label="按素材推荐">
    <h3>按当前素材推荐</h3><p>根据已有图片、图片用途与题材配方提供建议，点击后应用到本次设置。</p>
    {value?.forms.map(item=><div className="suggestion-row" key={item.id}><div><b>{item.name}</b><p>{item.reason}</p></div><button type="button" className="secondary-button" onClick={()=>onForm(item.id)}>选用{item.name}</button></div>)}
    {!!value?.music.length && <details><summary>推荐无词配乐 · 优先复用</summary><p>{value.music_note}</p>{value.music.map(item=><div className="suggestion-music" key={item.id}><b>{item.name}</b><p>{item.reason}</p><audio controls preload="none" src={item.preview_url} aria-label={`${item.name}推荐试听`}/><button type="button" className="text-link" onClick={()=>onMusic(item.id)}>使用这首配乐</button></div>)}</details>}
    {error && <p role="status">{error}，仍可手动选择形式与配乐。</p>}
  </section>;
}
