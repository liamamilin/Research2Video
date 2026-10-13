import React, {useCallback, useEffect, useMemo, useState} from 'react';
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
  updated_at: string; attempts?: Attempt[];
};
type Artifact = {name: string; content_type: string; bytes: number; download_url: string};
type SceneImage = {scene_id: string; bytes: number; width: number; height: number; sha256: string; image_url: string};
type Config = {ready: boolean; provider: string; model: string; checks: Record<string, boolean>; blockers: string[]; draw_things?: {ready: boolean; endpoint: string; error?: string | null}};
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

function Icon({name}: {name: 'spark' | 'film' | 'arrow' | 'plus' | 'download' | 'refresh' | 'stop' | 'check' | 'shield'}) {
  const paths: Record<string, React.ReactNode> = {
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

function Badge({status}: {status: Status}) { return <span className={`status-badge ${STATUS_CLASS[status]}`}><i/>{STATUS_TEXT[status]}</span>; }

function App() {
  const [page, setPage] = useState<Page>(parseRoute);
  const [config, setConfig] = useState<Config | null>(null);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [job, setJob] = useState<Job | null>(null);
  const [artifacts, setArtifacts] = useState<Artifact[]>([]);
  const [sceneImages, setSceneImages] = useState<SceneImage[]>([]);
  const [title, setTitle] = useState('通信噪声如何影响信息传输');
  const [source, setSource] = useState(DEFAULT_SOURCE);
  const [duration, setDuration] = useState(18);
  const [useDrawThings, setUseDrawThings] = useState(true);
  const [configLoading, setConfigLoading] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [toast, setToast] = useState('');

  const reloadJobs = useCallback(async () => {
    try { const result = await api<{items: Job[]}>('/api/jobs?limit=30'); setJobs(result.items); }
    catch (err) { setError(err instanceof Error ? err.message : '读取任务列表失败'); }
  }, []);

  const reloadConfig = useCallback(async () => {
    setConfigLoading(true);
    try { setConfig(await api<Config>('/api/config/status')); }
    catch { setConfig(null); }
    finally { setConfigLoading(false); }
  }, []);

  const reloadJob = useCallback(async (id: string) => {
    try {
      const value = await api<Job>(`/api/jobs/${id}`);
      setJob(value);
      if (value.status === 'COMPLETED') {
        const [collection, imageCollection] = await Promise.all([
          api<{items: Artifact[]}>(`/api/jobs/${id}/artifacts`),
          api<{items: SceneImage[]}>(`/api/jobs/${id}/scene-images`),
        ]);
        setArtifacts(collection.items);
        setSceneImages(imageCollection.items);
      } else { setArtifacts([]); setSceneImages([]); }
    } catch (err) { setError(err instanceof Error ? err.message : '读取任务失败'); }
  }, []);

  useEffect(() => {
    const onRoute = () => { setPage(parseRoute()); setError(''); };
    window.addEventListener('hashchange', onRoute);
    void reloadConfig();
    void reloadJobs();
    return () => window.removeEventListener('hashchange', onRoute);
  }, [reloadConfig, reloadJobs]);

  useEffect(() => {
    if (page.kind !== 'job') { setJob(null); setArtifacts([]); return; }
    let mounted = true;
    const tick = async () => {
      if (!mounted) return;
      await reloadJob(page.id);
      await reloadJobs();
    };
    void tick();
    const timer = window.setInterval(() => { void tick(); }, 1800);
    return () => { mounted = false; window.clearInterval(timer); };
  }, [page, reloadJob, reloadJobs]);

  const completedCount = useMemo(() => jobs.filter((item) => item.status === 'COMPLETED').length, [jobs]);

  async function submit(event: React.FormEvent) {
    event.preventDefault(); setError(''); setLoading(true);
    try {
      const result = await api<{job_id: string}>('/api/jobs', {method: 'POST', body: JSON.stringify({
        title: title.trim() || undefined, source_text: source, target_duration_sec: duration,
        theme: 'minimal_dark', voice_id: 'zf_xiaoxiao', use_draw_things: useDrawThings,
      })});
      setToast('任务已加入队列'); window.location.hash = `#job/${result.job_id}`;
      await reloadJobs();
    } catch (err) { setError(err instanceof Error ? err.message : '无法创建任务'); }
    finally { setLoading(false); }
  }

  async function cancel() {
    if (!job) return;
    setError('');
    try { await api(`/api/jobs/${job.job_id}/cancel`, {method: 'POST'}); setToast('已发送取消请求'); await reloadJob(job.job_id); }
    catch (err) { setError(err instanceof Error ? err.message : '取消失败'); }
  }

  async function retry() {
    if (!job) return;
    setError('');
    try { await api(`/api/jobs/${job.job_id}/retry`, {method: 'POST'}); setToast('已创建新的任务尝试'); await reloadJob(job.job_id); }
    catch (err) { setError(err instanceof Error ? err.message : '重试失败'); }
  }

  const activeTitle = page.kind === 'jobs' ? '任务库' : page.kind === 'job' ? '生成详情' : '创作台';

  return <div className="app-shell">
    <aside className="sidebar">
      <a className="brand" href="#new"><span className="brand-mark"><Icon name="spark"/></span><span><b>AutoVideo</b><small>知识动画工作台</small></span></a>
      <div className="side-label">工作空间</div>
      <nav className="side-nav">
        <a href="#new" className={page.kind === 'new' ? 'active' : ''}><span className="nav-icon"><Icon name="plus"/></span>新建视频</a>
        <a href="#jobs" className={page.kind === 'jobs' ? 'active' : ''}><span className="nav-icon"><Icon name="film"/></span>任务列表<span className="nav-count">{jobs.length}</span></a>
      </nav>
      <div className="sidebar-spacer"/>
      <div className="side-status"><span className={`status-dot ${config?.ready ? 'online' : 'offline'}`}/><span>{config?.ready ? '本机生成服务已就绪' : '需要检查生成配置'}</span><span className="side-status-arrow">›</span></div>
      <div className="sidebar-footer"><span>本地优先 · 单机模式</span><span>G3 · 服务化验证</span></div>
    </aside>

    <main className="main-area">
      <header className="topbar"><div className="crumb"><span>AutoVideo</span><b>/</b><strong>{activeTitle}</strong></div><div className="topbar-right"><span className="local-chip"><i/>本地工作区</span><div className="avatar">AV</div></div></header>
      <div className="page-wrap">
        {error && <div className="alert error"><strong>遇到问题</strong><span>{error}</span><button onClick={() => setError('')} aria-label="关闭">×</button></div>}
        {toast && <div className="toast" onAnimationEnd={() => setToast('')}>{toast}</div>}
        {page.kind === 'new' && <>
          <section className="welcome-row"><div><div className="eyebrow"><span className="eyebrow-line"/>自动生成 · 真实中文旁白</div><h1>把知识，讲成一段动画。</h1><p>粘贴一段内容，AutoVideo 会整理讲稿、生成配音，并编排对应的画面。</p></div><div className="welcome-stat"><span>已完成视频</span><b>{completedCount.toString().padStart(2, '0')}</b><small>保存在本机</small></div></section>
          {config && <section className={`config-banner ${config.ready ? 'ready' : 'blocked'}`}><span className="config-icon"><Icon name={config.ready ? 'shield' : 'film'}/></span><div><strong>{config.ready ? '生成能力已通过预检' : '生成配置尚未就绪'}</strong><p>{config.ready ? 'DeepSeek 文本规划 · Kokoro 中文配音 · Remotion 本地渲染' : `缺少：${config.blockers.join('、')}`}</p></div><span className="config-right">{config.provider} <i>·</i> {config.model}</span></section>}
          <div className="create-layout">
            <form className="create-card" onSubmit={submit}>
              <div className="card-heading"><span className="step-number">01</span><div><h2>描述你想讲的内容</h2><p>提供事实、背景和必要的表达边界</p></div><span className="required-note">必填</span></div>
              <label className="field-label" htmlFor="title">视频标题 <span>可留空，系统会自动拟定</span></label>
              <input id="title" className="text-input" value={title} onChange={(e) => setTitle(e.target.value)} placeholder="例如：为什么天空是蓝色的？" maxLength={80}/>
              <label className="field-label source-label" htmlFor="source">知识素材 <span>{source.length} / 2,000</span></label>
              <textarea id="source" className="source-input" value={source} onChange={(e) => setSource(e.target.value)} placeholder="粘贴中文知识素材。写下你知道的事实，系统会据此组织旁白和画面。" maxLength={2000} minLength={50}/>
              <div className="input-hint"><span className="hint-check"><Icon name="check"/></span>建议 200–1,200 字；保留原文出处有助于提升表达准确度。</div>
              <div className="divider"/>
              <div className="card-heading compact"><span className="step-number">02</span><div><h2>设置成片风格</h2><p>先使用经过真实音画验证的配置</p></div></div>
              <div className="settings-grid">
                <label className="setting"><span className="setting-label">目标时长</span><span className="select-wrap"><select value={duration} onChange={(e) => setDuration(Number(e.target.value))}><option value={12}>约 12 秒</option><option value={15}>约 15 秒</option><option value={18}>约 18 秒</option><option value={20}>约 20 秒</option></select><span className="chevron">⌄</span></span><small>当前验证范围 10–20 秒</small></label>
                <div className="setting"><span className="setting-label">视觉主题</span><div className="theme-choice"><span className="theme-swatch"/><span>深色知识图解</span><span className="selected-check"><Icon name="check"/></span></div><small>简洁排版 · 受控组件动画</small></div>
                <div className="setting"><span className="setting-label">中文旁白</span><div className="voice-choice"><span className="voice-wave">▂▅▃▇▅</span><span>小晓 · 普通话</span><span className="voice-play">▶</span></div><small>本机 Kokoro 语音合成</small></div>
              </div>
              <section className="visual-option">
                <label className="visual-option-main"><input type="checkbox" checked={useDrawThings} onChange={(event) => setUseDrawThings(event.target.checked)}/><span><strong>使用 Draw Things 本地场景图</strong><small>每个场景生成一张背景图，叠加受控图解与缓慢运镜</small></span><em>本机生成</em></label>
                <div className={`visual-connection ${config?.draw_things?.ready ? 'connected' : 'disconnected'}`}><span><i/>{config?.draw_things?.ready ? 'Draw Things 已连接 · HTTP 127.0.0.1:7860' : '尚未连接 Draw Things HTTP API'}</span><button type="button" onClick={() => void reloadConfig()} disabled={configLoading}>{configLoading ? '检测中…' : '重新检测'}</button></div>
                {useDrawThings && !config?.draw_things?.ready && <p className="visual-help">在 Draw Things「设置 → 高级」开启 HTTP API 服务器并选择 HTTP，然后重新检测。关闭此选项仍可生成原有图解动画。</p>}
              </section>
              <div className="privacy-note"><Icon name="shield"/><span>提交后，文本会发送给 DeepSeek API 生成 AI 讲稿与分镜，可能有遗漏或错误，请核对后使用；请勿提交无权外发的内容。启用场景图时，只把分镜意图发送给本机 Draw Things；配音、图像和视频渲染均在本机完成。</span></div>
              <div className="form-footer"><span><i className="secure-dot"/>不会执行模型生成的代码</span><button className="primary-button" type="submit" disabled={loading || !config?.ready || (useDrawThings && !config?.draw_things?.ready) || source.trim().length < 50}>{loading ? <><span className="spinner"/>正在创建…</> : <>开始生成 <span className="button-icon"><Icon name="arrow"/></span></>}</button></div>
            </form>
            <aside className="right-rail">
              <section className="rail-card preview-card"><div className="rail-title"><span>生成流程</span><span className="live-label"><i/>自动</span></div><div className="flow-preview"><div className="flow-node user-node"><span className="mini-icon">文</span><span>知识素材</span></div><div className="flow-connector"><i/></div><div className="flow-node ai-node"><span className="ai-mini"><Icon name="spark"/></span><span>AI 讲稿</span></div><div className="flow-connector"><i/></div><div className="flow-node voice-node"><span className="mini-wave">▂▅▃</span><span>中文配音</span></div><div className="flow-connector"><i/></div><div className="flow-node video-node"><span className="mini-play">▶</span><span>动画成片</span></div></div><div className="rail-note"><span>{useDrawThings ? '约 3–8 分钟' : '约 1–3 分钟'}</span><i/> 任务可在关闭浏览器后继续运行</div></section>
              <section className="rail-card safety-card"><div className="safety-icon"><Icon name="shield"/></div><div><strong>受控生成</strong><p>文本与模型结果均经过校验。动画由已登记组件绘制，不运行自由生成脚本。</p></div></section>
              <section className="rail-card recent-card"><div className="rail-title"><span>最近任务</span><a href="#jobs">全部 <Icon name="arrow"/></a></div>{jobs.length ? jobs.slice(0, 3).map((item) => <JobRow key={item.job_id} job={item}/>) : <div className="empty-mini">创建第一个任务后，会显示在这里。</div>}</section>
            </aside>
          </div>
        </>}

        {page.kind === 'jobs' && <section className="jobs-page"><div className="section-head"><div><div className="eyebrow"><span className="eyebrow-line"/>任务管理</div><h1>任务库</h1><p>所有生成记录与产物都保存在本机。</p></div><a className="primary-button small" href="#new">新建视频 <span className="button-icon"><Icon name="plus"/></span></a></div><div className="jobs-table"><div className="table-head"><span>视频任务</span><span>创建时间</span><span>时长</span><span>状态</span><span/></div>{jobs.length ? jobs.map((item) => <JobRow key={item.job_id} job={item} detailed/>) : <div className="empty-state"><span className="empty-icon"><Icon name="film"/></span><strong>还没有任务</strong><p>开始生成后，任务会保存在这里。</p><a href="#new" className="text-link">创建一个视频 <Icon name="arrow"/></a></div>}</div></section>}

        {page.kind === 'job' && <JobDetail job={job} artifacts={artifacts} sceneImages={sceneImages} onCancel={cancel} onRetry={retry} onRefresh={() => job && reloadJob(job.job_id)}/>}
      </div>
      <footer className="main-footer"><span>AutoVideo 本地版 <b>0.1</b></span><span>单 worker · SQLite 持久化 <i/> <a href="/api/health">服务状态</a></span></footer>
    </main>
  </div>;
}

function JobRow({job, detailed = false}: {job: Job; detailed?: boolean}) {
  return <a className={`job-row ${detailed ? 'table-row' : ''}`} href={`#job/${job.job_id}`}>
    <span className="job-title-cell"><span className={`job-thumb ${STATUS_CLASS[job.status]}`}><Icon name="film"/></span><span><b>{job.title}</b><small>{job.job_id.slice(0, 8)} · 尝试 {Math.max(1, job.attempt_count)}</small></span></span>
    {detailed && <><span className="table-date">{formatTime(job.created_at)}</span><span className="table-duration">10–20 秒</span><span><Badge status={job.status}/></span><span className="row-arrow"><Icon name="arrow"/></span></>}
    {!detailed && <span className="recent-state"><Badge status={job.status}/></span>}
  </a>;
}

function JobDetail({job, artifacts, sceneImages, onCancel, onRetry, onRefresh}: {
  job: Job | null; artifacts: Artifact[]; sceneImages: SceneImage[]; onCancel: () => void; onRetry: () => void; onRefresh: () => void;
}) {
  if (!job) return <div className="loading-state"><span className="spinner dark"/>正在读取任务…</div>;
  const latest = job.attempts?.[0];
  const video = artifacts.find((item) => item.name === 'video.mp4');
  const srt = artifacts.find((item) => item.name === 'captions.srt');
  const qa = artifacts.find((item) => item.name === 'qa.json');
  return <section className="detail-page">
    <a className="back-link" href="#jobs">← 返回任务列表</a>
    <div className="detail-heading"><div><div className="eyebrow"><span className="eyebrow-line"/>生成任务 · {job.job_id.slice(0, 8)}</div><h1>{job.title}</h1><div className="detail-meta"><Badge status={job.status}/><span>创建于 {formatTime(job.created_at)}</span><span>•</span><span>第 {Math.max(1, job.attempt_count)} 次尝试</span>{job.cancel_requested && <span className="cancel-pending">正在停止任务…</span>}</div></div><div className="detail-actions">{job.status === 'RUNNING' && <button className="secondary-button danger-outline" onClick={onCancel}><Icon name="stop"/>取消任务</button>}{job.status === 'FAILED' && job.retryable && <button className="secondary-button" onClick={onRetry}><Icon name="refresh"/>重试</button>}<button className="icon-button" onClick={onRefresh} title="刷新"><Icon name="refresh"/></button></div></div>
    {job.status === 'FAILED' && <div className="alert error detail-error"><strong>{job.error_code || '生成失败'}</strong><span>{job.error_message || '请检查配置后重试。'}</span></div>}
    <div className="detail-grid"><div className="detail-main">
      {job.status === 'COMPLETED' && video ? <section className="video-card"><div className="video-topline"><span><i className="completed-dot"/>成片已就绪</span><div><a className="download-link" href={video.download_url} download><Icon name="download"/>下载 MP4</a>{srt && <a className="download-link quiet" href={srt.download_url} download><Icon name="download"/>SRT</a>}</div></div><video className="video-player" controls playsInline src={video.download_url}/><div className="video-caption"><span>{job.title}</span><span>H.264 · 1080p · AAC</span></div></section> : <section className={`progress-card ${job.status.toLowerCase()}`}><div className="progress-top"><div><span className="progress-overline">{job.status === 'FAILED' ? '本次尝试未完成' : job.status === 'CANCELLED' ? '任务已取消' : '正在准备你的知识动画'}</span><h2>{job.status === 'QUEUED' ? '任务已加入队列' : job.status === 'RUNNING' ? stageLabel(job.active_stage) : job.status === 'FAILED' ? '暂时没有生成成片' : '你可以随时创建新的任务'}</h2><p>{job.status === 'RUNNING' ? '生成在本机后台持续进行；关闭此页面不会中断任务。' : job.status === 'QUEUED' ? '单 worker 会按顺序安全领取任务。' : job.status === 'FAILED' ? '阶段与错误已经保存；修复配置或素材后可以重试。' : '任务状态已保存到本机。'}</p></div><div className={`progress-orb ${job.status === 'RUNNING' ? 'spinning' : ''}`}><Icon name={job.status === 'COMPLETED' ? 'check' : job.status === 'FAILED' ? 'film' : 'spark'}/></div></div><div className="stage-list">{STAGES.map(([key, label], index) => {const stage = latest?.stages.find((item) => item.stage === key);const current = job.active_stage === key && job.status === 'RUNNING';const done = stage?.status === 'SUCCEEDED';const skipped = stage?.status === 'SKIPPED';const failed = stage?.status === 'FAILED' || (job.status === 'FAILED' && job.active_stage === key);return <div className={`stage-item ${done ? 'done' : ''} ${skipped ? 'stage-skipped' : ''} ${current ? 'current' : ''} ${failed ? 'stage-failed' : ''}`} key={key}><span className="stage-check">{done ? <Icon name="check"/> : skipped ? '—' : failed ? '!' : current ? <i/> : String(index + 1).padStart(2, '0')}</span><span className="stage-label">{label}{current && <small>处理中…</small>}</span>{stage?.duration_seconds != null && <small className="stage-duration">{stage.duration_seconds.toFixed(1)}s</small>}</div>;})}</div></section>}
      {job.status === 'COMPLETED' && <section className="success-summary"><span className="success-check"><Icon name="check"/></span><div><strong>生成与基础检查全部通过</strong><p>任务与制品已保存在本机。重新打开浏览器后仍可继续查看和下载。</p></div></section>}
      {job.status === 'COMPLETED' && sceneImages.length > 0 && <section className="scene-preview-card"><div className="card-title"><div><h2>场景背景预览</h2><p>模型背景在成片中经过柔化，并由受控图解覆盖</p></div><span>{sceneImages.length} 张</span></div><div className="scene-preview-grid">{sceneImages.map((image, index) => <a className="scene-preview-item" href={image.image_url} target="_blank" rel="noreferrer" key={image.scene_id}><img src={image.image_url} alt={`${sceneLabel(image.scene_id)}背景预览`}/><span><b>{sceneLabel(image.scene_id)}</b><small>{image.width} × {image.height} · {formatBytes(image.bytes)}</small></span></a>)}</div></section>}
      {job.status === 'COMPLETED' && <section className="artifact-card"><div className="card-title"><div><h2>交付文件</h2><p>本次生成的可下载产物</p></div><span>{artifacts.length} 个文件</span></div>{artifacts.map((item) => <a className="artifact-row" key={item.name} href={item.download_url} download><span className="artifact-type">{item.name.endsWith('.mp4') ? 'MP4' : item.name.endsWith('.srt') ? 'SRT' : 'JSON'}</span><span><b>{artifactLabel(item.name)}</b><small>{item.name}</small></span><span className="artifact-size">{formatBytes(item.bytes)}</span><Icon name="download"/></a>)}</section>}
      <section className="integrity-card"><div className="integrity-icon"><Icon name="shield"/></div><div><strong>任务状态已持久化</strong><p>SQLite 保存状态与阶段；每次重试使用独立工作区，不覆盖旧尝试。</p></div><span className="hash-short">SHA {job.source_sha256.slice(0, 12)}</span></section>
    </div><aside className="detail-side"><section className="side-panel"><div className="panel-head"><h3>任务信息</h3><span className="panel-menu">···</span></div><div className="info-line"><span>目标时长</span><b>{job.target_duration_sec} 秒</b></div><div className="info-line"><span>视觉主题</span><b>深色知识图解</b></div><div className="info-line"><span>旁白音色</span><b>小晓 · 普通话</b></div><div className="info-line"><span>素材长度</span><b>{job.source_char_count} 字</b></div><div className="info-line"><span>任务状态</span><Badge status={job.status}/></div></section><section className="side-panel"><div className="panel-head"><h3>处理记录</h3><span>{latest ? `尝试 ${latest.attempt_no}` : '排队中'}</span></div>{latest?.stages.map((stage) => <div className="history-line" key={stage.stage}><span className={`history-dot ${stage.status.toLowerCase()}`}/><span>{stageLabel(stage.stage)}</span><small>{stage.status === 'SUCCEEDED' ? '完成' : stage.status === 'SKIPPED' ? '已跳过' : stage.status === 'RUNNING' ? '进行中' : stage.status === 'FAILED' || stage.status === 'INTERRUPTED' ? '中断' : '待处理'}</small></div>) || <div className="empty-mini">任务开始后显示阶段状态。</div>}</section>{qa && <a className="report-link" href={qa.download_url}><span className="report-file">✓</span><span><b>基础验收报告</b><small>查看 QA JSON</small></span><Icon name="arrow"/></a>}</aside></div>
  </section>;
}

function stageLabel(stage: string | null) { return STAGES.find(([key]) => key === stage)?.[1] || '准备生成'; }
function sceneLabel(sceneId: string) { return ({opening: '开场', mechanism: '内容展开', middle: '内容展开', note: '结尾提示', closing: '结尾'} as Record<string, string>)[sceneId] || sceneId; }
function artifactLabel(name: string) { return ({'video.mp4': '动画视频', 'captions.srt': '中文字幕', 'qa.json': '基础验收报告', 'manifest.json': '制品清单', 'storyboard.json': '分镜计划', 'scene-backgrounds.json': '本地场景图记录', 'video-ir.json': 'Video IR', 'model-cost.json': '模型用量', 'execution-log.txt': '执行日志'} as Record<string, string>)[name] || name; }

createRoot(document.getElementById('root')!).render(<React.StrictMode><App/></React.StrictMode>);
