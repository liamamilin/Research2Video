"""Read verified scene inputs and queue isolated revision jobs."""
from pathlib import Path
import hashlib,json,subprocess
from uuid import UUID,uuid4
from g2.scene_fields import editable_fields,apply_changes
from g3.store import StoreError


def source(store,jid, *, allow_legacy=False):
    parent=store.get_job(jid)
    if parent['status']!='COMPLETED':raise StoreError('REVISION_NOT_ALLOWED','请先等待本任务完成。')
    if not allow_legacy and (parent.get('resolved_video_form') or parent.get('video_form')) not in {'process_diagram','information_motion','kinetic_typography','image_slideshow','chat_dialogue','information_cards','object_showcase','dynamic_collage'}:
        raise StoreError('REVISION_FORM_UNSUPPORTED','场景与配音修订适用于流程图解和简洁信息动效。')
    paths={}
    for name in ['video-ir.json','video.mp4','narration.json','manifest.json']:
        paths[name]=store.artifact_path(jid,name)[0]
    if allow_legacy:
        # Music revisions consume this original rather than the already mixed video.
        try:
            paths['unmixed.mp4']=store.artifact_path(jid,'unmixed.mp4')[0]
        except StoreError as exc:
            if exc.code!='ARTIFACT_NOT_FOUND':raise
    attempt=store.job_root/jid/'attempts'/str(parent['attempt_count']);base=attempt.resolve(strict=True)
    manifest=json.loads(paths['manifest.json'].read_text())
    project=Path(__file__).resolve().parents[1]
    files={str((project/f['path']).resolve()):f['sha256'] for f in manifest['files']}
    for folder in ['fixtures','artifacts']:
        root=base/'g2-run'/folder
        if root.is_symlink():raise StoreError('REVISION_SOURCE_INVALID','原制品路径不安全。')
        for path in root.rglob('*'):
            if path.is_symlink():raise StoreError('REVISION_SOURCE_INVALID','原制品含链接，暂不能复用。')
            if path.is_file() and (folder=='fixtures' or path.suffix=='.json'):
                if path.name=='manifest.json':continue
                if not path.resolve().is_relative_to(base) or files.get(str(path.resolve()))!=hashlib.sha256(path.read_bytes()).hexdigest():
                    raise StoreError('REVISION_SOURCE_CHANGED','原制品完整性校验失败，暂不能修订。')
    ir=json.loads(paths['video-ir.json'].read_text())
    return parent,ir,base


def scenes(store,jid):
    _,ir,_=source(store,jid);segments={s['id']:s for s in ir['narration']['segments']}
    return [{'scene_id':s['id'],'component':s['component'],'start_ms':s['start_ms'],'end_ms':s['start_ms']+s['duration_ms'],
             'title':s['props'].get('title') or s.get('purpose') or s['id'],
             'narration':'\n'.join(segments[sid]['text'] for sid in s['narration_segment_ids']),
             'fields':editable_fields(s),'preview_url':f"/api/jobs/{jid}/scenes/{s['id']}/preview"} for s in ir['scenes']]


def create_revision(store,jid,request,kind):
    parent,ir,_=source(store,jid);draft=store.editable_content(jid)
    settings=draft['options'];settings['video_form']=parent.get('resolved_video_form') or parent['video_form']
    payload={**settings,'title':(parent['title'][:70]+' · '+('画面修订' if kind=='visual_revision' else '声音与速度')),
             'source_text':draft['source_text'],'creative_intent':draft['creative_intent'],'use_draw_things':False,
             'operation_type':kind,'parent_job_id':jid,'parent_attempt_no':parent['attempt_count'],
             'parent_voice_id':parent['voice_id'],'voice_id':parent['voice_id'],'video_speed':parent.get('video_speed',1)}
    if kind=='visual_revision':
        if settings['video_form'] in {'kinetic_typography','image_slideshow','chat_dialogue','information_cards','object_showcase','dynamic_collage'}:
            raise StoreError('REVISION_FORM_UNSUPPORTED','此形式请通过讲稿修订调整画面文字；声音与速度可以单独修订。')
        selected=next((s for s in ir['scenes'] if s['id']==request['scene_id']),None)
        if selected is None:raise StoreError('SCENE_NOT_FOUND','场景不存在。')
        changes=request.get('changes',{})
        try:
            candidate=apply_changes(selected,changes)
            if changes:
                from g1.video_ir import _validate_component
                _validate_component(candidate['component'],candidate['props'],'/scenes/selected')
                if candidate['component']!='flow_diagram':
                    from g2.information_motion import validate_text_fit
                    validate_text_fit(candidate)
        except (ValueError,RuntimeError) as exc:raise StoreError('SCENE_CHANGES_INVALID',str(exc)) from exc
        payload.update(scene_id=selected['id'],scene_changes=changes,scene_instruction=request.get('instruction',''))
    else:
        for key in ['voice_id','video_speed']:
            if request.get(key) is not None:payload[key]=request[key]
    return store.create_job(str(uuid4()),payload)


def preview(store,settings,jid,sid):
    # Only registered video and IR are needed for read-only preview.
    # The scene times in the IR are content times, so frames come from the content-only video when packaged.
    try:video,_=store.artifact_path(jid,'content.mp4')
    except StoreError:video,_=store.artifact_path(jid,'video.mp4')
    ir_path,_=store.artifact_path(jid,'video-ir.json')
    ir=json.loads(ir_path.read_text());scene=next((s for s in ir['scenes'] if s['id']==sid),None)
    if scene is None:raise StoreError('SCENE_NOT_FOUND','场景不存在。')
    UUID(jid)
    if not __import__('re').fullmatch(r'[a-z][a-z0-9_]{0,47}',sid):raise StoreError('SCENE_NOT_FOUND','场景不存在。')
    root=store.job_root/jid/'previews';root.mkdir(exist_ok=True)
    target=root/(sid+'.png')
    if target.is_symlink():raise StoreError('PREVIEW_INVALID','预览路径无效。')
    if not target.exists():
        segments={s['id']:s for s in ir['narration']['segments']};cues=[]
        for b in scene['beats']:
            t=b['trigger'];cues.append(segments[t['segment_id']][t['boundary']+'_ms']+t['offset_ms'])
        time=min(scene['start_ms']+scene['duration_ms']-34,max(cues,default=scene['start_ms'])+400)/1000
        result=subprocess.run([str(settings.ffmpeg),'-nostdin','-v','error','-ss',str(time),'-i',str(video),'-frames:v','1','-vf','scale=640:-1','-y',str(target)],capture_output=True,timeout=30)
        if result.returncode:raise StoreError('PREVIEW_FAILED','暂时无法提取场景预览。')
    return target
