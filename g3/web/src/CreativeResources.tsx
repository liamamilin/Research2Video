import React, {useEffect, useMemo, useState} from 'react';
import './creative-resources.css';
import {getMusicAssets, MusicShelf, type BackgroundMusic, type MusicAsset} from './MusicOptions';

export type VideoForm = 'auto' | 'process_diagram' | 'information_motion' | 'kinetic_typography' | 'image_slideshow' | 'chat_dialogue' | 'information_cards' | 'object_showcase' | 'dynamic_collage';
export type PackagingSelection = {preset: 'common'; enabled?: boolean; label?: string; accent?: string; intro?: {enabled?: boolean; seconds?: number}; outro?: {enabled?: boolean; seconds?: number}; badge?: {enabled?: boolean}; source_card?: {enabled?: boolean}; sound?: {enabled?: boolean}};
export type ProductionRecipe = {
  id: string; version: number; name: string; description: string; topics: string[]; recommended_forms: string[];
  defaults: {video_form: VideoForm; duration_mode: 'content'; creative_intent: {audience: string; purpose: string; tone: string; focus: string}; packaging: PackagingSelection; background_music?: BackgroundMusic};
  music: {mode: 'none' | 'optional'; suggested_forms: string[]; description: string};
};
type Resource = {
  id: string; name: string; group: string; kind: 'video' | 'music'; description: string; topics: string[]; structure: string[];
  reference_only?: boolean; availability: 'available' | 'partial' | 'planned'; executors: string[]; capability_note: string; music_assets?: string[]; generation_styles?: string[];
  example?: {available: boolean; preview_url: string; source: string; review: string};
};
export type CreativeCatalog = {
  forms: Resource[]; native_forms?: Resource[]; recipes: ProductionRecipe[];
  executors: Record<string, {name: string; available: boolean}>;
  packaging: {components: Record<string, {slot: string; label: string; version: number}>};
};

const FORM_NAMES: Record<string, string> = {object_showcase: '对象展示', dynamic_collage: '动态拼贴', information_cards: '信息卡片 · 快速', auto: '按素材自动选择', information_motion: '简洁信息动效', process_diagram: '流程动态图解', kinetic_typography: '动态排版', image_slideshow: '图片轮播', chat_dialogue: '聊天对话体'};

export function RecipePicker({catalog, selected, onSelect}: {catalog: CreativeCatalog; selected: string; onSelect: (recipe: ProductionRecipe | null) => void}) {
  const current = catalog.recipes.find(item => item.id === selected);
  return <section className="recipe-picker" aria-label="制作配方">
    <div className="section-line"><div><h2>制作配方</h2><p>选择适合题材的组合，表现形式和包装仍可调整。</p></div><a className="text-link" href="#resources">浏览创作资源</a></div>
    <div className="recipe-options" role="group" aria-label="选择制作配方">
      <button type="button" aria-pressed={!selected} className={!selected ? 'selected' : ''} onClick={() => onSelect(null)}><b>自定义设置</b><small>沿用当前设置，自由选择</small></button>
      {catalog.recipes.map(item => <button type="button" key={item.id} aria-pressed={item.id === selected} className={item.id === selected ? 'selected' : ''} onClick={() => onSelect(item)}><b>{item.name}</b><small>{item.description}</small></button>)}
    </div>
    {current && <p className="recipe-summary" role="status">已采用{current.name}的题材建议，当前形式、配乐和包装以下方设置为准，素材与旁白声音保持原样。</p>}
  </section>;
}

export function ResourceLibrary({catalog, onUseRecipe, onUseForm, onUseMusic}: {catalog: CreativeCatalog; onUseRecipe: (recipe: ProductionRecipe, form?: VideoForm, musicId?: string) => void; onUseForm: (form: VideoForm) => void; onUseMusic: (id:string) => void}) {
  const [tab, setTab] = useState('recipes');
  const [query, setQuery] = useState('');
  const [availableOnly, setAvailableOnly] = useState(false);
  const [music,setMusic]=useState<MusicAsset[]>([]),[recipeMusic,setRecipeMusic]=useState<Record<string,string>>({});
  useEffect(()=>{if(tab==='recipes')void getMusicAssets().then(setMusic).catch(()=>{});},[tab]);
  const filtered = useMemo(() => [...catalog.forms, ...(catalog.native_forms || [])].filter(item => item.kind === tab && (!availableOnly || item.executors.length > 0 || Boolean(item.music_assets?.length) || Boolean(item.generation_styles?.length))
    && [item.name, item.id, item.group, item.description, ...item.topics].join(' ').toLowerCase().includes(query.toLowerCase().trim())), [catalog, tab, query, availableOnly]);
  const nameFor = (id: string) => catalog.forms.find(form => form.id === id)?.name || id;
  return <section className="resources-page">
    <div className="page-heading"><div><div className="eyebrow">按题材组合表达方式</div><h1>创作资源库</h1><p>17 种视频形式、14 种音乐形式，与共用的包装组件。已接入与待接入分别标注；有图片可用对象展示和动态拼贴。</p></div><a className="secondary-button" href="#new">返回创作</a></div>
    <div className="resource-tabs" role="group" aria-label="资源分类">{[['recipes','制作配方'],['video','视频形式 · 17'],['music','音乐形式 · 14'],['packaging','包装与辅助素材']].map(([key, label]) => <button type="button" key={key} aria-pressed={tab === key} className={tab === key ? 'selected' : ''} onClick={() => {setTab(key); setQuery(''); setAvailableOnly(false);}}>{label}</button>)}</div>
    {tab === 'recipes' && <div className="resource-grid recipe-grid">{catalog.recipes.map(item => <article className="resource-card recipe-card" key={item.id}>
      <span className="resource-state available">可用于制作</span><h2>{item.name}</h2><p>{item.description}</p>
      <dl><dt>当前画面</dt><dd>{FORM_NAMES[item.defaults.video_form]}</dd><dt>适合的形式</dt><dd>{item.recommended_forms.map(nameFor).join('、')}</dd><dt>声音安排</dt><dd>{item.music.description}</dd><dt>栏目包装</dt><dd>{item.defaults.packaging.label} · {item.defaults.packaging.intro?.enabled === false ? '无片头' : '短片头'} · {item.defaults.packaging.sound?.enabled === false ? '无提示音' : '片头片尾提示音'}</dd></dl>
      <p className="quiet-note">{item.topics.join(' · ')}。纯文稿沿用默认形式；对象展示和动态拼贴需要已有图片。</p>
      {music.some(a=>(a.kind==='generated'||a.curation?.common)&&a.available)&&<label className="setting"><span className="setting-label">搭配常用或已生成的音乐</span><select className="text-input" aria-label={`${item.name}的配乐`} value={recipeMusic[item.id]||''} onChange={e=>setRecipeMusic(current=>({...current,[item.id]:e.target.value}))}><option value="">沿用配方默认</option>{music.filter(a=>(a.kind==='generated'||(a.curation?.common&&a.curation.recipe_ids?.includes(item.id)))&&a.available).sort((a,b)=>Number(Boolean(b.curation?.recipe_ids?.includes(item.id)))-Number(Boolean(a.curation?.recipe_ids?.includes(item.id)))).map(a=><option key={a.id} value={a.id}>{a.curation?.display_name||a.name}{a.curation?.recipe_ids?.includes(item.id)?' · 本题材常用':''}{a.curation?.review.status==='passed'?' · 已试听':' · 待试听'}</option>)}</select></label>}
      <button type="button" className="primary-button" onClick={() => onUseRecipe(item,undefined,recipeMusic[item.id])}>使用{item.name}配方</button>
      <div className="recipe-form-links">{item.recommended_forms.flatMap(id => catalog.forms.find(f => f.id === id)?.executors || []).filter((id, i, ids) => ids.indexOf(id) === i && id !== item.defaults.video_form).map(id => <button type="button" className="text-link" key={id} onClick={() => onUseRecipe(item, id as VideoForm,recipeMusic[item.id])}>配方 + {catalog.executors[id].name}</button>)}</div>
    </article>)}</div>}
    {(tab === 'video' || tab === 'music') && <>
      <div className="resource-toolbar"><label><span className="sr-only">搜索资源</span><input className="text-input" value={query} onChange={e => setQuery(e.target.value)} placeholder="搜索名称、题材或形式编号"/></label><label className="resource-check"><input type="checkbox" checked={availableOnly} onChange={e => setAvailableOnly(e.target.checked)}/>仅看已接入制作的形式</label></div>
      {tab === 'music' && <><MusicShelf onSelect={onUseMusic}/><p className="resource-notice">以下保留 14 种音乐形式和案例。已接入本机无词配乐生成，其他音乐形式仍按各自能力标注。</p></>}
      <div className="resource-grid">{filtered.map(item => <ResourceCard key={item.id} item={item} catalog={catalog} onUseForm={onUseForm} onUseMusic={onUseMusic}/>)}</div>
      {!filtered.length && <div className="empty-state"><h2>没有符合条件的资源</h2><p>试试其他关键词，或关闭制作能力筛选。</p></div>}
    </>}
    {tab === 'packaging' && <><p className="resource-notice">配方引用共用包装组件；在成片设置中可分别开关、调整栏目名称。来源卡在提供来源时显示。</p><div className="resource-grid">{Object.entries(catalog.packaging.components).map(([id, item]) => <article className="resource-card" key={id}><span className="resource-state available">已接入</span><h2>{item.label}</h2><p>{({intro:'正文前的栏目片头', outro:'正文后的收尾画面', badge:'正文画面上的栏目角标', source_card:'展示本次提供的参考来源', sound:'片头片尾的提示音，不作为正文背景配乐'} as Record<string, string>)[item.slot]}</p><a href="#new" className="text-link">在成片设置中使用</a></article>)}</div></>}
  </section>;
}

function ResourceCard({item, catalog, onUseForm, onUseMusic}: {item: Resource; catalog: CreativeCatalog; onUseForm: (form: VideoForm) => void; onUseMusic:(id:string)=>void}) {
  const [open, setOpen] = useState(false);
  const [mediaError, setMediaError] = useState(false);
  const state = item.reference_only ? '仅作参考' : {available: '已接入制作', partial: '部分表达可用', planned: '待接入制作'}[item.availability];
  return <article className="resource-card"><div className="resource-meta"><span>{item.id} · {item.group}</span><span className={`resource-state ${item.availability}`}>{state}</span></div><h2>{item.name}</h2><p>{item.description}</p>{item.topics.length > 0 && <p className="resource-topics">适合：{item.topics.join(' · ')}</p>}<p className="resource-capability">{item.capability_note}</p>
    <details onToggle={e => setOpen(e.currentTarget.open)}><summary>结构与案例{item.example ? ' · 有媒体' : ' · 文字参考'}</summary>
      {item.structure.length > 0 && <p>{item.structure.join(' → ')}</p>}
      {open && item.example && <>{item.example.available && !mediaError ? item.kind === 'video' ? <video controls preload="none" src={item.example.preview_url} onError={() => setMediaError(true)} aria-label={`${item.name}案例`}/> : <audio controls preload="none" src={item.example.preview_url} onError={() => setMediaError(true)} aria-label={`${item.name}案例`}/> : <p>案例暂不可用，请连接素材磁盘后重试。</p>}<small>{item.example.source}。{item.example.review}；案例仅展示形式。</small></>}
      {!item.example && <p>暂无可展示的成品案例。</p>}
    </details>
    {item.music_assets?.map(id => <button type="button" key={id} className="secondary-button" onClick={() => onUseMusic(id)}>使用这类背景配乐</button>)}
    {item.executors.map(id => <button type="button" key={id} className="secondary-button" onClick={() => onUseForm(id as VideoForm)}>用{catalog.executors[id].name}创作</button>)}
  </article>;
}
