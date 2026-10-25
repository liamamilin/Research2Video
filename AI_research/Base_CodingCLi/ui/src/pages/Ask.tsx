import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Loader2, MessageSquareText, Plus, Send, Square } from "lucide-react";
import {
  chatWithAssistant, getAssistantConversation, newAssistantConversation, stopAssistantResponse,
  type AssistantConversation, type AssistantMessage,
} from "@/api";
import { AssistantAction } from "@/components/AssistantAction";
import { useAuthStore } from "@/lib/auth-store";

const EXAMPLES = ["看看有哪些文稿等我审", "今天的快讯里有哪些选题还没写", "用今天的快讯写一期盘点", "查看现有 Jobs"];
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
function submitConversation(key: string, text: string, history: AssistantMessage[], conversationId?: string) {
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
  }, { conversation_id: conversationId, request_id: pending.requestId }).then((result) => {
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
function ApplicationAssistant({ username, readOnly }: { username: string; readOnly: boolean }) {
  const storageKey = `jobs-assistant:${username}`;
  const [params] = useSearchParams();
  const [messages, setMessages] = useState<AssistantMessage[]>(() => pendingConversations.get(storageKey)?.messages || readConversation(storageKey));
  const [input, setInput] = useState(() => params.get("video") ? `请查看视频文稿 ${params.get("video")}，告诉我现在到哪一步、下一步做什么` : params.get("job") ? `请查看任务 ${params.get("job")}` : params.get("report") ? `请分析报告 ${params.get("report")}` : "");
  const [busy, setBusy] = useState(() => pendingConversations.has(storageKey));
  const [restoring, setRestoring] = useState(() => !pendingConversations.has(storageKey));
  const [restoreError, setRestoreError] = useState("");
  const [status, setStatus] = useState(() => pendingConversations.get(storageKey)?.status || "");
  const [recoverVersion, setRecoverVersion] = useState(0);
  const conversationId = useRef<string | undefined>(pendingConversations.get(storageKey)?.conversationId || readConversationId(storageKey));
  const runningId = useRef<string>();
  const submitting = useRef(false);
  const bottom = useRef<HTMLDivElement>(null);
  const composer = useRef<HTMLTextAreaElement>(null);
  const follow = useRef(true);
  const context = params.get("video") ? { label: "来自视频文稿", href: `/video/${encodeURIComponent(params.get("video")!)}` }
    : params.get("job") ? { label: `来自任务 ${params.get("job")}`, href: `/jobs/${encodeURIComponent(params.get("job")!)}` }
    : params.get("report") ? { label: `来自报告 ${params.get("report")!.split("/").pop()}`, href: `/reports/view?path=${encodeURIComponent(params.get("report")!)}` } : null;
  useEffect(() => { if (follow.current) bottom.current?.scrollIntoView?.({ behavior: "auto" }); }, [messages, busy]);
  useEffect(() => {
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    const pending = pendingConversations.get(storageKey);
    if (pending) {
      const update = (current: AssistantMessage[], progress: string) => { if (active) { setMessages(current); setStatus(progress); } };
      pending.listeners.add(update);
      void pending.promise.then((completed) => { if (active) { conversationId.current = pending.conversationId; setMessages(completed); setBusy(false); } });
      return () => { active = false; pending.listeners.delete(update); };
    }
    const recover = async () => {
      try {
        const data = await getAssistantConversation(conversationId.current);
        if (!active) return;
        conversationId.current = data.id;
        saveConversationId(storageKey, data.id);
        const restored = messagesFromServer(data);
        setMessages(restored); saveConversation(storageKey, restored);
        const running = data.requests.find((item) => item.status === "running");
        runningId.current = running?.id;
        setBusy(!!running); setRestoring(false); setRestoreError("");
        setStatus(running ? "正在恢复本次请求的操作结果…" : "");
        if (running) timer = setTimeout(() => void recover(), 1200);
      } catch (error) {
        if (active) { setRestoring(false); setRestoreError(error instanceof Error ? error.message : "对话恢复失败"); }
      }
    };
    void recover();
    return () => { active = false; clearTimeout(timer); };
  }, [storageKey, recoverVersion]);
  const send = async (example?: string) => {
    const text = (example ?? input).trim();
    if (!text || restoring || restoreError || busy || submitting.current || pendingConversations.has(storageKey)) return;
    follow.current = true; submitting.current = true; setInput(""); setBusy(true);
    const pending = submitConversation(storageKey, text, messages, conversationId.current);
    runningId.current = pending.requestId;
    const update = (current: AssistantMessage[], progress: string) => { setMessages(current); setStatus(progress); };
    update(pending.messages, pending.status); pending.listeners.add(update);
    try { setMessages(await pending.promise); }
    finally { conversationId.current = pending.conversationId; pending.listeners.delete(update); submitting.current = false; setBusy(false); runningId.current = undefined; }
  };
  const fresh = async () => {
    setRestoring(true); setRestoreError("");
    try { const data = await newAssistantConversation(); conversationId.current = data.id; saveConversationId(storageKey, data.id); saveConversation(storageKey, []); setMessages([]); setInput(""); }
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
  const disabled = busy || restoring || !!restoreError;
  return <div className="h-full min-h-[420px] max-w-4xl mx-auto flex flex-col gap-4">
    <div className="flex flex-wrap items-center gap-3">
      <div className="flex-1 min-w-[14rem]"><h1 className="text-lg font-semibold">助手</h1><p className="text-xs text-text-muted mt-1">
        {readOnly ? "查询任务、报告与情报轮次。当前账号为只读权限；账号操作仅限本人。" : "通过对话处理视频文稿、选题、成片和发布记录，也能管理研究任务、报告和应用设置。"}
      </p></div>
      <button className="btn text-xs" disabled={disabled || messages.length === 0} onClick={() => { setRestoring(true); setRecoverVersion((n) => n + 1); }}>核验结果</button>
      <button className="btn text-xs" disabled={disabled || messages.length === 0} onClick={() => void fresh()}><Plus className="w-3.5 h-3.5" />新对话</button>
    </div>
    {context ? <div className="card p-3 flex flex-wrap gap-2 text-xs text-text-muted"><span>{context.label}</span><Link className="text-accent hover:underline ml-auto" to={context.href}>返回原页面 ↗</Link></div> : null}
    {restoring && <p className="text-xs text-text-muted">正在恢复对话与操作回执…</p>}
    {restoreError && <div className="card p-3 text-xs text-danger" role="alert">{restoreError}<button className="btn text-xs ml-3" onClick={() => { setRestoring(true); setRecoverVersion((n) => n+1); }}>重新读取对话</button></div>}
    <div className="flex-1 min-h-0 overflow-y-auto space-y-5 pr-1" aria-label="对话记录" onScroll={(event) => { const el = event.currentTarget; follow.current = el.scrollHeight - el.scrollTop - el.clientHeight < 96; }} style={{ colorScheme: "dark" }}>
      {messages.length === 0 && <div className="flex flex-col items-center justify-center h-full min-h-[200px] gap-4 text-center">
        <MessageSquareText className="w-9 h-9 text-accent" /><div><p className="font-medium">想处理什么？</p><p className="text-sm text-text-muted mt-2">选择一个示例填入输入框，也可以直接描述要处理的工作。</p></div>
        <div className="flex flex-wrap justify-center gap-2">{EXAMPLES.filter((_, i) => !readOnly || i !== 2).map((example) => <button key={example} className="btn text-xs" disabled={disabled} onClick={() => { setInput(example); composer.current?.focus(); }}>{example}</button>)}</div>
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
      <textarea ref={composer} className="input resize-none border-0 focus:ring-0 flex-1 min-w-0" rows={2} aria-label="消息" placeholder="例如：让这个 Job 每周一九点运行，使用上海时区"
        value={input} maxLength={8000} disabled={disabled} onChange={(event) => setInput(event.target.value)} onKeyDown={(event) => {
          if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); void send(); }
        }} />
      {busy ? <button type="button" className="btn mb-1" onClick={() => void stop()}><Square className="w-3.5 h-3.5" />停止响应</button> : <button type="submit" className="btn btn-primary mb-1" disabled={disabled || !input.trim()}><Send className="w-3.5 h-3.5" />发送</button>}
    </form>
    <p className="text-[11px] text-text-muted">Enter 发送 · Shift + Enter 换行 · 执行结果会保留在对话中，可用「核验结果」重新读取。</p>
  </div>;
}
