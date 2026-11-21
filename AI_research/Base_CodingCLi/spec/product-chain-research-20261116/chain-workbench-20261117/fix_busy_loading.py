from pathlib import Path
p=Path('runtime/ui/src/pages/Chains.tsx');s=p.read_text().replace("const [error,setError]=useState('');", "const [error,setError]=useState('');const [loadError,setLoadError]=useState('');")
s=s.replace('await Promise.all(requests);','await Promise.all(requests);if(route.current===requested)setLoadError(\'\');')
s=s.replace("setNotice('');setError('');setBusy(false)","setNotice('');setError('');setLoadError('');setBusy(false)")
s=s.replace("void load().catch(e=>setError(msg(e)));", "void load().catch(e=>{if(route.current===key)setLoadError(msg(e))});")
s=s.replace("api<Chain>('/api/chains/'+key).then(d=>{if(!stopped&&route.current===key)setData(d)}),", "loadError?load():api<Chain>('/api/chains/'+key).then(d=>{if(!stopped&&route.current===key)setData(d)}),")
s=s.replace('},[key,edit,runId]);','},[key,edit,runId,load,loadError]);')
s=s.replace('{error&&<div role="alert"','{(error||loadError)&&<div role="alert"').replace('text-sm text-danger">{error}<button','text-sm text-danger">{error||loadError}<button')
p.write_text(s)
p=Path('runtime/web/services/video_executor.py');s=p.read_text()
a='    if status in ("DRAFT", "APPROVED") and script.get("current_version"):\n'
b='''    if status == "APPROVED":
        # Following an existing render (or waiting for capacity) spends no new
        # attempt. Keep the history check at the point a new render can start,
        # instead of rereading the entire archive on every progress poll.
        renders = store.list_renders(script["id"])
        current = [row for row in renders if row["version"] == script["approved_version"]]
        if not can_start_render or any(row["status"] in (*store.ACTIVE_RENDER_STATUSES, "COMPLETED") for row in current):
            return _render_auto(script, can_start_render, out, retries)
'''
assert a in s;s=s.replace(a,b+a);p.write_text(s)
