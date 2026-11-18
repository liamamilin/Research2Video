from pathlib import Path
r=Path(__file__).resolve().parent/'runtime'
p=r/'ui/src/pages/Chains.tsx';s=p.read_text()
old='<label className="block text-sm">每天时间（北京时间）<input type="time" className={field} value={wizard.schedule.split(\' \')[1].padStart(2,\'0\')+\':\'+wizard.schedule.split(\' \')[0].padStart(2,\'0\')} onChange={e=>{const [h,m]=e.target.value.split(\':\');setWizard({...wizard,schedule:`${Number(m)} ${Number(h)} * * *`})}}/></label>'
assert old in s;s=s.replace(old,'<DailyTime value={wizard.schedule} onChange={schedule=>setWizard({...wizard,schedule})}/>')
old='<label className="block text-sm">每天时间（北京时间）<input type="time" className={field} value={(value.split(\' \')[1]||\'10\').padStart(2,\'0\')+\':\'+(value.split(\' \')[0]||\'0\').padStart(2,\'0\')} onChange={e=>{const [h,m]=e.target.value.split(\':\');onChange(`${Number(m)} ${Number(h)} * * *`)}}/></label>'
assert old in s;s=s.replace(old,'<DailyTime value={value} onChange={onChange}/>')
s+='''
function DailyTime({value,onChange}:{value:string;onChange:(value:string)=>void}){
 const parts=value.split(' ');const hour=Number(parts[1]||10);const minute=Number(parts[0]||0);
 return <fieldset className="space-y-2"><legend className="text-sm">每天时间（北京时间）</legend><div className="flex items-center gap-2"><select aria-label="每天小时（北京时间）" className="input" value={hour} onChange={e=>onChange(`${minute} ${e.target.value} * * *`)}>{Array.from({length:24},(_,n)=><option key={n} value={n}>{String(n).padStart(2,'0')} 时</option>)}</select><select aria-label="每天分钟" className="input" value={minute} onChange={e=>onChange(`${e.target.value} ${hour} * * *`)}>{Array.from({length:60},(_,n)=><option key={n} value={n}>{String(n).padStart(2,'0')} 分</option>)}</select></div></fieldset>;
}
''';p.write_text(s)
p=r/'ui/src/pages/Scheduler.tsx';s=p.read_text();s=s.replace('const warnings = [...new Set([...(managed.data?.warnings || []), ...(system.data?.warnings || [])])];','const warnings = [...new Set([...(managed.data?.warnings || []), ...(!chainScope.key?system.data?.warnings || []:[])])];')
s=s.replace('const removed = system.data?.removed_system_jobs || [];','const removed = chainScope.key?[]:system.data?.removed_system_jobs || [];').replace('const others = system.data?.others || [];','const others = chainScope.key?[]:system.data?.others || [];')
s=s.replace('const filteredJobs = schedulable.filter(job =>','const filteredJobs = schedulable.filter(job => chainScope.job(job.name) &&')
s=s.replace('const hasLaunchd = jobs.some(job => job.backend === "launchd");','const scopedJobs=jobs.filter(j=>chainScope.job(j.job_name||""));\n  const hasLaunchd = !chainScope.key && jobs.some(job => job.backend === "launchd");')
s=s.replace('const needsAttention = Object.values(states).filter(s => ["overdue", "unverified", "no_schedule"].includes(s.status)).length;','const needsAttention = scopedJobs.filter(j=>["overdue", "unverified", "no_schedule"].includes(states[j.id]?.status)).length;')
s=s.replace('jobs.filter(j => j.enabled).length','scopedJobs.filter(j => j.enabled).length').replace('jobs.filter(j => !j.enabled).length','scopedJobs.filter(j => !j.enabled).length').replace('{visibleSchedules.length} / {jobs.length}','{visibleSchedules.length} / {scopedJobs.length}')
s=s.replace('。新 job 使用托管计划，关闭页面或重启 Web 不影响执行。','。{dispatcher.detail?.includes("隔离")?"关闭浏览器后继续执行；重新启动隔离应用后恢复已保存计划。":"新 job 使用托管计划，关闭页面或重启 Web 不影响执行。"}')
p.write_text(s)
