import { useCallback, useEffect, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Loader2, MessageSquareText, Plus, Send, Square, History, PanelRight, ListTodo, X } from "lucide-react";
import {
  chatWithAssistant, getAssistantConversation, newAssistantConversation, stopAssistantResponse,
  type AssistantConversation, type AssistantMessage,
} from "@/api";
import { AssistantAction } from "@/components/AssistantAction";
import { AssistantWorkspacePanel } from "@/components/AssistantWorkspace";
import { AssistantTaskCenter } from "@/components/AssistantTaskCenter";
import { AssistantCapabilities } from "@/components/AssistantCapabilities";
import { listAssistantConversations, setAssistantContext, type ConversationSummary, type AssistantContext } from "@/api/assistant-workspace";
import { assistantContext, CONTEXT_LABELS } from "@/lib/assistant-context";
import { useAuthStore } from "@/lib/auth-store";

const EXAMPLES = ["查看各矩阵的任务与运行安排", "帮我创建一个研究矩阵", "看看有哪些文稿等我审", "看看有哪些视频可以发布", "我想把本地视频发布到指定平台", "检查发布队列里需要处理的问题", "检查今天有哪些任务失败了"];
const READ_EXAMPLES = ["看看有哪些文稿等我审", "今天的快讯里有哪些选题还没写", "查看现有 Jobs", "查看发布进度"];
interface PendingConversation {
  messages: AssistantMessage[];
  status: string;
  requestId: string;
  conversationId?: string;
  listeners: Set<(messages: AssistantMessage[], status: string) => void>;
  promise: Promise<AssistantMessage[]>;
}
const pendingConversations = new Map<string, PendingConversation>();
function saveConversation(key: string, messages: AssistantMessage[]) {
  try { sessionStorage.setItem(key, JSON.stringify(messages.slice(-60))); } catch { /* Storage is optional. */ }
}
function readConversation(key: string): AssistantMessage[] {
  try {
    const saved: unknown = JSON.parse(sessionStorage.getItem(key) || "[]");
    if (Array.isArray(saved)) return saved.filter((m): m is AssistantMessage => m && (m.role === "user" || m.role === "assistant") && typeof m.content === "string").slice(-60);
  } catch { /* Storage is optional. */ }
  return [];
}
function readConversationId(key: string): string | undefined {
  try { const id = sessionStorage.getItem(`${key}:id`); return id && /^[a-f0-9]{32}$/.test(id) ? id : undefined; }
  catch { return undefined; }
}
function saveConversationId(key: string, id?: string) {
  if (id) { try { sessionStorage.setItem(`${key}:id`, id); } catch { /* Storage is optional. */ } }
}
function messagesFromServer(data: AssistantConversation): AssistantMessage[] {
  return data.requests.flatMap((item) => [
    { role: "user" as const, content: item.message },
    { role: "assistant" as const, content: item.result.answer || "", actions: item.result.actions || [], error: item.result.error, continuation: item.result.continuation },
  ]);
}
function submitConversation(key: string, text: string, history: AssistantMessage[], conversationId?: string, context?: AssistantContext | null) {
  const prefix: AssistantMessage[] = [...history, { role: "user", content: text }];
  const pending: PendingConversation = {
    messages: [...prefix, { role: "assistant", content: "", actions: [] }], status: "正在连接…",
    requestId: crypto.randomUUID().replace(/-/g, ""), listeners: new Set(), promise: Promise.resolve([]),
    conversationId,
  };
  const publish = () => {
    saveConversation(key, pending.messages);
    pending.listeners.forEach((listener) => listener(pending.messages, pending.status));
  };
  pendingConversations.set(key, pending);
  pending.promise = chatWithAssistant(text, history, (event) => {
    const reply = pending.messages[pending.messages.length - 1];
    if (event.type === "reset") pending.messages = [...prefix, { ...reply, content: "" }];
    if (event.type === "delta") { pending.messages = [...prefix, { ...reply, content: reply.content + event.text }]; pending.status = "正在回复…"; }
    if (event.type === "action") pending.messages = [...prefix, { ...reply, actions: [...(reply.actions || []), event.action] }];
    if (event.type === "status") pending.status = event.message;
    publish();
  }, { conversation_id: conversationId, request_id: pending.requestId, context }).then((result) => {
    pending.conversationId = result.conversation_id || conversationId;
    saveConversationId(key, pending.conversationId);
    pending.messages = [...prefix, { role: "assistant", content: result.answer, actions: result.actions, error: result.error, continuation: result.continuation }];
  }).catch((error: unknown) => {
    const reply = pending.messages[pending.messages.length - 1];
    pending.messages = [...prefix, { ...reply, content: reply.content || "请求未完成。",
      error: (error instanceof Error ? error.message : "请求失败") + "。请点顶部“核验结果”读取实际回执，再继续。" }];
  }).then(() => { pending.status = ""; publish(); pendingConversations.delete(key); return pending.messages; });
  publish();
  return pending;
}
export function AskPage() {
  const user = useAuthStore((s) => s.user);
  return <ApplicationAssistant key={user?.username} username={user?.username || ""} readOnly={user?.role === "viewer"} />;
}
export function ApplicationAssistant({ username, readOnly, embedded = false }: { username: string; readOnly: boolean; embedded?: boolean }) {
  const storageKey = `jobs-assistant:${username}`;
  const location = useLocation();
  const navigate = useNavigate();
  const [contextError, setContextError] = useState("");
  const [savingContext, setSavingContext] = useState(false);
  const routeKey = location.pathname + location.search;
  const [selectedContext, setSelectedContext] = useState<AssistantContext | null>(() => assistantContext(location.pathname, location.search));
  const [historyOpen, setHistoryOpen] = useState(false);
  const [historyItems, setHistoryItems] = useState<ConversationSummary[]>([]);
  const [historyError, setHistoryError] = useState("");
  const [historyLoading, setHistoryLoading] = useState(false);
  const [workspaceOpen, setWorkspaceOpen] = useState(!embedded && window.innerWidth >= 1024 && new URLSearchParams(location.search).get('tasks') !== '1');
  const [taskCenterOpen, setTaskCenterOpen] = useState(new URLSearchParams(location.search).get('tasks') === '1');
  const [capabilitiesOpen, setCapabilitiesOpen] = useState(new URLSearchParams(location.search).get('capabilities') === '1');
  const [revision, setRevision] = useState(0);
  const [messages, setMessages] = useState<AssistantMessage[]>(() => pendingConversations.get(storageKey)?.messages || readConversation(storageKey));
  const [input, setInput] = useState(() => (new URLSearchParams(location.search).get('prompt') || '').slice(0, 8000));
  const [busy, setBusy] = useState(() => pendingConversations.has(storageKey));
  const [restoring, setRestoring] = useState(() => !pendingConversations.has(storageKey));
  const [restoreError, setRestoreError] = useState("");
  const [status, setStatus] = useState(() => pendingConversations.get(storageKey)?.status || "");
  const [recoverVersion, setRecoverVersion] = useState(0);
  const requestedConversation = new URLSearchParams(location.search).get('conversation') || '';
  const conversationId = useRef<string | undefined>(pendingConversations.get(storageKey)?.conversationId ||
    (/^[a-f0-9]{32}$/.test(requestedConversation) ? requestedConversation : readConversationId(storageKey)));
  const [activeConversationId, setActiveConversationId] = useState(conversationId.current);
  const bindConversation = useCallback((id?: string) => { conversationId.current = id; setActiveConversationId(id); if (id) saveConversationId(storageKey, id); }, [storageKey]);
  const historySelected = useRef(false);
  const runningId = useRef<string>();
  const submitting = useRef(false);
  const bottom = useRef<HTMLDivElement>(null);
  const composer = useRef<HTMLTextAreaElement>(null);
  const follow = useRef(true);
  const previousRoute = useRef(routeKey);
  useEffect(() => {
    if (previousRoute.current !== routeKey) {
      previousRoute.current = routeKey;
      historySelected.current = false;
      setSelectedContext(assistantContext(location.pathname, location.search));
    }
  }, [routeKey, location.pathname, location.search]);
  useEffect(() => {
    if (new URLSearchParams(location.search).get('tasks') === '1') { setTaskCenterOpen(true); setWorkspaceOpen(false); }
  }, [location.search]);
  useEffect(() => {
    if (!busy && /^[a-f0-9]{32}$/.test(requestedConversation) && conversationId.current !== requestedConversation) {
      historySelected.current = true; bindConversation(requestedConversation);
      setMessages([]); setSelectedContext(null); setRestoring(true); setRestoreError(''); setRecoverVersion(n => n + 1);
    }
  }, [requestedConversation, busy, bindConversation]);
  const fill = (text: string) => { setInput(text); composer.current?.focus(); };
  const removeContext = async () => {
    setSavingContext(true); setContextError("");
    try {
      if (activeConversationId) await setAssistantContext(activeConversationId, null);
      setSelectedContext(null);
      if (location.pathname === '/ask') navigate('/ask', { replace: true });
    } catch (error) { setContextError(error instanceof Error ? error.message : '移除对象失败'); }
    finally { setSavingContext(false); }
  };
  useEffect(() => { if (embedded && !restoring) composer.current?.focus(); }, [embedded, restoring]);
  const openHistory = async () => {
    setHistoryOpen(true); setHistoryLoading(true); setHistoryError("");
    try { setHistoryItems(await listAssistantConversations()); }
    catch (error) { setHistoryError(error instanceof Error ? error.message : "历史对话读取失败"); }
    finally { setHistoryLoading(false); }
  };
  useEffect(() => { if (follow.current) bottom.current?.scrollIntoView?.({ behavior: "auto" }); }, [messages, busy]);
  useEffect(() => {
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    const pending = pendingConversations.get(storageKey);
    if (pending) {
      const update = (current: AssistantMessage[], progress: string) => { if (active) { setMessages(current); setStatus(progress); } };
      pending.listeners.add(update);
      void pending.promise.then((completed) => { if (active) { bindConversation(pending.conversationId); setMessages(completed); setBusy(false); setRevision((n) => n + 1); } });
      return () => { active = false; pending.listeners.delete(update); };
    }
    const recover = async () => {
      try {
        const data = await getAssistantConversation(conversationId.current);
        if (!active) return;
        bindConversation(data.id);
        if (historySelected.current || !assistantContext(location.pathname, location.search)) setSelectedContext(data.context || null);
        const restored = messagesFromServer(data);
        setMessages(restored); saveConversation(storageKey, restored);
        const running = data.requests.find((item) => item.status === "running");
        runningId.current = running?.id;
        setBusy(!!running); setRestoring(false); setRestoreError("");
        setStatus(running ? "正在恢复本次请求的操作结果…" : "");
        setRevision((n) => n + 1);
        if (running) timer = setTimeout(() => void recover(), 1200);
      } catch (error) {
        if (active) { setRestoring(false); setRestoreError(error instanceof Error ? error.message : "对话恢复失败"); }
      }
    };
    void recover();
    return () => { active = false; clearTimeout(timer); };
  }, [storageKey, recoverVersion, bindConversation, location.pathname, location.search]);
  const send = async (example?: string) => {
    const text = (example ?? input).trim();
    if (!text || restoring || restoreError || busy || submitting.current || pendingConversations.has(storageKey)) return;
    follow.current = true; submitting.current = true; setInput(""); setBusy(true);
    const pending = submitConversation(storageKey, text, messages, conversationId.current, selectedContext);
    runningId.current = pending.requestId;
    const update = (current: AssistantMessage[], progress: string) => { setMessages(current); setStatus(progress); };
    update(pending.messages, pending.status); pending.listeners.add(update);
    try { setMessages(await pending.promise); }
    finally { bindConversation(pending.conversationId); setRevision((n) => n + 1); pending.listeners.delete(update); submitting.current = false; setBusy(false); runningId.current = undefined; }
  };
  const fresh = async () => {
    setRestoring(true); setRestoreError(""); historySelected.current = false;
    try { const data = await newAssistantConversation(); bindConversation(data.id); saveConversation(storageKey, []); setMessages([]); setInput(""); setSelectedContext(assistantContext(location.pathname, location.search)); setRevision((n) => n + 1);
      if (location.pathname === '/ask' && requestedConversation) { const params = new URLSearchParams(location.search); params.delete('conversation'); navigate('/ask' + (params.size ? '?' + params.toString() : ''), { replace: true }); }
    }
    catch (error) { setRestoreError(error instanceof Error ? error.message : "新对话未创建"); }
    finally { setRestoring(false); }
  };
  const stop = useCallback(async () => {
    const id = pendingConversations.get(storageKey)?.requestId || runningId.current;
    if (!id) return;
    setStatus("正在停止助手响应…");
    try { await stopAssistantResponse(id); }
    catch (error) { setRestoreError(error instanceof Error ? error.message : "停止请求失败"); }
  }, [storageKey]);
  const openConversation = (id: string, prompt = '') => {
    if (busy) return;
    historySelected.current = true; bindConversation(id); setHistoryOpen(false); setTaskCenterOpen(false);
    setWorkspaceOpen(!embedded && window.innerWidth >= 1024); setRestoring(true); setRestoreError('');
    setMessages([]); setSelectedContext(null); setInput(prompt); setRecoverVersion(n => n + 1);
    if (!embedded) navigate('/ask?conversation=' + id, { replace: true });
  };
  const disabled = busy || restoring || !!restoreError;
  return <div className={`${embedded ? "h-full" : "h-[calc(100dvh-3rem)]"} min-h-[420px] max-w-[1500px] mx-auto flex flex-col gap-4`}>
    <div className="flex flex-wrap items-center gap-3">
      <div className="flex-1 min-w-[14rem]"><h1 className="text-lg font-semibold">助手</h1><p className="text-xs text-text-muted mt-1">
        {readOnly ? "查询任务、报告与情报轮次。当前账号为只读权限；账号操作仅限本人。" : "说出目标，助手处理研究、内容制作、配置与发布，工作区显示实际结果。"}
      </p></div>
      <button className="btn text-xs" disabled={busy} onClick={() => void openHistory()}><History className="w-3.5 h-3.5" />历史对话</button>
      <button className="btn text-xs" aria-expanded={workspaceOpen} onClick={() => { setWorkspaceOpen((v) => !v); setTaskCenterOpen(false); }}><PanelRight className="w-3.5 h-3.5" />工作区</button>
      <button className="btn text-xs" aria-expanded={taskCenterOpen} onClick={() => { setTaskCenterOpen(v => !v); setWorkspaceOpen(false); }}><ListTodo className="w-3.5 h-3.5" />任务中心</button>
      <button className="btn text-xs" aria-expanded={capabilitiesOpen} onClick={() => setCapabilitiesOpen(v => !v)}>能做什么</button>
      <button className="btn text-xs" disabled={disabled || messages.length === 0} onClick={() => { setRestoring(true); setRecoverVersion((n) => n + 1); }}>核验结果</button>
      <button className="btn text-xs" disabled={busy || restoring || (!restoreError && messages.length === 0)} onClick={() => void fresh()}><Plus className="w-3.5 h-3.5" />新对话</button>
    </div>
    {capabilitiesOpen && <AssistantCapabilities onPrompt={text => { setInput(text); setCapabilitiesOpen(false); }} />}
    {selectedContext && <div className="card p-3 flex items-center gap-3 text-xs">
      <span className="text-text-muted">当前对象：{CONTEXT_LABELS[selectedContext.kind]}</span><span className="truncate">{selectedContext.kind === "video" ? "已关联文稿，详情见工作区" : selectedContext.id}</span>
      <button className="ml-auto shrink-0 text-text-muted hover:text-text" aria-label="移除当前对象" disabled={busy || restoring || savingContext} onClick={() => void removeContext()}><X className="w-4 h-4" /></button>
    </div>}
    {contextError && <p className="text-xs text-danger">{contextError}</p>}
    {historyOpen && <div className="card p-4 max-h-64 overflow-auto space-y-2" aria-label="历史对话">
      <div className="flex items-center justify-between"><h2 className="text-sm font-medium">历史对话</h2><button className="btn text-xs" onClick={() => setHistoryOpen(false)}>收起</button></div>
      {historyLoading && <p className="text-xs text-text-muted">正在读取…</p>}
      {historyError && <p className="text-xs text-danger">{historyError}<button className="btn text-xs ml-2" onClick={() => void openHistory()}>重试</button></p>}
      {historyItems.map((item) => <button key={item.id} disabled={busy} className={`block w-full text-left rounded p-2 text-sm hover:bg-bg-hover ${activeConversationId === item.id ? 'text-accent bg-bg-hover' : ''}`}
        onClick={() => openConversation(item.id)}>
        {item.title}<span className="block text-xs text-text-muted mt-1">{new Date(item.updated * 1000).toLocaleString('zh-CN')} · {item.request_count} 次请求</span>
      </button>)}
    </div>}
    <div className="flex flex-1 min-h-0 gap-5 relative">
    <section className="flex flex-1 flex-col min-w-0 min-h-0 gap-4">
    {restoring && <p className="text-xs text-text-muted">正在恢复对话与操作回执…</p>}
    {restoreError && <div className="card p-3 text-xs text-danger" role="alert">{restoreError}<button className="btn text-xs ml-3" onClick={() => { setRestoring(true); setRecoverVersion((n) => n+1); }}>重新读取对话</button></div>}
    <div className="flex-1 min-h-0 overflow-y-auto space-y-5 pr-1" aria-label="对话记录" onScroll={(event) => { const el = event.currentTarget; follow.current = el.scrollHeight - el.scrollTop - el.clientHeight < 96; }} style={{ colorScheme: "dark" }}>
      {messages.length === 0 && <div className="flex flex-col items-center justify-center h-full min-h-[200px] gap-4 text-center">
        <MessageSquareText className="w-9 h-9 text-accent" /><div><p className="font-medium">想处理什么？</p><p className="text-sm text-text-muted mt-2">选择一个示例填入输入框，也可以直接描述要处理的工作。</p></div>
        <div className="flex flex-wrap justify-center gap-2">{(readOnly ? READ_EXAMPLES : EXAMPLES).map((example) => <button key={example} className="btn text-xs" disabled={disabled} onClick={() => { setInput(example); composer.current?.focus(); }}>{example}</button>)}</div>
      </div>}
      {messages.map((message, index) => <div key={index} className={message.role === "user" ? "ml-auto max-w-[90%] sm:max-w-[80%]" : "max-w-full"}>
        <div className="text-xs text-text-muted mb-2">{message.role === "user" ? "你" : "助手"}</div>
        <div className={message.role === "user" ? "rounded-lg bg-bg-hover border border-border px-4 py-3" : "card px-4 py-4 sm:px-5"}>
          {message.role === "user" ? <p className="text-sm whitespace-pre-wrap break-words">{message.content}</p> : <>
            <div className="md-body"><ReactMarkdown remarkPlugins={[remarkGfm]}>{message.content}</ReactMarkdown></div>
            {message.continuation && <div className="mt-3 rounded border border-border p-3 text-xs space-y-2">
              <p>{message.continuation.reason === 'service_maintenance' ? '维护已提交，后续步骤需恢复访问后继续。' : `原请求尚未完成，进度已保存。已完整读取 ${message.continuation.reports_complete} 份报告，${message.continuation.remaining_report_count} 份候选待展开。`}</p>
              <button type="button" className="btn text-xs" disabled={disabled} onClick={() => void send('继续上一条尚未完成的请求。先核验已有回执，从保存的读取位置和剩余工作继续；已成功的操作不要重复执行。')}>继续处理</button>
            </div>}
            {!!message.actions?.length && <ul className="mt-4 pt-3 border-t border-border space-y-3" aria-label="操作结果">{message.actions.map((action, i) => <AssistantAction key={action.operation_id || i} action={action} />)}</ul>}
            {message.error && message.error !== message.content && <p role="alert" className="text-xs text-danger mt-3">{message.error}</p>}
          </>}
        </div>
      </div>)}
      {busy && <div className="flex items-center gap-2 text-sm text-text-muted" role="status"><Loader2 className="w-4 h-4 animate-spin text-accent" />{status || "正在处理…"}</div>}
      {messages.length > 0 && <div ref={bottom} />}
    </div>
    <form className="card p-3 flex items-end gap-3" onSubmit={(event) => { event.preventDefault(); void send(); }}>
      <textarea ref={composer} className="input resize-none border-0 focus:ring-0 flex-1 min-w-0" rows={2} aria-label="消息" placeholder={selectedContext ? "描述你想怎样调整当前对象…" : "例如：管理矩阵和周期，调整生成配置，准备或发布视频"}
        value={input} maxLength={8000} disabled={disabled} onChange={(event) => setInput(event.target.value)} onKeyDown={(event) => {
          if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); void send(); }
        }} />
      {busy ? <button type="button" className="btn mb-1" onClick={() => void stop()}><Square className="w-3.5 h-3.5" />停止响应</button> : <button type="submit" className="btn btn-primary mb-1" disabled={disabled || !input.trim()}><Send className="w-3.5 h-3.5" />发送</button>}
    </form>
    <p className="text-[11px] text-text-muted">Enter 发送 · Shift + Enter 换行 · 执行结果会保留在对话中，可用「核验结果」重新读取。</p>
    </section>
    {(workspaceOpen || taskCenterOpen) && <aside className={`${embedded ? 'absolute inset-0 z-10 bg-bg-card p-4 rounded-xl' : 'absolute inset-0 z-10 bg-bg-card p-4 lg:p-0 lg:static lg:w-96 lg:shrink-0'} overflow-y-auto border-l border-border lg:pl-5`}>
      <button className={`${embedded ? '' : 'lg:hidden'} btn text-xs mb-4`} onClick={() => { setWorkspaceOpen(false); setTaskCenterOpen(false); }}>返回对话</button>
      {restoring && revision === 0 ? <p role="status" className="text-xs text-text-muted">正在恢复工作区…</p>
        : taskCenterOpen ? <AssistantTaskCenter revision={revision} busy={busy || restoring} onOpenConversation={openConversation} />
        : <AssistantWorkspacePanel conversationId={activeConversationId} context={selectedContext} revision={revision} onPrompt={(text) => { fill(text); if (embedded || window.innerWidth < 1024) setWorkspaceOpen(false); }} />}
    </aside>}
    </div>
  </div>;
}
