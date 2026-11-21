from pathlib import Path
p=Path('runtime/ui/src/pages/Chains.tsx');s=p.read_text();a=s.index('  const load=');b=s.index('  useEffect',a)
s=s[:a]+'''  const load=useCallback(async()=>{
    const requested=key;
    const requests:Promise<unknown>[]=[api<Catalog>('/api/chains/catalog').then(cat=>{if(route.current===requested)setCatalog(cat)})];
    if(key&&key!=='new')requests.push(api<Chain>('/api/chains/'+key).then(detail=>{if(route.current===requested)setData(detail)}));
    else if(!key)requests.push(api<{items:Chain[];archived:Chain[]}>('/api/chains').then(list=>{if(route.current===requested){setItems(list.items);setArchived(list.archived)}}));
    await Promise.all(requests);
  },[key]);
''' +s[b:];a=s.index("  useEffect(()=>{if(!key||key==='new'||edit)return;");b=s.index('  async function act',a)
s=s[:a]+'''  useEffect(()=>{
    if(!key||key==='new'||edit)return;
    let stopped=false;let timer:number|undefined;
    async function poll(){
      if(stopped)return;
      await Promise.allSettled([
        api<Chain>('/api/chains/'+key).then(d=>{if(!stopped&&route.current===key)setData(d)}),
        ...(runId?[api<Run>('/api/chains/runs/'+runId).then(r=>{if(!stopped&&route.current===key)setRun(r)})]:[]),
      ]);
      if(!stopped)timer=window.setTimeout(()=>void poll(),6000);
    }
    timer=window.setTimeout(()=>void poll(),6000);
    return()=>{stopped=true;clearTimeout(timer)};
  },[key,edit,runId]);
''' +s[b:];p.write_text(s)
