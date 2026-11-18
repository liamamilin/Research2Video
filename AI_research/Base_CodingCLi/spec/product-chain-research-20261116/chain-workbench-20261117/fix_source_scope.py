from pathlib import Path
root=Path('runtime')
p=root/'core/source_coverage.py';s=p.read_text().replace("'cells': cells, 'candidates': counts})", "'jobs':list(profile.get('jobs',{})), 'cells': cells, 'candidates': counts})");p.write_text(s)
p=root/'ui/src/api/sources.ts';s=p.read_text().replace('coverage?: { key: string; name: string;', 'coverage?: { key: string; name: string; jobs?: string[];').replace('key: string; subject: string; topics: string[];', 'key: string; subject: string; topics: string[]; jobs?: string[];');p.write_text(s)
p=root/'ui/src/pages/Sources.tsx';s=p.read_text();a=s.index('  const tags =');b=s.index('  if (!catalog)',a)
s=s[:a]+'''  const scopedSources = useMemo(() => (catalog?.sources ?? []).filter(s => !chainScope.key || s.used_by.some(u => u.job === chainScope.data?.job)), [catalog, chainScope.key, chainScope.data?.job]);
  const tags = useMemo(() => Array.from(new Set(scopedSources.flatMap(s => s.tags))).sort(), [scopedSources]);
  const shown = useMemo(() => {
    const needle = text.trim().toLowerCase();
    return scopedSources.filter(s =>
      (filter === "all" || (filter === "used" && s.used_by.length > 0) || (filter === "unused" && s.used_by.length === 0)
        || (filter === "failing" && Boolean(s.last?.error))) &&
      (!tag || s.tags.includes(tag)) &&
      (!needle || `${s.name} ${s.id} ${s.spec} ${s.note}`.toLowerCase().includes(needle)));
  }, [scopedSources, filter, tag, text]);
''' +s[b:];s=s.replace('const failing = catalog.sources.filter','const failing = scopedSources.filter');s=s.replace('catalog.sources.length','scopedSources.length');s=s.replace('catalog.coverage?.map(profile =>',"catalog.coverage?.filter(profile => !chainScope.key || profile.jobs?.includes(chainScope.data?.job||'')).map(profile =>");s=s.replace('catalog.problems.length ?', 'catalog.problems.some(p => chainScope.job(p.job)) ?').replace('catalog.problems.map((p)', 'catalog.problems.filter(p=>chainScope.job(p.job)).map((p)');p.write_text(s)
p=root/'web/services/video_executor.py';s=p.read_text();s=s.replace('''    if not can_start_render:
        return out
    from core.video import production_admission''','''    if not can_start_render:
        out['item']={'note':'上次出片失败；等待渲染名额后自动重试' if latest and latest['status']=='FAILED' else '等待渲染名额，已有讲稿保留'}
        return out
    from core.video import production_admission''');p.write_text(s)
p=root/'ui/src/pages/Chains.tsx';s=p.read_text().replace('当前出片步骤：{({PLAN:', "{i.render_progress.status==='FAILED'?'上次出片失败：':'当前出片步骤：'}{({PLAN:");p.write_text(s)
