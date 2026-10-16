import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { CheckCircle2, Loader2, XCircle } from "lucide-react";
import {
  type AssistantAction as Action, getAssistantInput, submitAssistantInput,
  rollbackAssistantConfiguration, getAssistantBackground, getAssistantMaintenance,
  verifyAssistantInput, getAssistantConfigRecovery, revokeApiToken, type AssistantInputVerification,
} from "@/api";
import { ApiError } from "@/api/client";
import { scheduleSummary } from "@/components/CronBuilder";

function RollbackControl({ id, onRestored }: { id: string; onRestored?: () => void }) {
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);
  const undo = async () => {
    setBusy(true);
    try { await rollbackAssistantConfiguration(id); setMessage('已恢复变更前配置'); onRestored?.(); }
    catch (error) { setMessage(error instanceof Error ? error.message : '回退失败'); }
    finally { setBusy(false); }
  };
  return <div className="space-x-2"><button className="btn text-xs" disabled={busy || message === '已恢复变更前配置'} onClick={() => void undo()}>
    {busy && <Loader2 className="w-3 h-3 animate-spin" />}恢复变更前配置
  </button><span>{message}</span></div>;
}

export function AssistantConfigRecovery({ onRestored }: { onRestored?: () => void }) {
  const [id, setId] = useState<string | null>(null);
  useEffect(() => {
    let active = true;
    getAssistantConfigRecovery().then((value) => { if (active) setId(value.rollback_id); }).catch(() => { /* Other settings remain usable. */ });
    return () => { active = false; };
  }, []);
  if (!id) return null;
  return <div className="card p-3 text-xs space-y-2"><p>可恢复上一笔助手配置变更，无需模型连接。</p>
    <RollbackControl id={id} onRestored={onRestored} /></div>;
}

function ProtectedInput({ card }: { card: NonNullable<Action["input_card"]> }) {
  const [values, setValues] = useState<Record<string, string>>({});
  const [status, setStatus] = useState("loading");
  const [error, setError] = useState("");
  const [token, setToken] = useState("");
  const [rollbackId, setRollbackId] = useState('');
  const [refresh, setRefresh] = useState(0);
  const [verification, setVerification] = useState<AssistantInputVerification>();
  const [verifying, setVerifying] = useState(false);
  useEffect(() => {
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const result = await getAssistantInput(card.id);
        if (!active) return;
        setStatus(result.status); setRollbackId(result.result?.rollback_id || '');
        if (result.status === 'processing') timer = setTimeout(() => void poll(), 1500);
      } catch (e) { if (active) { setStatus('unavailable'); setError(e instanceof Error ? e.message : '未取得状态'); } }
    };
    void poll();
    return () => { active = false; clearTimeout(timer); };
  }, [card.id, refresh]);
  const verify = async () => {
    setVerifying(true); setError('');
    try { const result = await verifyAssistantInput(card.id); setVerification(result); setStatus(result.status); if (result.rollback_id) setRollbackId(result.rollback_id); }
    catch (e) { setError(e instanceof Error ? e.message : '核验未完成'); }
    finally { setVerifying(false); }
  };
  const revoke = async (id: number) => {
    setVerifying(true);
    try { await revokeApiToken(id); setVerification((previous) => previous && ({ ...previous, tokens: previous.tokens?.map((t) => t.id === id ? { ...t, revoked: true } : t) })); }
    catch (e) { setError(e instanceof Error ? e.message : '撤销未完成'); }
    finally { setVerifying(false); }
  };
  const submit = async () => {
    setStatus("processing"); setError("");
    try {
      const result = await submitAssistantInput(card.id, values);
      setValues({}); setStatus("completed");
      if (result.token) setToken(result.token);
      if (result.rollback_id) setRollbackId(result.rollback_id);
    } catch (e) {
      setValues({}); setStatus("failed");
      setError(e instanceof Error ? e.message : "提交未完成，请核验实际状态");
      try { const actual = await getAssistantInput(card.id); setStatus(actual.status); } catch { /* Keep an unknown result from being replayed. */ }
      setRefresh((n) => n + 1);
    }
  };
  return <div className="rounded-md border border-border p-3 space-y-3 mt-2">
    <p className="text-sm font-medium">{card.title}</p>
    <p className="text-xs text-text-muted">{card.description}</p>
    {status === "pending" && <form className="space-y-3" onSubmit={(event) => { event.preventDefault(); void submit(); }}>
      {card.fields.map((field) => <label key={field.name} className="block text-xs space-y-1">
        <span>{field.label}</span>
        <input className="input w-full" type="password" autoComplete="new-password" required
          aria-label={field.label} value={values[field.name] || ""} maxLength={12000}
          onChange={(event) => setValues({ ...values, [field.name]: event.target.value })} />
      </label>)}
      <button className="btn btn-primary text-xs" type="submit">提交此操作</button>
    </form>}
    {status === "loading" && <p className="text-xs text-text-muted">正在核验卡片状态…</p>}
    {status === "processing" && <p className="text-xs text-text-muted">已提交，正在处理；请勿重复提交。</p>}
    {status === 'interrupted' && <p className="text-xs text-warning">提交执行已中断，业务结果待核验；不会自动重复提交。</p>}
    {status === "completed" && <p className="text-xs text-success">操作已完成</p>}
    {token && <div className="space-y-2">
      <p className="text-xs">请现在保存 Token，关闭后无法再次显示。</p>
      <code className="block break-all select-all text-sm">{token}</code>
      <button className="btn text-xs" onClick={() => setToken("")}>隐藏 Token</button>
    </div>}
    {error && <p role="alert" className="text-xs text-danger">{error}</p>}
    {rollbackId && <RollbackControl id={rollbackId} />}
    {status === "failed" && <p className="text-xs text-text-muted">请先核验实际结果；需要时重新发起操作。</p>}
    {['processing', 'failed', 'interrupted', 'unavailable'].includes(status) && <button className="btn text-xs" disabled={verifying} onClick={() => void verify()}>核验实际结果</button>}
    {verification && <div className="text-xs space-y-2">
      <p>{verification.message}</p>
      {verification.tokens?.map((item) => <div key={item.id}>{item.name} · {item.created_at} · {item.revoked ? '已撤销' : '可用'}
        {!item.revoked && <button className="btn text-xs ml-2" disabled={verifying} onClick={() => void revoke(item.id)}>撤销这个 Token</button>}</div>)}
      {verification.configured !== undefined && <p>{verification.configured ? '密钥已配置' : '密钥尚未配置'}</p>}
      {verification.header_names && <p>当前请求头：{verification.header_names.join('、') || '无'}</p>}
      {verification.user && <p>{verification.user.username} · {verification.user.role} · {verification.user.disabled ? '已禁用' : '可用'}</p>}
    </div>}
  </div>;
}

function OperationProgress({ id, maintenance }: { id: string; maintenance?: boolean }) {
  const [text, setText] = useState("正在查询实际进度…");
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const result: { status: string; error?: string; progress?: { stage?: string; completed?: number; total?: number } } = maintenance ? await getAssistantMaintenance(id) : await getAssistantBackground(id);
        if (!active) return;
        const labels: Record<string, string> = { queued: "已排队", running: "进行中", waiting: "等待当前运行结束", applying: "正在执行", starting: "等待新服务通过健康检查", completed: "已完成", partial: "部分完成", failed: "失败", interrupted: "已中断" };
        let progress = "";
        if (result.progress) {
          progress = ` · ${result.progress.stage || ""} ${result.progress.completed ?? ""}${result.progress.total !== undefined ? ` / ${result.progress.total}` : ""}`;
        }
        setText((labels[result.status] || result.status) + progress + (result.error ? `：${result.error}` : ""));
        if (["queued", "running", "waiting", "applying", "starting"].includes(result.status)) {
          timer = setTimeout(() => void poll(), maintenance ? 4000 : 1500);
        }
      } catch (error) {
        if (active) setText(error instanceof ApiError && error.code === 'app_stopped'
          ? '应用已关停，自动查询已停止；重新打开应用后可核验维护回执。'
          : "暂未取得结果，恢复连接后请重新查询。");
      }
    };
    void poll();
    return () => { active = false; clearTimeout(timer); };
  }, [id, maintenance, retry]);
  return <div className="text-xs text-text-muted flex items-center gap-2 mt-2">
    <span>{text}</span><button className="text-accent hover:underline" onClick={() => setRetry((n) => n + 1)}>查询进度</button>
  </div>;
}

export function AssistantAction({ action }: { action: Action }) {
  const refs = (action.references || []).filter((ref) => ref.type !== 'job' || !action.job_name);
  return <li className="text-xs space-y-2">
    <div className="flex items-start gap-2">
      {action.ok ? <CheckCircle2 className="w-3.5 h-3.5 mt-0.5 text-success shrink-0" /> : <XCircle className="w-3.5 h-3.5 mt-0.5 text-danger shrink-0" />}
      <span className={action.ok ? "text-text-muted break-words" : "text-danger break-words"}>{action.message}</span>
      {action.ok && action.job_name && action.tool !== "delete_job" && <Link className="text-accent hover:underline whitespace-nowrap ml-auto" to={`/jobs/${encodeURIComponent(action.job_name)}`}>查看任务</Link>}
      {action.href && !action.job_name && <Link className="text-accent hover:underline ml-auto whitespace-nowrap" to={action.href}>查看详情</Link>}
    </div>
    {action.detail && <div className="rounded bg-bg-hover px-3 py-2 text-text-muted space-y-1">
      <p>{action.detail.enabled === false ? "已暂停" : "周期计划"} · {action.detail.timezone} · {action.detail.schedule ? scheduleSummary(action.detail.schedule) : ''}</p>
      {action.detail.executor_online !== undefined && <p>{action.detail.executor_online ? '执行器在线' : '执行器离线，计划已保留'}</p>}
      {!!action.detail.next_runs?.length && <p>{action.detail.enabled === false ? '启用后的时间预览' : '接下来'}：{action.detail.next_runs.join("、")}</p>}
      {action.detail.last_run && <p>最近运行：{({ success: '成功', failed: '失败', cancelled: '已取消', running: '运行中', queued: '已排队', retry: '等待重试', interrupted: '已中断', skipped: '已跳过', blocked: '已阻止' } as Record<string, string>)[action.detail.last_run.status] || action.detail.last_run.status}
        {action.detail.last_run.report && <Link className="text-accent ml-2" to={action.detail.last_run.report.href}>打开报告</Link>}</p>}
    </div>}
    {refs.length > 0 && <div className="flex flex-wrap gap-2">
      {refs.map((ref) => <Link className="text-accent hover:underline" key={`${ref.type}:${ref.id}`} to={ref.href}>{ref.date ? `${ref.date} · ` : ""}{ref.title || ref.id}</Link>)}
    </div>}
    {action.download_url && <a className="text-accent hover:underline" href={action.download_url} download>下载报告 HTML</a>}
    {action.input_card && <ProtectedInput card={action.input_card} />}
    {action.rollback_id && <RollbackControl id={action.rollback_id} />}
    {action.background_id && <OperationProgress id={action.background_id} />}
    {action.maintenance_id && <OperationProgress id={action.maintenance_id} maintenance />}
  </li>;
}
