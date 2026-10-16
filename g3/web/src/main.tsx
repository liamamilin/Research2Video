import React, {useCallback, useEffect, useMemo, useRef, useState} from 'react';
import {createRoot} from 'react-dom/client';
import './styles.css';
import './visual.css';

type Status = 'QUEUED' | 'RUNNING' | 'COMPLETED' | 'FAILED' | 'CANCELLED';
type Stage = {stage: string; status: string; duration_seconds?: number; error_message?: string | null};
type Attempt = {attempt_no: number; status: string; stages: Stage[]; error_code?: string | null};
type Job = {
  job_id: string; title: string; status: Status; active_stage: string | null; attempt_count: number;
  cancel_requested: boolean; error_code?: string | null; error_message?: string | null; retryable: boolean;
  source_sha256: string; source_char_count: number; target_duration_sec: number; created_at: string;
  updated_at: string; operation_type: 'generate' | 'scene_edit' | 'content_revision' | 'visual_revision' | 'media_revision'; parent_job_id?: string | null;
  parent_attempt_no?: number | null; scene_id?: string | null; attempts?: Attempt[];
  resolved_video_form?: string | null; input_analysis?: {status: string; reason: string; selected_form: string; selection_mode: string; retained_units: number; excluded_units: number; issues: {kind:string; message:string; blocking:boolean; source_quotes?:string[]}[] } | null;
  voice_id?:string; video_speed?:number;
  creative_intent?: Partial<CreativeIntent>; revision_mode?: 'source'|'narration'|null;
  video_form?: 'knowledge_cards' | 'process_diagram' | 'information_motion' | 'auto'; duration_mode?: 'fixed' | 'content';
};
type Artifact = {name: string; content_type: string; bytes: number; download_url: string};
type SceneImage = {scene_id: string; bytes: number; width: number; height: number; sha256: string; image_url: string};
type Config = {ready: boolean; provider: string; model: string; checks: Record<string, boolean>; blockers: string[]; draw_things?: {ready: boolean; endpoint: string; error?: string | null}};
type InputDocument = {document_id: string; filename: string; text: string; format: string; char_count: number; warnings: string[]; source_edited?: boolean};
type Page = {kind: 'new'} | {kind: 'jobs'} | {kind: 'job'; id: string};

const DEFAULT_SOURCE = `主题：通信噪声如何影响信息传输。\n事实材料：发送端通过通信信道把信息传给接收端。通信噪声是传输过程中对信号产生的干扰。受到噪声影响后，接收端收到的信息可能与发送端发出的信息不同。噪声带来差错的可能性，但不表示每次传输都会出错。\n表达边界：面向普通成年人，用通俗中文解释发送、信道受扰、接收三个环节。不要增加概率、统计数字、具体设备、未经材料支持的纠错方法或现实案例。可用抽象图形演示信号受到干扰，但要说明这是概念示意。`;
const STAGES = [
  ['PLAN', '整理讲稿'], ['VOICE_ALIGN', '生成旁白与对齐'], ['STORYBOARD', '规划分镜'],
  ['SCENE_IMAGES', '生成场景图'], ['BUILD_IR', '校验时间线'], ['RENDER', '渲染动画'], ['QUALITY_CHECK', '检查音视频'],
] as const;
const STATUS_TEXT: Record<Status, string> = {QUEUED: '等待中', RUNNING: '生成中', COMPLETED: '已完成', FAILED: '失败', CANCELLED: '已取消'};
const STATUS_CLASS: Record<Status, string> = {QUEUED: 'queued', RUNNING: 'running', COMPLETED: 'completed', FAILED: 'failed', CANCELLED: 'cancelled'};

function parseRoute(): Page {
  const hash = window.location.hash || '#new';
  if (hash === '#jobs') return {kind: 'jobs'};
  const match = hash.match(/^#job\/([0-9a-f-]{36})$/i);
  if (match) return {kind: 'job', id: match[1]};
  return {kind: 'new'};
}

async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(path, {headers: {'Content-Type': 'application/json'}, ...options});
  const value = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = value?.detail;
    throw new Error(typeof detail === 'string' ? detail : (detail?.message || `请求失败 (${response.status})`));
  }
  return value as T;
}

function formatTime(iso: string) {
  return new Intl.DateTimeFormat('zh-CN', {month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit'}).format(new Date(iso));
}
function formatBytes(bytes: number) { return bytes >= 1024 * 1024 ? `${(bytes / 1024 / 1024).toFixed(1)} MB` : `${Math.max(1, Math.round(bytes / 1024))} KB`; }

function Icon({name}: {name: 'spark' | 'film' | 'arrow' | 'plus' | 'download' | 'refresh' | 'stop' | 'check' | 'shield' | 'file' | 'search' | 'layers' | 'settings' | 'sound'}) {
  const paths: Record<string, React.ReactNode> = {
    file: <><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6M8 13h8M8 17h6"/></>,
    search: <><circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 5 5"/></>,
    layers: <><path d="m12 3 10 5-10 5L2 8zM2 12l10 5 10-5M2 16l10 5 10-5"/></>,
    settings: <><path d="M4 7h16M4 17h16"/><circle cx="8" cy="7" r="3" fill="white"/><circle cx="16" cy="17" r="3" fill="white"/></>,
    sound: <><path d="m11 5-6 4H2v6h3l6 4zM15 8a6 6 0 0 1 0 8M18 5a10 10 0 0 1 0 14"/></>,
    spark: <><path d="m12 2 1.9 6.1L20 10l-6.1 1.9L12 18l-1.9-6.1L4 10l6.1-1.9L12 2Z"/><path d="m19 15 .9 2.1L22 18l-2.1.9L19 21l-.9-2.1L16 18l2.1-.9L19 15Z"/></>,
    film: <><rect x="3" y="4" width="18" height="16" rx="3"/><path d="M7 4v16M17 4v16M3 9h4M3 15h4M17 9h4M17 15h4"/></>,
    arrow: <><path d="M5 12h14M13 6l6 6-6 6"/></>,
    plus: <><path d="M12 5v14M5 12h14"/></>,
    download: <><path d="M12 3v12m0 0 4-4m-4 4-4-4"/><path d="M5 17v3h14v-3"/></>,
    refresh: <><path d="M20 7v5h-5M4 17v-5h5"/><path d="M5.7 9A7 7 0 0 1 18 6l2 6M4 12l2 6a7 7 0 0 0 12.3-3"/></>,
    stop: <><rect x="6" y="6" width="12" height="12" rx="2"/></>,
    check: <><path d="m5 12 4 4L19 6"/></>,
    shield: <><path d="M12 22s8-3.8 8-10V5l-8-3-8 3v7c0 6.2 8 10 8 10Z"/><path d="m9 12 2 2 4-4"/></>,
  };
  return <svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">{paths[name]}</svg>;
}

function Badge({status, needsInput = false}: {status: Status; needsInput?: boolean}) {
  return <span className={`status-badge ${needsInput ? 'needs-input' : STATUS_CLASS[status]}`}><i/>{needsInput ? '待补充' : STATUS_TEXT[status]}</span>;
}

type JobCollection = {items: Job[]; total: number; summary?: {total: number; completed: number; running: number; needs_input: number}};
type Draft = {title: string; source: string; inputDocument: InputDocument | null; voiceId: string; videoSpeed: number; duration: number | 'content'; videoForm: 'auto' | 'process_diagram' | 'information_motion'; creativeIntent: CreativeIntent};
const DRAFT_KEY = 'autovideo.creation-draft.v1';
function readDraft(): Partial<Draft> {
  try {
    const v = JSON.parse(localStorage.getItem(DRAFT_KEY) || '{}');
    if (!v || typeof v !== 'object') return {};
    return {
      title: typeof v.title === 'string' ? v.title : '', source: typeof v.source === 'string' ? v.source : '',
      inputDocument: v.inputDocument && typeof v.inputDocument.document_id === 'string' && typeof v.inputDocument.text === 'string' && Array.isArray(v.inputDocument.warnings) ? v.inputDocument : null,
      voiceId: typeof v.voiceId === 'string' ? v.voiceId : 'zf_xiaoxiao',
      videoSpeed: [.5, 1, 1.5, 2].includes(v.videoSpeed) ? v.videoSpeed : 1,
      duration: v.duration === 'content' || [15, 18, 20, 30, 45, 60, 90].includes(v.duration) ? v.duration : 'content',
      videoForm: ['auto', 'process_diagram', 'information_motion'].includes(v.videoForm) ? v.videoForm : 'auto',
    };
  } catch { return {}; }
}
function JobBadge({job}: {job: Job}) { return <Badge status={job.status} needsInput={job.error_code === 'INPUT_REQUIRES_CLARIFICATION'}/>; }
function operationLabel(job: Job) {
  return job.operation_type === 'visual_revision' ? '画面修订' : job.operation_type === 'media_revision' ? '声音与速度' : job.operation_type === 'scene_edit' ? '背景修订' : job.operation_type === 'content_revision' ? (job.revision_mode === 'narration' ? '讲稿修订' : '素材修订') : formLabel(job.resolved_video_form || job.video_form);
}
function timecode(seconds: number) { const n = Math.max(0, Math.floor(seconds)); return `${Math.floor(n / 60)}:${String(n % 60).padStart(2, '0')}`; }

function App() {
  const [initial] = useState(readDraft);
  const [page, setPage] = useState<Page>(parseRoute);
  const [config, setConfig] = useState<Config | null>(null);
  const [recent, setRecent] = useState<Job[]>([]);
  const [collection, setCollection] = useState<JobCollection>({items: [], total: 0});
  const [summary, setSummary] = useState({total: 0, completed: 0, running: 0, needs_input: 0});
  const [query, setQuery] = useState(''), [filter, setFilter] = useState(''), [offset, setOffset] = useState(0);
  const [listLoading, setListLoading] = useState(false);
  const [job, setJob] = useState<Job | null>(null);
  const [artifacts, setArtifacts] = useState<Artifact[]>([]), [sceneImages, setSceneImages] = useState<SceneImage[]>([]);
  const [title, setTitle] = useState(initial.title || ''), [source, setSource] = useState(initial.source || '');
  const [inputDocument, setInputDocument] = useState<InputDocument | null>(initial.inputDocument || null);
  const [voiceId, setVoiceId] = useState(initial.voiceId || 'zf_xiaoxiao'), [videoSpeed, setVideoSpeed] = useState(initial.videoSpeed || 1);
  const [duration, setDuration] = useState<number | 'content'>(initial.duration || 'content');
  const [videoForm, setVideoForm] = useState<'auto' | 'process_diagram' | 'information_motion'>(initial.videoForm || 'auto');
  const [creativeIntent, setCreativeIntent] = useState<CreativeIntent>(readIntent);
  const [uploading, setUploading] = useState(false), [uploadError, setUploadError] = useState('');
  const [loading, setLoading] = useState(false), [configLoading, setConfigLoading] = useState(false);
  const [error, setError] = useState(''), [toast, setToast] = useState(''), [draftStatus, setDraftStatus] = useState('');
  const listRequest = useRef(0);
  useEffect(() => {
    try { localStorage.setItem(DRAFT_KEY, JSON.stringify({title, source, inputDocument, voiceId, videoSpeed, duration, videoForm})); setDraftStatus(source || title ? '草稿已保存在此浏览器' : ''); }
    catch { setDraftStatus('草稿未能保存，请保留一份正文副本'); }
  }, [title, source, inputDocument, voiceId, videoSpeed, duration, videoForm]);
  useEffect(() => { rememberIntent(creativeIntent); }, [creativeIntent]);
  useEffect(() => { if (!toast) return; const t = setTimeout(() => setToast(''), 4000); return () => clearTimeout(t); }, [toast]);

  const reloadRecent = useCallback(async () => {
    try { const data = await api<JobCollection>('/api/jobs?limit=3'); setRecent(data.items); if (data.summary) setSummary(data.summary); }
    catch (e) { setError(e instanceof Error ? e.message : '无法读取作品'); }
  }, []);
  const reloadConfig = useCallback(async () => {
    setConfigLoading(true);
    try { setConfig(await api<Config>('/api/config/status')); } catch { setConfig(null); }
    finally { setConfigLoading(false); }
  }, []);
  const reloadList = useCallback(async () => {
    const request = ++listRequest.current;
    setListLoading(true);
    try {
      const params = new URLSearchParams({limit: '12', offset: String(offset), q: query, status: filter});
      const data = await api<JobCollection>(`/api/jobs?${params}`);
      if (request !== listRequest.current) return;
      if (offset > 0 && !data.items.length) { setOffset(Math.max(0, offset - 12)); return; }
      setCollection(data); if (data.summary) setSummary(data.summary);
    } catch (e) { if (request === listRequest.current) setError(e instanceof Error ? e.message : '无法读取任务列表'); }
    finally { if (request === listRequest.current) setListLoading(false); }
  }, [offset, query, filter]);
  const reloadJob = useCallback(async (id: string) => {
    try {
      const value = await api<Job>(`/api/jobs/${id}`);
      const active = parseRoute(); if (active.kind !== 'job' || active.id !== id) return;
      setJob(value);
      if (value.status === 'COMPLETED') {
        const [files, images] = await Promise.all([api<{items: Artifact[]}>(`/api/jobs/${id}/artifacts`), api<{items: SceneImage[]}>(`/api/jobs/${id}/scene-images`)]);
        const active = parseRoute(); if (active.kind !== 'job' || active.id !== id) return;
        setArtifacts(files.items); setSceneImages(images.items);
      } else { setArtifacts([]); setSceneImages([]); }
    } catch (e) { const active = parseRoute(); if (active.kind === 'job' && active.id === id) setError(e instanceof Error ? e.message : '无法读取任务'); }
  }, []);
  useEffect(() => {
    const onRoute = () => { setPage(parseRoute()); setError(''); window.scrollTo(0, 0); };
    window.addEventListener('hashchange', onRoute); void reloadConfig(); void reloadRecent();
    return () => window.removeEventListener('hashchange', onRoute);
  }, [reloadConfig, reloadRecent]);
  useEffect(() => {
    if (page.kind !== 'jobs') return;
    setListLoading(true);
    const timer = setTimeout(() => { void reloadList(); }, 180);
    return () => clearTimeout(timer);
  }, [page.kind, reloadList]);
  useEffect(() => {
    if (page.kind !== 'job') { setJob(null); setArtifacts([]); setSceneImages([]); return; }
    setJob(null); setArtifacts([]); setSceneImages([]);
    const tick = () => { void reloadJob(page.id); };
    tick(); const timer = setInterval(tick, 2500);
    return () => clearInterval(timer);
  }, [page, reloadJob]);
  useEffect(() => { if (job?.status === 'COMPLETED' || job?.status === 'FAILED' || job?.status === 'CANCELLED') void reloadRecent(); }, [job?.job_id, job?.status, reloadRecent]);

  async function uploadDocument(file?: File) {
    if (!file) return; setUploading(true); setUploadError('');
    try {
      const body = new FormData(); body.append('file', file);
      const response = await fetch('/api/input-documents', {method: 'POST', body});
      const data = await response.json(); if (!response.ok) throw new Error(data.detail?.message || '文档读取失败');
      setInputDocument(data); setSource(data.text); const name = file.name.replace(/\.[^.]+$/, '').trim(); setTitle((name.length >= 2 ? name : name + ' · 视频').slice(0, 80));
    } catch (e) { setUploadError(e instanceof Error ? e.message : '文档读取失败'); }
    finally { setUploading(false); }
  }
  function useExample() {
    if ((source.trim() || title.trim()) && !window.confirm('载入示例将替换当前素材和标题，是否继续？')) return;
    setSource(DEFAULT_SOURCE); setTitle('通信噪声如何影响信息传输'); setInputDocument(null); setUploadError('');
  }
  function clearDraft() {
    if ((source.trim() || title.trim()) && !window.confirm('清空当前素材草稿？已生成的视频不会受到影响。')) return;
    setSource(''); setTitle(''); setInputDocument(null); setUploadError('');
  }
  async function submit(e: React.FormEvent) {
    e.preventDefault(); if (uploading || loading) return; setError(''); setLoading(true);
    try {
      const result = await api<{job_id: string}>('/api/jobs', {method: 'POST', body: JSON.stringify({title: title.trim() || undefined, source_text: source, input_document_id: inputDocument?.document_id, target_duration_sec: duration === 'content' ? 60 : duration, duration_mode: duration === 'content' ? 'content' : 'fixed', theme: 'minimal_dark', voice_id: voiceId, video_speed: videoSpeed, video_form: videoForm, creative_intent: creativeIntent, use_draw_things: false})});
      setToast('已开始制作，离开页面也会继续生成'); window.location.hash = `#job/${result.job_id}`; void reloadRecent();
    } catch (e) { setError(e instanceof Error ? e.message : '无法创建任务'); }
    finally { setLoading(false); }
  }
  async function cancel() {
    if (!job) return;
    try { await api(`/api/jobs/${job.job_id}/cancel`, {method: 'POST'}); setToast('正在停止制作'); void reloadJob(job.job_id); }
    catch (e) { setError(e instanceof Error ? e.message : '取消失败'); }
  }
  async function retry() {
    if (!job) return;
    try { await api(`/api/jobs/${job.job_id}/retry`, {method: 'POST'}); setToast('已重新加入制作队列'); void reloadJob(job.job_id); }
    catch (e) { setError(e instanceof Error ? e.message : '重试失败'); }
  }
  async function regenerateScene(sceneId: string, promptExtra: string): Promise<boolean> {
    if (!job) return false;
    try { const data = await api<{job_id: string}>(`/api/jobs/${job.job_id}/scene-images/${sceneId}/regenerate`, {method: 'POST', body: JSON.stringify({prompt_extra: promptExtra})}); window.location.hash = `#job/${data.job_id}`; return true; }
    catch (e) { setError(e instanceof Error ? e.message : '无法修订背景'); return false; }
  }
  const blocked = uploading || loading || !config?.ready || source.trim().length < 50 || (duration !== 'content' && source.length > 2000) || (videoForm === 'process_diagram' && !config?.checks.diagram_layout);
  const disabledReason = uploading ? '正在读取文档' : !config?.ready ? '请先检查生成服务' : source.trim().length < 50 ? '请提供至少 50 字的素材' : duration !== 'content' && source.length > 2000 ? '完整材料请选择按内容组织' : videoForm === 'process_diagram' && !config?.checks.diagram_layout ? '流程组件尚未就绪' : '生成过程中可离开此页';
  const titleText = page.kind === 'jobs' ? '作品库' : page.kind === 'job' ? '视频工作区' : '新建视频';
  return <div className="app-shell">
    <a className="skip-link" href="#main-content" onClick={e=>{e.preventDefault();document.getElementById("main-content")?.focus();}}>跳至主要内容</a>
    <aside className="sidebar">
      <a className="brand" href="#new" aria-label="AutoVideo 创作台"><span className="brand-mark"><Icon name="spark"/></span><span><b>AutoVideo</b><small>视频创作工作台</small></span></a>
      <div className="side-label">工作空间</div>
      <nav className="side-nav" aria-label="主导航">
        <a href="#new" aria-current={page.kind === 'new' ? 'page' : undefined} className={page.kind === 'new' ? 'active' : ''}><Icon name="plus"/><span>新建视频</span></a>
        <a href="#jobs" aria-current={page.kind === 'jobs' ? 'page' : undefined} className={page.kind === 'jobs' ? 'active' : ''}><Icon name="film"/><span>作品库</span><b className="nav-count">{summary.total}</b></a>
      </nav>
      <div className="sidebar-bottom"><div className="local-status"><i className={config?.ready ? 'online' : ''}/><span>{config?.ready ? '生成服务已就绪' : configLoading ? '正在检查服务' : '生成服务待检查'}</span></div><small>本地工作区 <span>v0.1</span></small></div>
    </aside>
    <main className="main-area" id="main-content" tabIndex={-1}>
      <header className="topbar"><div className="crumb"><span>工作空间</span><span>/</span><strong>{titleText}</strong></div><span className="local-chip"><Icon name="shield"/>本地保存</span></header>
      <div className="page-wrap">
        {error && <div className="alert error" role="alert"><div><strong>操作未完成</strong><p>{error}</p></div><button className="icon-button" onClick={() => setError('')} aria-label="关闭提示">×</button></div>}
        {toast && <div className="toast" role="status"><Icon name="check"/>{toast}</div>}
        {page.kind === 'new' && <>
          <section className="page-heading"><div><div className="eyebrow">从素材开始创作</div><h1>把内容，变成一支视频。</h1><p>提供素材，自动完成讲稿、配音和画面。每一步都可以继续修改。</p></div><a href="#jobs" className="text-link">查看我的作品 <Icon name="arrow"/></a></section>
          {(!config || !config.ready) && <div className="alert warning"><div><strong>{configLoading ? '正在检查生成环境…' : '生成服务暂未就绪'}</strong><p>{config ? '请检查文本服务密钥、配音与渲染依赖。' : '正在连接本机服务，请稍候。'}</p><details><summary>查看配置详情</summary><p>{config?.blockers.join('、') || '尚未收到服务状态'}</p></details></div><button type="button" className="secondary-button" onClick={() => void reloadConfig()} disabled={configLoading}>{configLoading ? '检查中…' : '重新检测'}</button></div>}
          <form className="create-layout" id="create-video" onSubmit={submit}>
            <section className="create-card">
              <div className="card-title"><div><h2>创作素材</h2><p>粘贴文字，或从本地文档开始。</p></div><button type="button" className="text-link" onClick={useExample}>载入示例 <Icon name="arrow"/></button></div>
              <div className="material-body">
                <label className="field-label" htmlFor="title">视频标题 <span>可选 · 留空自动拟定</span></label>
                <input id="title" className="text-input" value={title} onChange={e => setTitle(e.target.value)} placeholder="为这次创作起个名字" maxLength={80}/>
                <div className="document-upload" onDragOver={e => e.preventDefault()} onDrop={e => {e.preventDefault(); if (!uploading && !loading) void uploadDocument(e.dataTransfer.files?.[0]);}}>
                  <div className="document-upload-heading"><Icon name="file"/><div><strong>{inputDocument ? inputDocument.filename : '将文档拖到这里，或点击上传'}</strong><span>Word · 文字型 PDF · TXT · Markdown</span></div><label className={`secondary-button upload-button ${uploading ? 'disabled' : ''}`} htmlFor="input-document">{uploading ? '读取中…' : '选择文件'}</label><input id="input-document" type="file" accept=".docx,.pdf,.txt,.md" disabled={uploading || loading} onChange={e => {void uploadDocument(e.target.files?.[0]); e.target.value = '';}}/></div>
                  {inputDocument && <div className="document-summary"><span>提取 {inputDocument.char_count.toLocaleString()} 字{source !== inputDocument.text ? ' · 正文已修改' : ''}</span><button type="button" className="text-link" onClick={() => setInputDocument(null)}>解除关联，保留正文</button>{inputDocument.warnings.map((v, i) => <p key={i}>{v}</p>)}</div>}
                  {uploadError && <p className="revision-error" role="alert">{uploadError} 原有正文已保留。</p>}
                </div>
                <label className="field-label source-label" htmlFor="source">正文素材 <span>{source.length.toLocaleString()} 字{duration !== 'content' ? ' / 最多 2,000 字' : ''}</span></label>
                <textarea id="source" className="source-input" value={source} onChange={e => setSource(e.target.value)} placeholder={'粘贴你想讲清楚的内容。\n\n可以是一篇文章、工作笔记或一份流程说明。系统会根据素材组织讲解，选择适合的表现形式。'} minLength={50}/>
                <div className="draft-line"><span role="status">{draftStatus || '正文至少 50 字 · 支持完整材料自动分章'}</span>{(source || title) && <button type="button" className="text-link muted" onClick={clearDraft}>清空草稿</button>}</div>
                {duration !== 'content' && source.length > 2000 && <p className="revision-error">当前素材超过指定时长模式的范围，请切换为“按内容自动组织”。正文不会被截断。</p>}
                <CreativeFields value={creativeIntent} onChange={setCreativeIntent} id="create"/>
              </div>
            </section>
            <aside className="creation-options">
              <section className="options-card"><div className="card-title"><div><h2>成片设置</h2><p>使用推荐设置即可开始。</p></div><span className="small-tag">1080p</span></div>
                <fieldset className="form-presets"><legend>表现形式</legend>{(['auto', 'process_diagram', 'information_motion'] as const).map(value => <label className={`form-option ${value === videoForm ? 'selected' : ''}`} key={value}><input type="radio" name="video-form" checked={value === videoForm} onChange={() => setVideoForm(value)}/><span className={`preset-art ${value}`} aria-hidden="true">{value === 'auto' ? <Icon name="spark"/> : <><i/><i/><i/></>}</span><span><b>{value === 'auto' ? '自动选择' : value === 'process_diagram' ? '流程动态图解' : '简洁信息动效'}</b><small>{value === 'auto' ? '按素材匹配表现形式' : value === 'process_diagram' ? '步骤、分支与关系' : '观点、要点与对比'}</small></span>{value === 'auto' && <em>推荐</em>}</label>)}</fieldset>
                <details className="output-settings"><summary><span>声音与时长</span><small>{duration === 'content' ? '按内容组织' : `约 ${duration} 秒`} · {videoSpeed} 倍</small></summary><div className="settings-grid"><label className="setting"><span className="setting-label">旁白声音与口音</span><VoiceSelector value={voiceId} onChange={setVoiceId}/></label><label className="setting"><span className="setting-label">视频时长</span><span className="select-wrap"><select value={duration} onChange={e => setDuration(e.target.value === 'content' ? 'content' : Number(e.target.value))}><option value="content">按内容自动组织（推荐）</option>{[15, 18, 20, 30, 45, 60, 90].map(n => <option key={n} value={n}>约 {n} 秒</option>)}</select><span className="chevron">⌄</span></span><small>自动模式按完整配音决定时长。</small></label><label className="setting"><span className="setting-label">整片导出倍速</span><SpeedSelector value={videoSpeed} onChange={setVideoSpeed}/><small>声音、字幕和画面同步调整。</small></label></div></details>
                <div className="creation-actions"><button className="primary-button generate-button" type="submit" disabled={blocked}>{loading ? <><span className="spinner"/>正在创建…</> : <><Icon name="spark"/>开始生成视频 <Icon name="arrow"/></>}</button><p>{disabledReason}</p></div>
                <p className="privacy-note"><Icon name="shield"/><span>正文将发送至 DeepSeek 组织内容；文档原件、配音和视频保存在本机。</span></p>
              </section>
              <p className="quiet-note">上传单个文档最大 32 MB。扫描件暂不支持识别；可直接粘贴文字继续创作。</p>
            </aside>
          </form>
          {recent.length > 0 && <section className="recent-section"><div className="section-line"><h2>最近作品</h2><a className="text-link" href="#jobs">全部作品 <Icon name="arrow"/></a></div><div className="recent-grid">{recent.map(item => <JobRow key={item.job_id} job={item}/>)}</div></section>}
        </>}
        {page.kind === 'jobs' && <section className="jobs-page">
          <div className="page-heading"><div><div className="eyebrow">你的创作空间</div><h1>作品库</h1><p>{summary.total} 个制作记录，{summary.completed} 个已完成。素材与每个版本均保存在本机。</p></div><a className="primary-button" href="#new"><Icon name="plus"/>新建视频</a></div>
          <div className="library-toolbar"><label className="search-field"><Icon name="search"/><span className="sr-only">搜索作品标题或任务编号</span><input value={query} onChange={e => {setQuery(e.target.value); setOffset(0);}} placeholder="搜索标题或任务编号"/></label><label className="library-filter"><span className="sr-only">按状态筛选</span><select value={filter} onChange={e => {setFilter(e.target.value); setOffset(0);}}><option value="">全部状态</option><option value="COMPLETED">已完成</option><option value="RUNNING">生成中</option><option value="QUEUED">等待中</option><option value="NEEDS_INPUT">待补充素材</option><option value="FAILED">生成失败</option><option value="CANCELLED">已取消</option></select></label><button className="icon-button" aria-label="刷新作品库" onClick={() => void reloadList()}><Icon name="refresh"/></button></div>
          <div className="jobs-table" aria-busy={listLoading}><div className="table-head"><span>作品</span><span>创建时间</span><span>时长模式</span><span>状态</span><span/></div>{collection.items.map(item => <JobRow key={item.job_id} job={item} detailed/>)}{!collection.items.length && <div className="empty-state"><Icon name="film"/><h2>{listLoading ? '正在读取作品…' : query || filter ? '没有找到对应作品' : '从第一支视频开始'}</h2><p>{query || filter ? '试试其他关键词，或调整筛选条件。' : '提供素材，我们来完成讲稿、配音和画面。'}</p>{!query && !filter && <a href="#new" className="primary-button">新建视频</a>}</div>}</div>
          <div className="pagination"><span>{listLoading ? '正在更新作品…' : collection.total ? `${offset + 1}–${Math.min(offset + 12, collection.total)} / 共 ${collection.total} 个记录` : '0 个记录'}</span><div><button className="secondary-button" disabled={offset === 0 || listLoading} onClick={() => setOffset(v => Math.max(0, v - 12))}>上一页</button><button className="secondary-button" disabled={offset + 12 >= collection.total || listLoading} onClick={() => setOffset(v => v + 12)}>下一页</button></div></div>
        </section>}
        {page.kind === 'job' && <JobDetail key={page.id} job={job} artifacts={artifacts} sceneImages={sceneImages} drawThingsReady={Boolean(config?.draw_things?.ready)} onCancel={cancel} onRetry={retry} onRegenerateScene={regenerateScene} onRefresh={() => job && reloadJob(job.job_id)}/>}
      </div>
      <footer className="main-footer"><span>AutoVideo <b>本地版</b></span><span>素材到成片，留在同一个工作台。</span></footer>
    </main>
  </div>;
}
function JobRow({job, detailed = false}: {job: Job; detailed?: boolean}) {
  return <a className={`job-row ${detailed ? 'table-row' : 'recent-work'}`} href={`#job/${job.job_id}`}><span className="job-title-cell"><span className={`job-thumb ${job.status.toLowerCase()}`}><Icon name="film"/></span><span><b>{job.title}</b><small>{operationLabel(job)} · {job.job_id.slice(0, 8)}</small></span></span>{detailed ? <><span className="table-date">{formatTime(job.created_at)}</span><span className="table-duration">{job.duration_mode === 'content' ? '按内容组织' : `约 ${job.target_duration_sec} 秒`}</span><JobBadge job={job}/><span className="row-arrow"><Icon name="arrow"/></span></> : <div className="recent-meta"><JobBadge job={job}/><small>{formatTime(job.created_at)}</small></div>}</a>;
}

function JobDetail({job, artifacts, sceneImages, drawThingsReady, onCancel, onRetry, onRegenerateScene, onRefresh}: {
  job: Job | null; artifacts: Artifact[]; sceneImages: SceneImage[]; drawThingsReady: boolean;
  onCancel: () => void; onRetry: () => void; onRegenerateScene: (id: string, text: string) => Promise<boolean>; onRefresh: () => void;
}) {
  const player = useRef<HTMLVideoElement>(null);
  const [tab, setTab] = useState('visual'), [actualDuration, setActualDuration] = useState(0);
  const [poster, setPoster] = useState('');
  if (!job) return <div className="loading-state" role="status"><span className="spinner dark"/>正在打开视频工作区…</div>;
  const video = artifacts.find(a => a.name === 'video.mp4'), srt = artifacts.find(a => a.name === 'captions.srt');
  const chapter = artifacts.find(a => a.name === 'chapters.json');
  const completed = job.status === 'COMPLETED' && !!video;
  const needsInput = job.error_code === 'INPUT_REQUIRES_CLARIFICATION';
  const terminal = ['COMPLETED', 'FAILED', 'CANCELLED'].includes(job.status);
  const editableVisual = ['process_diagram', 'information_motion'].includes(job.resolved_video_form || job.video_form || '');
  const seek = (seconds: number) => { if (player.current) player.current.currentTime = seconds; };
  const tabs = [{id: 'visual', label: '画面', icon: 'film'}, {id: 'script', label: '讲稿', icon: 'file'}, {id: 'audio', label: '声音', icon: 'sound'}, {id: 'versions', label: '版本', icon: 'layers'}] as const;
  return <section className="detail-page">
    <a className="back-link" href="#jobs">← 返回作品库</a>
    <div className="detail-heading"><div><div className="detail-meta"><JobBadge job={job}/><span>{operationLabel(job)}</span><span>{formatTime(job.created_at)}</span></div><h1>{job.title}</h1><p>{completed ? `${actualDuration ? timecode(actualDuration) + ' · ' : ''}1080p · ${voiceLabel(job.voice_id)} · ${job.video_speed || 1} 倍导出` : '制作进度和每次尝试都会保存在本机。'}</p></div><div className="detail-actions">
      {['RUNNING', 'QUEUED'].includes(job.status) && <button className="secondary-button" disabled={job.cancel_requested} onClick={onCancel}><Icon name="stop"/>{job.cancel_requested ? '正在停止…' : '取消制作'}</button>}
      {job.status === 'FAILED' && job.retryable && !needsInput && <button className="primary-button" onClick={onRetry}><Icon name="refresh"/>重新制作</button>}
      {completed && <a className="primary-button" href={video.download_url} download><Icon name="download"/>下载视频</a>}
      <button className="icon-button" onClick={onRefresh} aria-label="刷新当前任务"><Icon name="refresh"/></button>
    </div></div>
    {job.parent_job_id && <div className="revision-banner"><span><Icon name="layers"/>当前为修订版本，原版已保留。</span><a href={`#job/${job.parent_job_id}`}>查看原版 <Icon name="arrow"/></a></div>}
    {needsInput && <div className="alert warning"><div><strong>请补充素材中的关键信息</strong>{job.input_analysis?.issues.filter(i => i.blocking).map((i, n) => <p key={n}>{i.message.replace(/\bu(\d+)\b/g, '第 $1 条原文')}</p>)}<p>在下方修改素材后重新制作，当前记录会保留。</p></div><Icon name="file"/></div>}
    {job.status === 'FAILED' && !needsInput && <div className="alert error" role="alert"><div><strong>本次制作未完成</strong><p>{job.error_message || '请检查素材或生成配置，再重新制作。'}</p><details><summary>错误详情</summary>{job.error_code}</details></div></div>}
    {completed ? <>
      <div className="workspace-layout">
        <div className="player-column"><section className="video-card"><div className="video-topline"><span><i className="completed-dot"/>成片预览</span>{srt && <a className="download-link" href={srt.download_url} download><Icon name="download"/>下载字幕</a>}</div><video ref={player} aria-label="视频预览" className="video-player" controls playsInline poster={poster || undefined} src={video.download_url} onLoadedMetadata={e => setActualDuration(e.currentTarget.duration)}/><div className="video-caption"><span>选择场景或章节，可定位到相应画面。</span><span>声音、字幕与动画同步</span></div></section>
          {chapter && <ChapterList url={chapter.download_url} onSeek={seek}/>}
          <div className="workspace-note"><Icon name="shield"/><span>修改后生成独立版本，原视频始终保留。</span></div>
        </div>
        <aside className="editor-panel" aria-label="视频修改工具"><div className="editor-heading"><div><span className="eyebrow">继续完善作品</span><h2>视频编辑</h2></div><span className="small-tag">保留原版</span></div>
          <div className="workspace-tabs" role="tablist" aria-label="编辑类型" onKeyDown={e => {
            const items = Array.from(e.currentTarget.querySelectorAll<HTMLButtonElement>('[role=tab]')); const i = items.indexOf(document.activeElement as HTMLButtonElement);
            const n = e.key === 'ArrowRight' ? (i + 1) % items.length : e.key === 'ArrowLeft' ? (i - 1 + items.length) % items.length : e.key === 'Home' ? 0 : e.key === 'End' ? items.length - 1 : -1;
            if (n >= 0) { e.preventDefault(); items[n].click(); items[n].focus(); }
          }}>{tabs.map(t => <button key={t.id} type="button" role="tab" id={`tab-${t.id}`} aria-controls={`panel-${t.id}`} aria-selected={tab === t.id} tabIndex={tab === t.id ? 0 : -1} className={tab === t.id ? 'active' : ''} onClick={() => setTab(t.id)}><Icon name={t.icon}/>{t.label}</button>)}</div>
          <div className="editor-body">
            <div id="panel-visual" role="tabpanel" aria-labelledby="tab-visual" hidden={tab !== 'visual'}>{editableVisual ? <SceneRevisionEditor key={`scenes-${job.job_id}`} job={job} onSeek={seek} onPoster={setPoster}/> : sceneImages.length ? <BackgroundEditor images={sceneImages} ready={drawThingsReady} onRegenerate={onRegenerateScene}/> : <div className="empty-state"><Icon name="film"/><h3>当前版本可通过素材修订调整</h3><button className="secondary-button" onClick={() => setTab('script')}>查看素材与讲稿</button></div>}</div>
            <div id="panel-script" role="tabpanel" aria-labelledby="tab-script" hidden={tab !== 'script'}><RevisionEditor key={`content-${job.job_id}`} job={job} startOpen/><InputDocumentSource jobId={job.job_id}/></div>
            <div id="panel-audio" role="tabpanel" aria-labelledby="tab-audio" hidden={tab !== 'audio'}>{editableVisual ? <MediaRevisionEditor key={`media-${job.job_id}`} job={job}/> : <p className="quiet-note">当前历史版本可通过素材修订制作新版。</p>}</div>
            <div id="panel-versions" role="tabpanel" aria-labelledby="tab-versions" hidden={tab !== 'versions'}><VersionHistory job={job}/></div>
          </div>
        </aside>
      </div>
    </> : <div className="incomplete-layout"><section className="progress-card"><div className="progress-top"><span className={`progress-symbol ${job.status === 'RUNNING' ? 'working' : ''}`}><Icon name={needsInput ? 'file' : job.status === 'FAILED' ? 'film' : 'spark'}/></span><div><h2>{needsInput ? '等待素材补充' : job.status === 'RUNNING' ? stageLabel(job.active_stage) : job.status === 'QUEUED' ? '已加入制作队列' : job.status === 'CANCELLED' ? '制作已取消' : '暂未生成成片'}</h2><p>{job.status === 'RUNNING' ? '正在后台制作，关闭页面不会中断。' : job.status === 'QUEUED' ? '前序任务完成后，将自动开始制作。' : needsInput ? '补充或修正信息后，即可继续制作新版。' : '可以调整素材后制作新版，已有记录会继续保留。'}</p></div></div><JobTimeline job={job}/></section>{terminal && <RevisionEditor key={job.job_id} job={job} startOpen/>}</div>}
    <details className="technical-details"><summary><span><Icon name="settings"/>制作详情与文件</span><span>{job.job_id.slice(0, 8)} <span aria-hidden="true">⌄</span></span></summary><div className="technical-grid"><section><h3>制作信息</h3><dl className="job-properties"><div><dt>表现形式</dt><dd>{formLabel(job.resolved_video_form || job.video_form)}</dd></div><div><dt>时长模式</dt><dd>{job.duration_mode === 'content' ? '按内容自动组织' : `约 ${job.target_duration_sec} 秒`}</dd></div><div><dt>声音</dt><dd>{voiceLabel(job.voice_id)}</dd></div><div><dt>导出倍速</dt><dd>{job.video_speed || 1} 倍</dd></div><div><dt>素材长度</dt><dd>{job.source_char_count.toLocaleString()} 字</dd></div><div><dt>制作尝试</dt><dd>{job.attempt_count} 次</dd></div></dl>
      {job.input_analysis && <details className="input-analysis"><summary>素材整理与形式选择</summary><p>{job.input_analysis.reason}</p><p>保留 {job.input_analysis.retained_units} 个内容单元，排除 {job.input_analysis.excluded_units} 个重复或粘贴噪声单元。</p>{job.input_analysis.issues.map((i, n) => <p key={n}>{i.message}</p>)}</details>}
      <h3>处理记录</h3><JobTimeline job={job}/>
    </section><section><h3>文件与运行记录</h3><p className="quiet-note">视频和字幕可直接下载，其余文件用于追溯制作过程。</p>{artifacts.filter(a => a.name !== 'video.mp4').map(a => <a className="artifact-row" key={a.name} href={a.download_url} download><span className="artifact-type">{a.name.endsWith('.srt') ? 'SRT' : a.name.endsWith('.txt') ? 'TXT' : 'JSON'}</span><span><b>{artifactLabel(a.name)}</b><small>{a.name}</small></span><span className="artifact-size">{formatBytes(a.bytes)}</span><Icon name="download"/></a>)}{!artifacts.length && <p className="quiet-note">制作完成后可在这里下载文件。</p>}</section></div></details>
  </section>;
}
function JobTimeline({job}: {job: Job}) {
  const latest = job.attempts?.[job.attempts.length - 1];
  return <ol className="stage-list">{STAGES.map(([key, label]) => {
    const stage = latest?.stages.find(s => s.stage === key);
    let state = stage?.status || 'PENDING';
    let text = state === 'SUCCEEDED' ? '已完成' : state === 'SKIPPED' ? '已复用 / 无需执行' : state === 'RUNNING' ? '进行中' : state === 'FAILED' || state === 'INTERRUPTED' ? '未完成' : '待处理';
    if (job.error_code === 'INPUT_REQUIRES_CLARIFICATION') { state = key === 'PLAN' ? 'NEEDS_INPUT' : 'SKIPPED'; text = key === 'PLAN' ? '待补充' : '未执行'; }
    else if (job.status === 'COMPLETED' && !['SUCCEEDED', 'SKIPPED'].includes(state)) {
      state = 'SKIPPED'; text = ['visual_revision', 'media_revision', 'scene_edit'].includes(job.operation_type) && ['PLAN', 'VOICE_ALIGN', 'STORYBOARD'].includes(key) ? '已复用 / 本次未重做' : key === 'SCENE_IMAGES' ? '无需执行' : '已结束';
    } else if (['FAILED', 'CANCELLED'].includes(job.status) && state === 'RUNNING') { state = 'INTERRUPTED'; text = '已停止'; }
    return <li className={`stage-item ${state.toLowerCase()}`} key={key}><span className="stage-dot"/><span>{label}</span><small>{text}</small></li>;
  })}</ol>;
}
function VersionHistory({job}: {job: Job}) {
  const [children, setChildren] = useState<Job[]>([]), [error, setError] = useState('');
  useEffect(() => {let active = true; void api<{items: Job[]}>(`/api/jobs/${job.job_id}/revisions`).then(v => {if (active) setChildren(v.items);}).catch(e => {if (active) setError(e.message);}); return () => {active = false;};}, [job.job_id]);
  return <section className="version-history"><div className="card-title"><div><h2>版本记录</h2><p>每次修改都是独立版本，随时返回查看。</p></div></div>{job.parent_job_id && <a className="parent-version" href={`#job/${job.parent_job_id}`}><Icon name="layers"/><span>查看此版本的原版</span><Icon name="arrow"/></a>}<div className="current-version"><span className="version-dot"/><div><b>当前版本</b><p>{operationLabel(job)} · {formatTime(job.created_at)}</p></div><JobBadge job={job}/></div>{children.map(child => <a className="version-row" key={child.job_id} href={`#job/${child.job_id}`}><span className="version-dot"/><span><b>{operationLabel(child)}</b><small>{child.title}</small><small>{formatTime(child.created_at)}</small></span><JobBadge job={child}/><Icon name="arrow"/></a>)}{!children.length && <p className="quiet-note">基于此版本制作的修订版，会显示在这里。</p>}{error && <p className="revision-error">{error}</p>}</section>;
}
function BackgroundEditor({images, ready, onRegenerate}: {images: SceneImage[]; ready: boolean; onRegenerate: (id: string, text: string) => Promise<boolean>}) {
  const [guidance, setGuidance] = useState<Record<string, string>>({}), [pending, setPending] = useState<string | null>(null);
  return <section className="scene-preview-card"><div className="card-title"><div><h2>场景背景</h2><p>单独重做背景，原版保留。</p></div></div><div className="scene-preview-grid">{images.map(image => <div key={image.scene_id} className="scene-preview-item"><a href={image.image_url} target="_blank" rel="noreferrer"><img src={image.image_url} alt={`${sceneLabel(image.scene_id)}背景`}/></a><details><summary>{sceneLabel(image.scene_id)} · 重做背景</summary><label><span className="field-label">画面要求（可选）</span><textarea className="text-input" value={guidance[image.scene_id] || ''} maxLength={300} onChange={e => setGuidance({...guidance, [image.scene_id]: e.target.value})}/></label><button className="secondary-button" disabled={!ready || !!pending} onClick={async () => {setPending(image.scene_id); if (!await onRegenerate(image.scene_id, guidance[image.scene_id] || '')) setPending(null);}}>{pending === image.scene_id ? '正在提交…' : '生成背景修订版'}</button>{!ready && <p className="quiet-note">请先打开 Draw Things 本地 API，再刷新服务状态。</p>}</details></div>)}</div></section>;
}

type CreativeIntent = {audience: string; purpose: string; tone: string; focus: string};
const emptyIntent: CreativeIntent = {audience:'', purpose:'', tone:'', focus:''};
function readIntent(): CreativeIntent {
  try {const v=JSON.parse(localStorage.getItem('autovideo.creative-intent') || '{}');return Object.fromEntries(Object.keys(emptyIntent).map(k=>[k,typeof v?.[k]==='string'?v[k]:''])) as CreativeIntent;} catch {return {...emptyIntent};}
}
function rememberIntent(value: CreativeIntent) {try {localStorage.setItem('autovideo.creative-intent',JSON.stringify(value));} catch {}}
function CreativeFields({value,onChange,id}: {value:CreativeIntent;onChange:(v:CreativeIntent)=>void;id:string}) {
  const suggestions = {audience:['普通读者','初学者','已有基础的读者','团队同事'],purpose:['知识讲解','内部培训','产品介绍','工作汇报'],tone:['通俗自然','简洁直接','专业严谨','轻松亲切']};
  const labels = {audience:'给谁看',purpose:'用于什么',tone:'表达语气'};
  return <details className="creative-preferences"><summary>创作偏好（可选）</summary><p>不填时自动决定。可以选择建议，也可以直接输入自己的要求；你的输入会保存并用于后续修订。</p><div className="settings-grid">{(['audience','purpose','tone'] as const).map(key=><label className="setting" key={key}><span className="setting-label">{labels[key]}</span><input className="text-input" list={`${id}-${key}`} value={value[key]} placeholder="自动，也可自定义" onChange={e=>onChange({...value,[key]:e.target.value})}/><datalist id={`${id}-${key}`}>{suggestions[key].map(v=><option value={v} key={v}/>)}</datalist></label>)}<label className="setting"><span className="setting-label">希望强调什么</span><textarea className="text-input" rows={2} value={value.focus} placeholder="可留空，也可写下希望强调或保留的内容" onChange={e=>onChange({...value,focus:e.target.value})}/></label></div></details>;
}

type EditableContent = {title:string;source_text:string;narration_text:string|null;creative_intent:Partial<CreativeIntent>};
function RevisionEditor({job, startOpen = false}: {job: Job; startOpen?: boolean}) {
  const [open, setOpen] = useState(startOpen), [draft, setDraft] = useState<EditableContent | null>(null);
  const [mode, setMode] = useState<'source' | 'narration'>('source');
  const [source, setSource] = useState(''), [narration, setNarration] = useState(''), [title, setTitle] = useState('');
  const [intent, setIntent] = useState<CreativeIntent>({...emptyIntent});
  const [loading, setLoading] = useState(false), [pending, setPending] = useState(false), [error, setError] = useState('');
  const [loadedFor, setLoadedFor] = useState('');
  useEffect(() => {
    if (!open || loadedFor === job.job_id) return;
    let active = true; setLoading(true); setError('');
    void api<EditableContent>(`/api/jobs/${job.job_id}/editable-content`).then(data => {
      if (!active) return; setDraft(data); setSource(data.source_text); setNarration(data.narration_text || '');
      setTitle((data.title.slice(0, 72) + ' · 修订版').slice(0, 80)); setIntent({...emptyIntent, ...data.creative_intent});
      setMode(data.narration_text && ['process_diagram', 'information_motion'].includes(job.resolved_video_form || job.video_form || '') ? 'narration' : 'source'); setLoadedFor(job.job_id);
    }).catch(e => {if (active) setError(e.message);}).finally(() => {if (active) setLoading(false);});
    return () => {active = false;};
  }, [open, job.job_id, loadedFor]);
  async function submit(e: React.FormEvent) {
    e.preventDefault(); setPending(true); setError('');
    try { const data = await api<{job_id: string}>(`/api/jobs/${job.job_id}/revisions`, {method: 'POST', body: JSON.stringify({mode, text: mode === 'source' ? source : narration, title, creative_intent: intent})}); rememberIntent(intent); window.location.hash = `#job/${data.job_id}`; }
    catch (e) { setError(e instanceof Error ? e.message : '无法制作修订版'); setPending(false); }
  }
  return <section className="artifact-card revision-editor"><div className="card-title"><div><h2>素材与讲稿</h2><p>修改后制作新版，当前视频会保留。</p></div>{!startOpen && <button className="secondary-button" onClick={() => setOpen(v => !v)}>{open ? '收起' : '打开修改'}</button>}</div>{open && <>{loading && <p className="quiet-note">正在读取内容…</p>}{error && <p className="revision-error" role="alert">{error}</p>}{draft && <form onSubmit={submit}><label className="field-label" htmlFor={`revision-title-${job.job_id}`}>新版标题</label><input id={`revision-title-${job.job_id}`} className="text-input" value={title} onChange={e => setTitle(e.target.value)} minLength={2} maxLength={80} required/><fieldset className="revision-modes"><legend className="sr-only">修改方式</legend><label><input type="radio" name={`revision-${job.job_id}`} checked={mode === 'narration'} disabled={!draft.narration_text || !['process_diagram', 'information_motion'].includes(job.resolved_video_form || job.video_form || '')} onChange={() => setMode('narration')}/>修改讲稿</label><label><input type="radio" name={`revision-${job.job_id}`} checked={mode === 'source'} onChange={() => setMode('source')}/>修改原始素材</label></fieldset><p className="revision-hint">{mode === 'narration' ? '逐字采用你的讲稿重新配音，画面按修订内容重新组织。' : '重新整理素材，组织讲稿和画面。'}</p><label className="field-label" htmlFor={`revision-text-${job.job_id}`}>{mode === 'source' ? '原始素材' : '旁白讲稿'}</label><textarea id={`revision-text-${job.job_id}`} className="source-input revision-text" value={mode === 'source' ? source : narration} onChange={e => mode === 'source' ? setSource(e.target.value) : setNarration(e.target.value)} minLength={mode === 'source' ? 50 : 1} required/><CreativeFields value={intent} onChange={setIntent} id={`revision-${job.job_id}`}/><div className="form-footer"><span>原版与本次修订分别保留</span><button className="primary-button" disabled={pending || !title.trim() || !(mode === 'source' ? source : narration).trim()}>{pending ? '正在创建…' : '生成内容修订版'}</button></div></form>}</>}</section>;
}

function InputDocumentSource({jobId}: {jobId:string}) {
  const [record,setRecord]=useState<{document:InputDocument|null;download_url?:string}|null>(null);
  const [error,setError]=useState('');
  useEffect(()=>{let active=true;void api<{document:InputDocument|null;download_url?:string}>(`/api/jobs/${jobId}/input-document`).then(v=>{if(active)setRecord(v);}).catch(e=>{if(active)setError(e.message);});return()=>{active=false;};},[jobId]);
  if(error)return <p className="revision-error">文档来源读取失败：{error}</p>;
  if(!record?.document)return null;
  return <section className="artifact-card document-source"><div className="card-title"><div><h2>输入文档</h2><p>{record.document.filename} · 提取 {record.document.char_count.toLocaleString()} 字{record.document.source_edited?' · 已按修改后的正文制作':''}</p></div><a className="download-link" href={record.download_url} download>下载原文档</a></div>{record.document.warnings.map((v,i)=><p key={i}>{v}</p>)}</section>;
}


function voiceLabel(id?:string) {return ({zf_xiaoxiao:'小晓 · 普通话',zf_xiaobei:'小贝 · 普通话',zf_xiaoni:'小妮 · 普通话',zf_xiaoyi:'小伊 · 普通话',zm_yunjian:'云健 · 普通话',zm_yunxi:'云希 · 普通话',zm_yunxia:'云夏 · 普通话',zm_yunyang:'云扬 · 普通话',qwen_eric:'Eric · 四川口音',qwen_dylan:'Dylan · 北京口音',qwen_vivian:'Vivian · 普通话',qwen_serena:'Serena · 普通话',qwen_uncle_fu:'福叔 · 普通话'} as Record<string,string>)[id||'zf_xiaoxiao']||'普通话';}
type Voice = {id:string;label:string;engine:string;accent:string;available:boolean};
type VideoScene = {scene_id:string;component:string;title:string;start_ms:number;end_ms:number;narration:string;fields:{path:string;label:string;value:string}[];preview_url:string};
function VoiceSelector({value,onChange}:{value:string;onChange:(value:string)=>void}) {
  const [voices,setVoices]=useState<Voice[]>([]),[sample,setSample]=useState(''),[error,setError]=useState(''),[loading,setLoading]=useState(false);
  useEffect(()=>{void api<{items:Voice[]}>('/api/voices').then(v=>setVoices(v.items)).catch(()=>{});},[]);
  const player=useRef<HTMLAudioElement>(null);
  async function listen(){setError('');setLoading(true);const url=`/api/voices/${value}/sample`;setSample(url);try{const audio=player.current;if(audio){audio.src=url;await audio.play();}}catch{setError('试听未能播放，请检查本地声音模型或稍后重试。');}finally{setLoading(false);}}
  return <div className="voice-selector"><span className="select-wrap"><select aria-label="旁白声音与口音" value={value} onChange={e=>{player.current?.pause();setSample('');onChange(e.target.value);}}>{voices.length?voices.map(v=><option key={v.id} value={v.id} disabled={!v.available}>{v.label}{v.available?'':'（模型未就绪）'}</option>):<option value="zf_xiaoxiao">小晓 · 普通话女声</option>}</select><span className="chevron">⌄</span></span><button className="text-link" type="button" disabled={loading} onClick={()=>void listen()}>{loading?'正在准备试听…':'试听所选声音'}</button><audio aria-label="声音试听" ref={player} controls hidden={!sample} onError={()=>setError('试听生成失败，请选择其他声音或稍后重试。')}/>{error&&<small className="revision-error">{error}</small>}</div>;
}
function SpeedSelector({value,onChange}:{value:number;onChange:(value:number)=>void}) {
  return <span className="select-wrap"><select aria-label="整片导出倍速" value={value} onChange={e=>onChange(Number(e.target.value))}>{[.5,1,1.5,2].map(s=><option key={s} value={s}>{s} 倍{s===1?'（正常）':''}</option>)}</select><span className="chevron">⌄</span></span>;
}
function MediaRevisionEditor({job}:{job:Job}) {
  const [voice,setVoice]=useState(job.voice_id||'zf_xiaoxiao'),[speed,setSpeed]=useState(job.video_speed||1),[pending,setPending]=useState(false),[error,setError]=useState('');
  async function submit(e:React.FormEvent){e.preventDefault();setPending(true);setError('');try{const v=await api<{job_id:string}>(`/api/jobs/${job.job_id}/media-revisions`,{method:'POST',body:JSON.stringify({voice_id:voice,video_speed:speed})});window.location.hash=`#job/${v.job_id}`;}catch(e){setError(e instanceof Error?e.message:'修订失败');setPending(false);}}
  return <section className="artifact-card media-editor"><div className="card-title"><div><h2>声音与导出速度</h2><p>讲稿保持原文；声音变化时重新配音，字幕和画面同步。旧版本保留。</p></div></div><form onSubmit={submit}><div className="settings-grid"><label className="setting"><span className="setting-label">旁白声音与口音</span><VoiceSelector value={voice} onChange={setVoice}/></label><label className="setting"><span className="setting-label">整片导出倍速</span><SpeedSelector value={speed} onChange={setSpeed}/><small>导出视频实际生效，保留音调，声音、字幕与动画一起调整。</small></label></div>{error&&<p className="revision-error">{error}</p>}<div className="form-footer"><span>只改速度时复用原配音</span><button className="primary-button" disabled={pending||(voice===(job.voice_id||'zf_xiaoxiao')&&speed===(job.video_speed||1))}>{pending?'正在创建…':'生成声音与速度修订版'}</button></div></form></section>;
}
function SceneRevisionEditor({job,onSeek,onPoster}:{job:Job;onSeek:(seconds:number)=>void;onPoster?:(url:string)=>void}) {
  const [scenes,setScenes]=useState<VideoScene[]>([]),[selected,setSelected]=useState<string|null>(null),[fields,setFields]=useState<Record<string,string>>({}),[mode,setMode]=useState<'text'|'ai'>('text'),[instruction,setInstruction]=useState(''),[error,setError]=useState(''),[pending,setPending]=useState(false);
  useEffect(()=>{void api<{items:VideoScene[]}>(`/api/jobs/${job.job_id}/scenes`).then(v=>{setScenes(v.items);onPoster?.(v.items[0]?.preview_url||'');}).catch(e=>setError(e.message));},[job.job_id,onPoster]);
  function choose(s:VideoScene){if(scene && ((mode==='ai'&&instruction.trim()) || (mode==='text'&&scene.fields.some(f=>fields[f.path]!==f.value))) && !window.confirm('当前场景有未提交的修改。切换场景会放弃这些修改，是否继续？'))return;setSelected(s.scene_id);setFields(Object.fromEntries(s.fields.map(f=>[f.path,f.value])));setInstruction('');setError('');onSeek((s.start_ms+Math.min(1000,(s.end_ms-s.start_ms)/2))/1000);}
  const scene=scenes.find(s=>s.scene_id===selected);
  async function submit(e:React.FormEvent){e.preventDefault();if(!scene)return;setPending(true);setError('');try{const changes=Object.fromEntries(scene.fields.filter(f=>fields[f.path]!==f.value).map(f=>[f.path,fields[f.path]]));const v=await api<{job_id:string}>(`/api/jobs/${job.job_id}/visual-revisions`,{method:'POST',body:JSON.stringify({scene_id:scene.scene_id,changes:mode==='text'?changes:{},instruction:mode==='ai'?instruction:''})});window.location.hash=`#job/${v.job_id}`;}catch(e){setError(e instanceof Error?e.message:'场景修订失败');setPending(false);}}
  return <section className="artifact-card scene-revision-editor"><div className="card-title"><div><h2>场景预览与局部修订</h2><p>选择需要调整的一幕，保留原讲稿、配音和其他场景。</p></div><span>{scenes.length} 幕</span></div><div className="scene-jump"><span>共 {scenes.length} 幕</span><select aria-label="选择场景" value={selected||''} onChange={e=>{const s=scenes.find(s=>s.scene_id===e.target.value);if(s)choose(s);}}><option value="">选择要调整的场景</option>{scenes.map((s,i)=><option key={s.scene_id} value={s.scene_id}>{i+1}. {s.title}</option>)}</select></div><div className="scene-thumb-grid">{scenes.map((s,i)=><button aria-pressed={selected===s.scene_id} className={selected===s.scene_id?'selected':''} key={s.scene_id} type="button" onClick={()=>choose(s)}><img loading="lazy" src={s.preview_url} alt={`场景 ${i+1} 预览`}/><b>{i+1}. {s.title}</b><small>{(s.start_ms/1000).toFixed(1)}–{(s.end_ms/1000).toFixed(1)} 秒</small></button>)}</div>{!scene&&<p className="quiet-note">选择一幕，可直接改文字，或描述希望 AI 调整的画面。</p>}{scene&&<form onSubmit={submit}><div className="revision-modes"><label><input type="radio" checked={mode==='text'} onChange={()=>setMode('text')}/>直接修改画面文字</label><label><input type="radio" checked={mode==='ai'} onChange={()=>setMode('ai')}/>AI 调整这一幕</label></div><details className="scene-narration"><summary>这一幕的旁白（保留）</summary><p>{scene.narration}</p></details>{mode==='text'?<div className="scene-fields">{scene.fields.map(f=><label key={f.path}><span className="field-label">{f.label}</span><textarea className="text-input" rows={2} value={fields[f.path]??f.value} onChange={e=>setFields({...fields,[f.path]:e.target.value})}/></label>)}</div>:<label><span className="field-label">希望如何调整画面</span><textarea className="source-input scene-guidance" value={instruction} onChange={e=>setInstruction(e.target.value)} placeholder="例如：将背景说明放在标题下，只把三个分类做成卡片。或：突出无设备时进入等待名单的分支。"/></label>}<p className="revision-hint">修改画面不会改写旁白。涉及旁白内容变化，请切换到“讲稿”标签。</p><div className="form-footer"><span>生成独立版本，原版保留</span><button className="primary-button" disabled={pending||(mode==='ai'?!instruction.trim():!scene.fields.some(f=>fields[f.path]!==f.value))}>{pending?'正在创建…':'生成这一幕的修订版'}</button></div></form>}{error&&<p className="revision-error">{error}</p>}</section>;
}

function ChapterList({url, onSeek}: {url: string; onSeek: (seconds: number) => void}) {
  const [chapters, setChapters] = useState<{id: string; title: string; start_ms: number}[]>([]);
  useEffect(() => {let active = true; void api<{chapters: typeof chapters}>(url).then(v => {if(active)setChapters(v.chapters);}).catch(() => {});return()=>{active=false;};}, [url]);
  if (!chapters.length) return null;
  return <details className="chapter-card"><summary>章节导航 <span>{chapters.length} 章 · 点击展开 ⌄</span></summary>{chapters.map((c, i)=><button type="button" className="artifact-row" key={c.id} onClick={()=>onSeek(c.start_ms/1000)}><span className="artifact-type">{timecode(c.start_ms/1000)}</span><span><b>{i+1}. {c.title}</b></span><Icon name="arrow"/></button>)}</details>;
}

function formLabel(form?: string | null) { return ({auto:'自动选择中', process_diagram:'流程动态图解', information_motion:'简洁信息动效', knowledge_cards:'知识卡片与背景图'} as Record<string,string>)[form || ''] || '选择中'; }
function stageLabel(stage: string | null) { return STAGES.find(([key]) => key === stage)?.[1] || '准备生成'; }
function sceneLabel(sceneId: string) { return ({opening: '开场', mechanism: '内容展开', middle: '内容展开', note: '结尾提示', closing: '结尾'} as Record<string, string>)[sceneId] || sceneId; }
function artifactLabel(name: string) { return ({'video.mp4': '动画视频', 'captions.srt': '中文字幕', 'qa.json': '基础验收报告', 'manifest.json': '制品清单', 'storyboard.json': '分镜计划', 'scene-backgrounds.json': '本地场景图记录', 'scene-edit.json': '场景修订记录', 'video-ir.json': 'Video IR', 'model-cost.json': '模型用量', 'execution-log.txt': '执行日志', 'chapters.json': '章节与内容覆盖', 'content-outline.json': '完整内容纲要', 'narration.json': '最终讲稿', 'authored-narration.json': '用户修订讲稿记录', 'input-brief.json': '输入整理与形式选择', 'prepared-source.txt': '整理后的原文材料', 'information-plan.json': '信息分镜与来源', 'information-timing.json': '信息动效与旁白对应', 'diagram-plan.json': '流程内容与来源', 'diagram-layout.json': '自动布局记录', 'diagram-timing.json': '图解与旁白对应记录'} as Record<string, string>)[name] || name; }

createRoot(document.getElementById('root')!).render(<React.StrictMode><App/></React.StrictMode>);
