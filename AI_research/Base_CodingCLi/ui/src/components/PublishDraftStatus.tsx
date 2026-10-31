export function PublishDraftStatus({ draft }: { draft: { status: string; recovered: boolean; dirty: boolean; storageError: boolean; discard: () => void } }) {
  return <div className="text-xs space-y-1" role="status"><p>{draft.status}</p>{draft.recovered ? <p className="text-warning">已恢复本机未保存内容；服务器草稿仍保留，可比较后继续编辑。</p> : null}{draft.storageError ? <p className="text-warning">浏览器暂存不可用，请先保存草稿再离开。</p> : null}{draft.dirty ? <button type="button" className="text-accent hover:underline" onClick={draft.discard}>恢复已保存草稿／默认文案</button> : null}</div>;
}
