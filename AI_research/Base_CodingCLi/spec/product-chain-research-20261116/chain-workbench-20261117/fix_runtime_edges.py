from pathlib import Path
r=Path(__file__).resolve().parent/'runtime'
p=r/'web/services/chain_reports.py';s=p.read_text();a=s.index('def record(');b=s.index('def reconcile(')
s=s[:a]+'''def relative_path(report):
    root=Path(get_settings().paths.output_dir).resolve()
    path=Path(report)
    if not path.is_absolute():path=root/str(path).removeprefix('output/')
    resolved=path.resolve()
    if not resolved.is_relative_to(root):raise ValueError('报告路径不在输出目录中')
    return str(resolved.relative_to(root))

def record(job,report):
    root=Path(get_settings().paths.output_dir)
    rel=relative_path(report)
    file=root/rel
    if not file.is_file():return
    stat=file.stat()
    db.upsert_report(path=rel,mtime=stat.st_mtime,size_bytes=stat.st_size,job_name=job,
                     title=scanner.extract_title(str(file)),category=Path(rel).parts[0],
                     content=file.read_text(errors='replace')[:scanner.MAX_INDEX_BYTES])
''' + s[b:];p.write_text(s)
p=r/'web/services/chain_runner.py';s=p.read_text();s=s.replace("        report=Path(row['report']);root=Path(chains.paths().output_dir)\n        row['report_view']=str(report.relative_to(root)) if report.is_absolute() and report.is_relative_to(root) else str(report).removeprefix('output/')", "        from web.services.chain_reports import relative_path\n        row['report_view']=relative_path(row['report'])");p.write_text(s)
p=r/'renderer/g2/explanation.py';s=p.read_text();s=s.replace("        raise ValueError('画面页需要按顺序覆盖输入分组')", "        expected=[g['id'] for g in context['groups']]\n        actual=[p['id'] for p in plan['pages']]\n        raise ValueError(f'画面页的 id 必须逐字使用输入分组 id，不能自行增减后缀。应为 {expected}；实际为 {actual}。按上述顺序返回每页，保留已规划的画面内容。')");p.write_text(s)
