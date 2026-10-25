import { useEffect, useRef, useState } from 'react';
import { Link, useLocation } from 'react-router-dom';
import { MessageSquareText, X, Maximize2 } from 'lucide-react';
import { ApplicationAssistant } from '@/pages/Ask';
import { useAuthStore } from '@/lib/auth-store';
import { assistantContext, contextHref } from '@/lib/assistant-context';

export function AssistantDrawer() {
  const [open, setOpen] = useState(false);
  const location = useLocation();
  const user = useAuthStore((s) => s.user);
  const panel = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  useEffect(() => { setOpen(false); }, [location.key]);
  useEffect(() => {
    if (!open) return;
    const restore = (document.activeElement as HTMLElement | null) || trigger.current;
    panel.current?.focus();
    const keydown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') { setOpen(false); return; }
      if (event.key !== 'Tab') return;
      const nodes = panel.current?.querySelectorAll<HTMLElement>('a[href],button:not(:disabled),textarea:not(:disabled),input:not(:disabled),[tabindex="0"]');
      if (!nodes?.length) return;
      const first = nodes[0], last = nodes[nodes.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    };
    document.addEventListener('keydown', keydown);
    return () => { document.removeEventListener('keydown', keydown); restore?.focus(); };
  }, [open]);
  if (!user || location.pathname === '/ask') return null;
  const context = assistantContext(location.pathname, location.search);
  return <>
    <button ref={trigger} className="btn btn-primary fixed bottom-5 right-5 z-20 shadow-lg" onClick={() => setOpen(true)} aria-label="让助手处理当前页面">
      <MessageSquareText className="w-4 h-4" />让助手处理
    </button>
    {open && <div className="fixed inset-0 z-50 flex justify-end">
      <div className="absolute inset-0 bg-black/50" onClick={() => setOpen(false)} aria-hidden="true" />
      <div ref={panel} tabIndex={-1} role="dialog" aria-modal="true" aria-label="应用助手" className="relative w-full max-w-2xl h-full bg-bg-card border-l border-border shadow-2xl p-4 flex flex-col gap-3">
        <div className="flex items-center justify-between text-xs"><span className="text-text-muted">保留当前页面，在这里继续处理</span><div className="flex gap-2">
          <Link className="btn text-xs" to={context ? contextHref(context) : '/ask'}><Maximize2 className="w-3.5 h-3.5" />完整工作台</Link>
          <button className="btn text-xs" aria-label="关闭助手" onClick={() => setOpen(false)}><X className="w-4 h-4" /></button>
        </div></div>
        <div className="flex-1 min-h-0"><ApplicationAssistant key={user.username} username={user.username} readOnly={user.role === 'viewer'} embedded /></div>
      </div>
    </div>}
  </>;
}
