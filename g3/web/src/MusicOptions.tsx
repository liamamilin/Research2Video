import React, {useEffect, useState} from 'react';
import './music.css';
import {MusicGenerator} from './MusicGenerator';
import {CommonMusic, MusicReview, MusicTags, type MusicCurationInfo} from './MusicCuration';

export type BackgroundMusic = {enabled: boolean; asset_id?: string; level_db?: number; ducking?: boolean; loop?: boolean; fade_seconds?: number};
export type MusicAsset = {id: string; sha256?:string; name: string; source: string; kind: string; available: boolean; duration_seconds?: number; preview_url: string; topics?: string[]; curation?:MusicCurationInfo};
export const NO_MUSIC: BackgroundMusic = {enabled:false, level_db:-18, ducking:true, loop:true, fade_seconds:1.2};

export async function getMusicAssets(): Promise<MusicAsset[]> {
  const response = await fetch('/api/music-assets');
  const value = await response.json();
  if (!response.ok) throw new Error(value.detail?.message || '无法读取音乐素材');
  return value.items;
}
export function musicDraft(value: unknown): BackgroundMusic {
  if (!value || typeof value !== 'object') return {...NO_MUSIC};
  const data = value as BackgroundMusic;
  return {...NO_MUSIC, enabled:data.enabled === true, ...(typeof data.asset_id === 'string' && data.asset_id.length <= 64 ? {asset_id:data.asset_id} : {}),
    level_db: typeof data.level_db === 'number' && data.level_db >= -30 && data.level_db <= -12 ? data.level_db : -18,
    ducking:data.ducking !== false, loop:data.loop !== false,
    fade_seconds:typeof data.fade_seconds === 'number' && data.fade_seconds >= .1 && data.fade_seconds <= 3 ? data.fade_seconds : 1.2};
}

export function MusicControls({value, onChange, onBusy, id = 'create-music'}: {value:BackgroundMusic; onChange:(value:BackgroundMusic)=>void; onBusy?:(busy:boolean)=>void; id?:string}) {
  const [assets, setAssets] = useState<MusicAsset[]>([]), [error, setError] = useState(''), [busy, setBusy] = useState(false);
  const [showGenerator,setShowGenerator]=useState(false);
  const refresh=()=>{void getMusicAssets().then(setAssets).catch(e=>setError(e.message));};
  useEffect(refresh, []);
  const selected = assets.find(a => a.id === value.asset_id);
  async function upload(file?: File) {
    if (!file) return;
    if (file.size > 32 * 1024 * 1024) {setError('每份音乐最大 32 MB。'); return;}
    setBusy(true); onBusy?.(true); setError('');
    try {
      const form = new FormData(); form.append('file', file);
      const response = await fetch('/api/music-assets', {method:'POST', body:form}); const asset = await response.json();
      if (!response.ok) throw new Error(asset.detail?.message || '音乐导入失败');
      setAssets(await getMusicAssets()); onChange({...value, enabled:true, asset_id:asset.id});
    } catch (e) {setError(e instanceof Error ? e.message : '音乐导入失败');}
    finally {setBusy(false); onBusy?.(false);}
  }
  return <section className="music-options" aria-label="背景配乐与混音">
    <div className="music-heading"><h3>背景配乐</h3><label className="music-check"><input type="checkbox" checked={value.enabled} onChange={e => onChange({...value, enabled:e.target.checked, asset_id:value.asset_id || assets.find(a => a.available)?.id})}/>开启配乐</label></div>
    <p>正文配乐自动淡入淡出，默认在旁白出现时压低。片头片尾沿用包装提示音。</p>
    <label className="setting"><span className="setting-label">音乐素材</span><select className="text-input" aria-label="音乐素材" value={value.asset_id || ''} disabled={busy} onChange={e => onChange({...value, asset_id:e.target.value || undefined})}><option value="">选择音乐素材</option>{assets.map(a => <option key={a.id} value={a.id} disabled={!a.available}>{a.curation?.display_name||a.name}{a.available ? '' : '（暂不可用）'}</option>)}</select></label>
    <div className="music-import"><label className="secondary-button" htmlFor={`${id}-file`}>{busy ? '正在导入…' : '导入本地音乐'}</label><input id={`${id}-file`} type="file" accept=".wav,.mp3,.flac,.ogg" disabled={busy} onChange={e => {void upload(e.target.files?.[0]); e.target.value='';}}/><small>WAV · MP3 · FLAC · OGG，最大 32 MB / 10 分钟</small></div>
    {selected && <div className="music-preview"><audio key={selected.id} controls preload="none" src={selected.preview_url} aria-label={`${selected.name}音乐试听`}/><small>{selected.source}</small><MusicTags value={selected.curation}/></div>}
    <div className="settings-grid music-settings"><label className="setting"><span className="setting-label">配乐相对旁白音量 · {value.level_db ?? -18} dB</span><input type="range" aria-label="配乐相对音量" min={-30} max={-12} step={1} value={value.level_db ?? -18} disabled={!value.enabled} onChange={e => onChange({...value,level_db:Number(e.target.value)})}/><small>左侧更轻，右侧更明显；旁白仍保持优先。</small></label><label className="setting"><span className="setting-label">淡入淡出（秒）</span><input className="text-input" aria-label="配乐淡入淡出秒数" type="number" min={.1} max={3} step={.1} value={value.fade_seconds ?? 1.2} disabled={!value.enabled} onChange={e => onChange({...value,fade_seconds:Number(e.target.value)})}/></label></div>
    <div className="music-switches"><label><input type="checkbox" checked={value.ducking !== false} disabled={!value.enabled} onChange={e => onChange({...value,ducking:e.target.checked})}/>旁白时自动压低</label><label><input type="checkbox" checked={value.loop !== false} disabled={!value.enabled} onChange={e => onChange({...value,loop:e.target.checked})}/>循环铺满正文</label></div>
    {value.enabled && (!value.asset_id || selected?.available === false) && <p className="revision-error">请选择可用音乐；也可以关闭配乐继续制作。</p>}
    {error && <p className="revision-error" role="alert">{error}</p>}
    <small className="music-local-note">音乐文件留在本机；每次制作保存所选素材和混音设置。</small>
    <details className="music-generation-details" onToggle={e=>setShowGenerator(e.currentTarget.open)}><summary>生成新的 AI 配乐</summary>{showGenerator&&<MusicGenerator onLibraryChanged={refresh} onUse={asset_id=>{refresh();onChange({...value,enabled:true,asset_id});}}/>}</details>
  </section>;
}

export function MusicShelf({onSelect}: {onSelect:(id:string)=>void}) {
  const [assets, setAssets] = useState<MusicAsset[]>([]), [error, setError] = useState('');
  const refresh=()=>{void getMusicAssets().then(setAssets).catch(e=>setError(e.message));};
  useEffect(refresh, []);
  return <section className="music-shelf"><CommonMusic assets={assets} onSelect={onSelect} onRefresh={refresh}/><MusicGenerator onLibraryChanged={refresh} onUse={onSelect}/><h2>可用于配乐的音乐素材</h2><p>先试听，再搭配题材。生成音乐会自动保存在这里，也可在新建页导入自己的音乐。</p>{error && <p role="alert">{error}</p>}<div className="resource-grid">{assets.map(a => <article className="resource-card" key={a.id}><span className="resource-state partial">{a.kind === 'generated'?'AI 生成':a.kind === 'uploaded' ? '本机导入' : '参考库素材'}</span><h3>{a.curation?.display_name||a.name}</h3><p>{a.curation?.purpose||a.topics?.join(' · ')}</p><MusicTags value={a.curation}/><audio controls preload="none" src={a.preview_url} aria-label={`${a.name}音乐素材试听`}/><p className="resource-capability">{a.source}</p><button type="button" disabled={!a.available} className="secondary-button" onClick={() => onSelect(a.id)}>{a.available ? '用作背景配乐' : '连接素材磁盘后使用'}</button><MusicReview asset={a} onRefresh={refresh}/></article>)}</div></section>;
}
