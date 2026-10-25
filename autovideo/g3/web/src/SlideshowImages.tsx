import React, {useState} from 'react';

export type CreativeImage = {image_id: string; filename: string; preview_url: string; width: number; height: number; caption?: string; role?: 'reference'|'overall'|'detail'|'comparison'};
export function SlideshowImages({images, onChange, onBusy, ready, materials = false}: {images: CreativeImage[]; onChange: (value: CreativeImage[]) => void; onBusy: (value: boolean) => void; ready: boolean; materials?: boolean}) {
  const [busy, setBusy] = useState(false), [error, setError] = useState('');
  async function upload(files: FileList | null) {
    if (!files?.length) return;
    if (images.length + files.length > 12) {setError('最多上传 12 张图片。'); return;}
    setBusy(true); onBusy(true); setError('');
    const next = [...images];
    try {
      for (const file of Array.from(files)) {
        const data = new FormData(); data.append('file', file);
        const response = await fetch('/api/creative-images', {method: 'POST', body: data});
        const result = await response.json();
        if (!response.ok) throw new Error(result.detail?.message || '图片上传失败');
        next.push(result); onChange([...next]);
      }
    } catch (e) {setError(e instanceof Error ? e.message : '图片上传失败');}
    finally {setBusy(false); onBusy(false);}
  }
  function move(index: number, direction: number) {
    const next = [...images]; [next[index], next[index + direction]] = [next[index + direction], next[index]]; onChange(next);
  }
  return <section className="slideshow-input" aria-label={materials ? "配图素材" : "轮播图片"}>
    <div className="section-line"><h3>{materials ? "配图素材（可选）" : "轮播图片"}</h3><label className="secondary-button" htmlFor="slideshow-upload">{busy ? '上传中…' : '选择图片'}</label><input id="slideshow-upload" type="file" multiple accept="image/png,image/jpeg,image/webp" disabled={busy} onChange={e => {void upload(e.target.files); e.target.value = '';}}/></div>
    <p>{materials ? '可使用上游取得的图片，补充说明和用途后推荐画面形式；图片按提交顺序编排，不做内容识别。' : images.length ? '按下方顺序分配到讲稿段落；段落较多时，图片会重复使用。' : ready ? '可上传自己的图片；不上传时，由本机 Draw Things 自动生成 3 张概念插画。' : '请上传图片，或连接 Draw Things 后自动生成插画。'}最多 12 张，每张 20 MB。</p>
    <div className="slideshow-grid">{images.map((image, i) => <div key={image.image_id}><img src={image.preview_url} alt={`轮播图片 ${i + 1}：${image.filename}`}/><small>{i + 1}. {image.filename}</small>{materials && <><label className="setting"><span className="setting-label">图片 {i+1} 说明</span><input className="text-input" aria-label={`图片 ${i+1} 说明`} value={image.caption || ''} maxLength={80} placeholder="可选，如：整体外观、接口细节" onChange={e=>onChange(images.map((item,n)=>n===i?{...item,caption:e.target.value}:item))}/></label><label className="setting"><span className="setting-label">图片 {i+1} 用途</span><select className="text-input" aria-label={`图片 ${i+1} 用途`} value={image.role || 'reference'} onChange={e=>onChange(images.map((item,n)=>n===i?{...item,role:e.target.value as CreativeImage['role']}:item))}><option value="reference">参考图片</option><option value="overall">整体</option><option value="detail">细节</option><option value="comparison">对比</option></select></label></>}<div className="slideshow-actions"><button type="button" disabled={busy || i === 0} onClick={() => move(i, -1)} aria-label={`前移图片 ${i+1}`}>前移</button><button type="button" disabled={busy || i === images.length - 1} onClick={() => move(i, 1)} aria-label={`后移图片 ${i+1}`}>后移</button><button type="button" disabled={busy} onClick={() => onChange(images.filter((_, n) => n !== i))} aria-label={`移除图片 ${i+1}`}>移除</button></div></div>)}</div>
    {error && <p role="alert" className="revision-error">{error}</p>}
  </section>;
}
