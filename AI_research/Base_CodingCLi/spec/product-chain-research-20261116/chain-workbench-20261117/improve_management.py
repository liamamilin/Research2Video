from pathlib import Path
root=Path(__file__).parent/'runtime'
def edit(p,old,new):
 f=root/p;s=f.read_text();assert old in s,(p,old[:80]);f.write_text(s.replace(old,new))
edit('core/source_supply.py','    return next((p for p in profiles(config_dir).values() if job.get("_file") in p.get("jobs", {})), None)','    if job.get("source_supply_enabled") is False:\n        return None\n    return next((p for p in profiles(config_dir).values() if job.get("_file") in p.get("jobs", {})), None)')
edit('core/source_supply.py','specs = list(dict.fromkeys([r[\'spec\'] for r in coverage] + declared + [r["spec"] for r in automatic]))','specs = resolve(list(dict.fromkeys([r[\'spec\'] for r in coverage] + declared + [r["spec"] for r in automatic])), engine.config_dir)')
edit('web/services/source_registry.py',"        if refs:\n            result.append", "        if entry.get('spec') in (data.get('sources') or []):\n            refs.append(entry['spec'])\n        from core import source_supply, source_coverage\n        profile=source_supply.for_job(paths.config_dir,{**data,'_file':job})\n        if profile and any(x['spec']==entry.get('spec') for x in source_coverage.targets(profile,job)):\n            refs.append(entry['spec'])\n        if refs:\n            result.append")
p=root/'web/services/chains.py';s=p.read_text();s=s.replace("    refs=[]\n", "    from core import source_supply, source_coverage\n    profile=next((p for p in source_supply.profiles(paths().config_dir).values() if meta['job'] in p.get('jobs',{})),None)\n    supply=None\n    if profile:\n        supply={'key':profile['key'],'enabled':job.get('source_supply_enabled',True),'pool_per_job':profile.get('pool_per_job',4),'targets':source_coverage.targets(profile,meta['job'])}\n    refs=[]\n")
s=s.replace("'spec':e.get('spec',ref),'used_by':", "'spec':e.get('spec',ref),'members':[{'key':rk,'name':rv.get('name',rk),'spec':rv['spec'],'enabled':rv.get('enabled',True)} for rk,rv in registry.items() if str(ref).startswith('tag:') and ref[4:] in rv.get('tags',[])],'used_by':")
s=s.replace("    plans=[p for p in schedule_store().list() if p['job_name']==meta['job']]\n", "    policy=production_policy.load(paths().config_dir)\n    window=(policy.get('jobs',{}).get(meta['job']) or policy.get('default') or {}).get('days',7)\n    for script in scripts:\n        version=store.get_version(script['id'],script.get('current_version',0)) or {}\n        script['title']=version.get('title') or script.get('topic_title') or script['id']\n    plans=[p for p in schedule_store().list() if p['job_name']==meta['job']]\n")
s=s.replace("'sources':refs,'matrix':", "'source_supply':supply,'window_days':window,'sources':refs,'matrix':")
s=s.replace("('sources','prompt','schedule')", "('sources','prompt','schedule','source_supply_enabled','window_days')")
s=s.replace("        write(jpath,job)\n", "        if 'source_supply_enabled' in body:job['source_supply_enabled']=bool(body['source_supply_enabled'])\n        if 'window_days' in body:\n            days=int(body['window_days'])\n            if not 1<=days<=90:error('研究窗口需要为 1–90 天',422)\n            policy=read(cfg()/'production_policy.yaml');policy.setdefault('jobs',{}).setdefault(m['job'],{}).update(mode='rolling',days=days,timezone='Asia/Shanghai');write(cfg()/'production_policy.yaml',policy)\n        write(jpath,job)\n")
old="""            if active and not fn.__name__.startswith(('create','add','copy')):
                error('有链路正在运行。请等待或先停止该批次，再修改共享配置；本次设置尚未保存')
            result=fn(*args,**kwargs)"""
new="""            def dependencies(row):
                job=load_job(paths().jobs_dir,row['job']) or {}
                ps=production.load_all(paths().config_dir,paths().jobs_dir)
                owner=matrices.of_job(row['job'],paths().config_dir)
                selected=[ps[x] for x in row.get('outputs',[]) if x in ps]
                registry=sources.load_registry(paths().config_dir)
                refs=job.get('sources',[])
                used={k:v for k,v in registry.items() if 'source:'+k in refs or v['spec'] in refs or any('tag:'+t in refs for t in v.get('tags',[]))}
                return {'job':job,'sources':used,'matrix':read(cfg()/'matrices'/f'{owner.key}.yaml') if owner else None,
                        'outputs':[{k:v for k,v in p.as_dict().items() if k!='auto'} for p in selected],
                        'recipes':[read(cfg()/'video_recipes'/f'{p.recipe}.yaml') for p in selected],
                        'columns':[content_columns.snapshot(paths().config_dir,p.key,row['job']) for p in selected]}
            frozen={r['id']:dependencies(r) for r in active}
            plans_before={p['id']:p for p in schedule_store().list()}
            result=fn(*args,**kwargs)
            if any(dependencies(r)!=frozen[r['id']] for r in active):
                error('此修改会影响正在运行的链路，请等待该批次结束；其他链路可继续管理')
            for plan in schedule_store().list():
                prior=plans_before.get(plan['id'])
                if not prior or any(plan[k]!=prior[k] for k in ('version','enabled')):
                    for chain in all_chains():
                        if chain['job']==plan['job_name']:
                            chain['schedule_armed']=bool(plan['enabled']);remember(chain)"""
assert old in s;s=s.replace(old,new);p.write_text(s)
