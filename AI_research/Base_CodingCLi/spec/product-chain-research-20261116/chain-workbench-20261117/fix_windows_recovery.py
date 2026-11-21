from pathlib import Path
r=Path(__file__).parent/'runtime'
p=r/'web/services/chains.py';s=p.read_text();s=s.replace("window=(policy.get('jobs',{}).get(meta['job']) or policy.get('default') or {}).get('days',7)","window=policy.get('jobs',{}).get(meta['job']) or policy.get('window') or {'mode':'rolling','days':7}")
s=s.replace("'window_days':window,", "'window_days':window.get('days',7),'window_mode':window.get('mode','rolling'),")
s=s.replace("'source_supply_enabled','window_days')", "'source_supply_enabled','window_days','window_mode')")
s=s.replace("        if 'window_days' in body:\n            days=int(body['window_days'])", "        if 'window_days' in body or 'window_mode' in body:\n            days=int(body.get('window_days',7))")
s=s.replace("policy.setdefault('jobs',{}).setdefault(m['job'],{}).update(mode='rolling',days=days,timezone='Asia/Shanghai');write(cfg()/'production_policy.yaml',policy)","mode=body.get('window_mode','rolling')\n            if mode not in ('rolling','previous_slot'):error('请选择滚动窗口或相邻周期',422)\n            policy.setdefault('jobs',{})[m['job']]={'mode':mode,'timezone':'Asia/Shanghai',**({'days':days} if mode=='rolling' else {})};write(cfg()/'production_policy.yaml',policy)")
# Archived details retain the research context at removal, current history and runs remain queryable.
s=s.replace("    return {**meta,'revision':digest(meta)","    result={**meta,'revision':digest(meta)")
needle="'scripts':scripts[:100], 'script_count':len(scripts),'runs':runs(key)}"
s=s.replace(needle,needle+"\n    if meta.get('archived') and meta.get('snapshot'):\n        for field in ('research','sources','matrix','outputs_detail','source_supply','window_days','window_mode'):\n            result[field]=meta['snapshot'].get(field,result.get(field))\n    return result")
p.write_text(s)
p=r/'ui/src/pages/Chains.tsx';s=p.read_text().replace('type Chain = {window_days:number;','type Chain = {window_mode:string;window_days:number;');s=s.replace('source_supply_enabled:boolean;window_days:number}', 'source_supply_enabled:boolean;window_days:number;window_mode:string}')
s=s.replace('window_days:data.window_days||7}', "window_days:data.window_days||7,window_mode:data.window_mode||'rolling'}")
s=s.replace('研究最近 {data.window_days||7} 天的材料，显式来源 {data.sources.length} 项。', "{data.window_mode==='previous_slot'?'按相邻周期之间的时间范围研究':`研究最近 ${data.window_days||7} 天的材料`}，显式来源 {data.sources.length} 项。")
s=s.replace('<label className="block text-sm">研究时间范围（天）', '<label className="block text-sm">研究时间范围<select className={field} value={draft.window_mode} onChange={e=>setDraft({...draft,window_mode:e.target.value})}><option value="rolling">最近若干天</option><option value="previous_slot">相邻周期之间（沿用生产策略）</option></select></label><label className="block text-sm">滚动窗口天数（仅选择最近若干天时生效）')
s=s.replace('window_days:draft.window_days,source_supply_enabled:draft.source_supply_enabled,', "...(draft.window_days!==data.window_days||draft.window_mode!==data.window_mode?{window_days:draft.window_days,window_mode:draft.window_mode}:{}),source_supply_enabled:draft.source_supply_enabled,")
p.write_text(s)
p=r/'web/services/chain_runner.py';s=p.read_text().replace("if not chosen:chains.error", "if not chosen and not body.get('schedule_run_id'):chains.error")
s=s.replace("        ps=production.load_all", "        if not row['outputs']:\n            row.update(status='DONE',report=report,stage='RESEARCH',note='研究报告完成；视频输出已暂停，本次没有制作视频',finished_at=time.time());put(row);return\n        ps=production.load_all")
s=s.replace('def start_loop():\n', '''def recover_owned():
    # Only this workbench's batches are resumed, never copied historic work.
    from web.services import video_render
    with chains.db() as d:rows=[json.loads(r['data']) for r in d.execute('SELECT data FROM runs')]
    for row in rows:
        if row['status'] not in ('RUNNING','CANCELLING') or not row.get('batch_id'):continue
        for item in (ap.get_run(row['batch_id']) or {}).get('items',[]):
            sid=item.get('script_id')
            if not sid:continue
            script=store.get_script(sid) or {}
            if script.get('status') in ('RESEARCHING','WRITING'):
                store.update_script(sid,status='FAILED',error='隔离应用重启时写稿中断，正在从该步骤恢复')
            for render in store.list_renders(sid):
                if render['status'] in ('SUBMITTING','QUEUED','RUNNING','DOWNLOADING'):video_render.start(render['id'])

def start_loop():
    recover_owned()
''');p.write_text(s)
