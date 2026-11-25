from pathlib import Path
p=Path('runtime/ui/src/pages/Chains.tsx')
s=p.read_text()
s=s.replace('这条链路已删除，历史运行与产出保留。','这条链路已删除。下方是删除前的只读配置，历史运行、报告与产出仍可查看。')
a='<Link className="btn" to={link(\'/jobs/\'+encodeURIComponent(data.job))}>'
b='<Link className="btn" to={link(\'/sources\')}>信息源库</Link>{role===\'admin\'&&<Link className="btn" to={link(\'/scheduler\')}>周期调度</Link>}'
s=s.replace(a,'{!data.archived&&<>'+a).replace(b,b+'</>}')
s=s.replace('<p className="text-xs text-text-muted">{data.policy_managed?', '{!data.archived&&<p className="text-xs text-text-muted">{data.policy_managed?')
s=s.replace('当前手动运行，保存周期后可自动执行。\'}</p>','当前手动运行，保存周期后可自动执行。\'}</p>}')
s=s.replace("{x.schedule_armed&&x.schedule?.enabled?'周期已启用':x.schedule?'已保留周期 · 未在隔离环境接管':'手动运行'}", "{x.schedule?(x.schedule.enabled?(x.schedule_armed?'周期已启用':'已保留周期 · 未在隔离环境接管'):'研究周期已暂停'):'手动运行'}")
s=s.replace('<select aria-label="本次单条视频数量"','{data.outputs_detail.some(o=>o.auto&&o.mode!==\'episode\')&&<select aria-label="本次单条视频数量"')
s=s.replace('<option value={0}>全部可用选题</option></select>','<option value={0}>全部可用选题</option></select>}')
s=s.replace('<Link className="btn text-sm" to={link(\'/jobs/\'+encodeURIComponent(data?.job||\'\'))} onClick={()=>setRun(null)}>研究日志</Link>', '{!data?.archived&&<Link className="btn text-sm" to={link(\'/jobs/\'+encodeURIComponent(data?.job||\'\'))} onClick={()=>setRun(null)}>研究日志</Link>}')
s=s.replace('<Link className="text-accent" to={link(\'/video-recipes/\'+o.recipe)}>文稿与画面配方</Link>', '{data.archived?<span>删除前文稿与画面配方</span>:<Link className="text-accent" to={link(\'/video-recipes/\'+o.recipe)}>文稿与画面配方</Link>}')
p.write_text(s)
