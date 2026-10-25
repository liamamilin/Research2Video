import { getAssistantCapabilities } from '@/api/assistant-workspace';
import { usePageResource } from '@/lib/use-page-resource';

export function AssistantCapabilities({ onPrompt }: { onPrompt: (text: string) => void }) {
  const resource = usePageResource(getAssistantCapabilities);
  return <section className="card p-4 space-y-3 text-xs max-h-[60vh] overflow-auto" aria-label="助手能力范围">
    <h2 className="font-medium text-sm">整个应用可以怎样交给助手</h2>
    {resource.error && <p role="alert">{resource.error}</p>}
    {!resource.data && !resource.error && <p>正在读取能力范围…</p>}
    <div className="grid sm:grid-cols-2 gap-3">{resource.data?.areas.map(area => <article className="border border-border rounded-md p-3 space-y-2" key={area.key}>
      <h3 className="font-medium">{area.title}</h3><p className="text-text-muted">{area.detail}</p>
      <button className="text-accent text-left" onClick={() => onPrompt(area.example)}>{area.example} ↗</button>
    </article>)}</div>
    {resource.data?.boundaries.map(text => <p className="text-text-muted" key={text}>{text}</p>)}
  </section>;
}
