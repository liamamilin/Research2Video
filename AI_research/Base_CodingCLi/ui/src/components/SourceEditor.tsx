import { useEffect, useState } from 'react';
import { deleteSource, getSource, saveSource } from '@/api/sources';

export function SourceEditor({ id, revision, onClose, onSaved }: {
  id?: string; revision: string; onClose: () => void; onSaved: () => void;
}) {
  const [key, setKey] = useState(id || '');
  const [version, setVersion] = useState(revision);
  const [name, setName] = useState('');
  const [spec, setSpec] = useState('');
  const [tags, setTags] = useState('');
  const [note, setNote] = useState('');
  const [usedBy, setUsedBy] = useState<{ job: string; name: string }[]>([]);
  const [loading, setLoading] = useState(!!id);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [confirmDelete, setConfirmDelete] = useState(false);
  useEffect(() => {
    if (!id) return;
    let active = true;
    void getSource(id).then(data => {
      if (!active) return;
      setName(data.name); setSpec(data.spec); setTags(data.tags.join(', ')); setNote(data.note); setVersion(data.revision); setUsedBy(data.used_by);
    }).catch(e => { if (active) setError(e instanceof Error ? e.message : '来源读取失败'); }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [id]);
  const save = async (remove = false) => {
    setSaving(true); setError('');
    try {
      if (remove) await deleteSource(key, version);
      else await saveSource(key.trim(), version, { name: name.trim(), spec: spec.trim(), tags: [...new Set(tags.split(/[,，]/).map(t => t.trim()).filter(Boolean))], note }, !id);
      onSaved(); onClose();
    } catch (e) { setError(e instanceof Error ? e.message : '保存失败'); }
    finally { setSaving(false); }
  };
  return <div className="fixed inset-0 z-50 bg-black/60 flex items-center justify-center p-4" role="dialog" aria-modal="true" aria-label={id ? '修改信息源' : '新增信息源'}>
    <form className="card p-5 w-full max-w-xl space-y-4 max-h-[90vh] overflow-y-auto" onSubmit={e => { e.preventDefault(); void save(); }}>
      <h2 className="font-medium">{id ? '修改信息源' : '新增信息源'}</h2>
      {loading && <p className="text-xs text-text-muted">正在读取当前配置…</p>}
      {error && <p role="alert" className="text-xs text-danger whitespace-pre-wrap break-words">{error}</p>}
      <fieldset disabled={loading || saving} className="space-y-3">
        <label className="block text-xs space-y-1"><span>来源 ID</span><input className="input w-full" required disabled={!!id} value={key} onChange={e => setKey(e.target.value)} placeholder="例如 nasa" /></label>
        <label className="block text-xs space-y-1"><span>名称</span><input className="input w-full" required value={name} onChange={e => setName(e.target.value)} /></label>
        <label className="block text-xs space-y-1"><span>来源地址</span><input className="input w-full" required value={spec} onChange={e => setSpec(e.target.value)} placeholder="rss:https://example.org/feed 或 github:owner/repo" /></label>
        <label className="block text-xs space-y-1"><span>来源标签（逗号分隔）</span><input className="input w-full" value={tags} onChange={e => setTags(e.target.value)} /></label>
        <label className="block text-xs space-y-1"><span>备注</span><textarea className="input w-full" rows={2} value={note} onChange={e => setNote(e.target.value)} /></label>
        <p className="text-xs text-text-muted">研究任务使用 source:{key || '来源ID'} 引用；使用 tag:标签 的任务会包含该标签下的来源。</p>
        {!!usedBy.length && <p className="text-xs text-warning">影响 {usedBy.length} 个引用任务：{usedBy.map(u => u.name).join('、')}。修改用于后续研究。</p>}
        <div className="flex gap-2 flex-wrap">
          <button className="btn-primary text-xs" type="submit">{saving ? '保存中…' : '保存信息源'}</button>
          {id && !confirmDelete && <button className="btn text-xs text-danger" type="button" onClick={() => setConfirmDelete(true)}>删除来源</button>}
          {confirmDelete && <button className="btn text-xs text-danger" type="button" onClick={() => void save(true)}>确认删除此来源</button>}
        </div>
      </fieldset>
      <button className="btn text-xs" type="button" disabled={saving} onClick={onClose}>关闭</button>
    </form>
  </div>;
}
