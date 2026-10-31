import { useCallback, useEffect, useState } from "react";
import { api } from "@/api/client";
import { errorMessage } from "@/lib/toast";
import { FolderOpen, HardDrive, Save } from "lucide-react";

type Storage = {
  directory: string;
  suggested_directory: string;
  online: boolean;
  free_bytes: number | null;
  migration: { status?: string; message?: string; current_folder?: string; moved_bytes?: number };
};
type Folders = { directory: string; parent: string; folders: { name: string; path: string }[] };
const folderLabel = (name: string) => ({ video_renders: "成片", jobs: "制作任务", "voice-cache": "配音缓存", "voice-models": "配音模型", "voice-runtime": "配音运行环境", "voice-samples": "配音样本" }[name] || "历史制作文件");
const gb = (bytes: number) => `${(bytes / 1024 ** 3).toFixed(1)} GB`;

export function VideoStorageConfig() {
  const [data, setData] = useState<Storage | null>(null);
  const [directory, setDirectory] = useState("");
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const [folders, setFolders] = useState<Folders | null>(null);
  const pending = ["waiting", "copying"].includes(data?.migration?.status || "");
  const load = useCallback(async (initial = false) => {
    try {
      const result = await api<Storage>("/api/config/video-storage");
      if (!result || typeof result.directory !== "string" || !result.migration) throw new Error("视频存储设置暂不可用，请刷新重试");
      setData(result);
      if (initial) setDirectory(result.directory || result.suggested_directory);
    } catch (err) { setError(errorMessage(err)); }
  }, []);
  useEffect(() => { void load(true); }, [load]);
  useEffect(() => {
    if (!pending) return;
    const timer = setInterval(() => { void load(); }, 3000);
    return () => clearInterval(timer);
  }, [load, pending]);
  const browse = async (path: string) => {
    setError("");
    try { setFolders(await api<Folders>("/api/config/video-storage/folders", { query: { directory: path } })); }
    catch (err) { setError(errorMessage(err)); }
  };
  const save = async () => {
    setSaving(true); setError("");
    try { setData(await api<Storage>("/api/config/video-storage", { method: "PUT", body: { directory } })); }
    catch (err) { setError(errorMessage(err)); }
    finally { setSaving(false); }
  };
  return <section id="setting-视频存储" className="card p-4 space-y-3" aria-label="视频输出位置">
    <h2 className="text-sm font-semibold flex items-center gap-2"><HardDrive size={16} />视频输出位置</h2>
    <p className="text-xs text-text-muted">成片、制作文件和缓存保存到所选文件夹。切换时会一起迁移旧文件，校验完成后释放原磁盘空间；正在生成的视频会先完成。</p>
    <div className="flex gap-2">
      <input className="input flex-1 min-w-0" aria-label="输出文件夹" value={directory} onChange={(e) => setDirectory(e.target.value)} placeholder="/Volumes/外接硬盘/视频输出" disabled={pending || saving} />
      <button className="btn text-xs flex items-center gap-1" onClick={() => void browse("/Volumes")} disabled={pending || saving}><FolderOpen size={14} />浏览文件夹</button>
      <button className="btn btn-primary text-xs flex items-center gap-1" onClick={() => void save()} disabled={!directory || pending || saving}><Save size={14} />{saving ? "保存中…" : "保存并迁移"}</button>
    </div>
    {data && <div className="text-xs text-text-muted space-y-1">
      <div>当前位置：{data.directory || "本机默认目录"}</div>
      <div>{data.online ? `磁盘已连接 · 可用空间 ${gb(data.free_bytes || 0)}` : "外接硬盘未连接，连接后才能生成和播放视频"}</div>
      {data.migration.message && <p role="status" className={data.migration.status === "failed" ? "text-danger" : data.migration.status === "completed" ? "text-success" : "text-accent"}>
        {data.migration.message}{data.migration.current_folder ? ` · ${folderLabel(data.migration.current_folder)}` : ""}{data.migration.moved_bytes ? ` · 已迁移 ${gb(data.migration.moved_bytes)}` : ""}
      </p>}
    </div>}
    {error && <p role="alert" className="text-xs text-danger">{error}</p>}
    {folders && <div role="dialog" aria-label="选择输出文件夹" className="border border-border rounded-lg p-3 space-y-3">
      <div className="flex items-center gap-2"><button className="btn text-xs" onClick={() => void browse(folders.parent)} disabled={folders.parent === folders.directory}>上一级</button><span className="text-xs break-all">{folders.directory}</span></div>
      <div className="max-h-56 overflow-y-auto space-y-1">{folders.folders.map((folder) => <button key={folder.path} className="flex items-center gap-2 w-full text-left text-xs rounded p-2 hover:bg-bg-hover" onClick={() => void browse(folder.path)}><FolderOpen size={14} />{folder.name}</button>)}{!folders.folders.length && <p className="text-xs text-text-muted p-2">此文件夹下没有子文件夹</p>}</div>
      <div className="flex gap-2"><button className="btn btn-primary text-xs" onClick={() => { setDirectory(folders.directory); setFolders(null); }}>使用此文件夹</button><button className="btn text-xs" onClick={() => setFolders(null)}>取消</button></div>
    </div>}
  </section>;
}
