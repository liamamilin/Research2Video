from fastapi import APIRouter,Depends,HTTPException
from web.deps import require_editor,require_viewer
from web.services import chains,chain_runner,source_registry
from core import sources
router=APIRouter(prefix='/api/chains',tags=['chains'])

@router.get('')
def listing(user=Depends(require_viewer)):
    return {'items':chains.summary(),'archived':chains.all_chains(True),'role':user['role']}

@router.get('/catalog')
def catalog(user=Depends(require_viewer)):
    snap=source_registry.snapshot()
    return {**snap,'chains':[{'key':m['key'],'job':m['job'],'name':m['name']} for m in chains.all_chains()]}

@router.post('/sources/preview')
def source_preview(body:dict,user=Depends(require_editor)):
    specs=sources.resolve(body.get('sources',[]),chains.paths().config_dir)
    results=[]
    for spec in specs[:4]:
        try:
            items=sources.fetch_source(spec,limit=3,timeout=15)
            results.append({'spec':spec,'items':[{'title':x.title,'url':x.url,'date':x.publish_date} for x in items],
                            'error':'' if items else '未取得条目，请检查来源类型或地址'})
        except Exception as e:results.append({'spec':spec,'items':[],'error':str(e)})
    return {'results':results}

@router.post('',status_code=201)
def create(body:dict,user=Depends(require_editor)):
    try:return chains.create(body,user)
    except (ValueError,KeyError) as e:raise HTTPException(422,str(e)) from e

@router.get('/runs/{rid}')
def run_detail(rid:str,user=Depends(require_viewer)):
    return chain_runner.get(rid)

@router.post('/runs/{rid}/cancel')
def cancel(rid:str,user=Depends(require_editor)):
    return chain_runner.cancel(rid)

@router.post('/runs/{rid}/retry')
def retry(rid:str,user=Depends(require_editor)):
    return chain_runner.retry(rid,user)

@router.get('/{key}')
def get(key:str,user=Depends(require_viewer)):
    return chains.detail(key)

@router.put('/{key}')
def update(key:str,body:dict,user=Depends(require_editor)):
    try:return chains.update(key,body,user)
    except (ValueError,KeyError) as e:raise HTTPException(422,str(e)) from e

@router.post('/{key}/toggle')
def toggle(key:str,body:dict,user=Depends(require_editor)):
    return chains.toggle(key,body,user)

@router.get('/{key}/delete-preview')
def delete_preview(key:str,user=Depends(require_viewer)):
    return chains.delete_preview(key)

@router.delete('/{key}')
def delete(key:str,revision:str,user=Depends(require_editor)):
    return chains.delete(key,revision)

@router.post('/{key}/run',status_code=202)
def run(key:str,body:dict,user=Depends(require_editor)):
    return chain_runner.start(key,body,user)


@router.get('/{key}/scope')
def scope(key:str,user=Depends(require_viewer)):
    return chains.scope(key)
