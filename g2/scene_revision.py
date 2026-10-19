"""Versioned screen-only changes and media settings over a verified parent."""
from copy import deepcopy
import json,os,shutil,sys
from pathlib import Path
from g2.scene_fields import apply_changes
from g2.media_settings import retime_ir,write_captions


def run_revision(p):
    from g1.video_ir import validate_video_ir
    p.RUN_ROOT.mkdir(parents=True,exist_ok=False);p.ARTIFACTS.mkdir();p.LOGS.mkdir()
    saved=json.loads(Path(os.environ['AUTOVIDEO_G2_INTENT_FILE']).read_text())
    parent=Path(os.environ['AUTOVIDEO_G2_SOURCE_ATTEMPT_DIR'])/'g2-run'
    p._copy_tree_without_symlinks(parent/'fixtures',p.FIXTURE_ROOT)
    (p.ARTIFACTS/'llm_calls').mkdir()
    for path in (parent/'artifacts').glob('*.json'):
        if path.is_symlink():raise p.G2Fault('REVISION_SOURCE_INVALID','原制品路径不安全')
        if path.name not in {'manifest.json','model-cost.json','qa.json','scene-revision.json','media-revision.json'}:shutil.copy2(path,p.ARTIFACTS/path.name)
    for name in ['captions.srt','prepared-source.txt']:
        path=parent/'artifacts'/name
        if path.is_file():shutil.copy2(path,p.ARTIFACTS/name)
    if (p.ARTIFACTS/'prepared-source.txt').exists():p.PREPARED_SOURCE=(p.ARTIFACTS/'prepared-source.txt').read_text()
    ir=json.loads(p.IR_PATH.read_text());before=deepcopy(ir)
    narration=json.loads((p.ARTIFACTS/'final-narration-plan.json').read_text())
    voice_changed=saved['voice_id']!=saved.get('parent_voice_id','zf_xiaoxiao')
    alignment={}
    p.skip_step('narration-plan','复用原讲稿')
    if not voice_changed:p.skip_step('tts-and-align','复用原配音')
    if saved['operation_type']=='visual_revision':
        for name in ['information-plan.json','diagram-plan.json']:
            path=p.ARTIFACTS/name
            if path.exists():path.replace(p.ARTIFACTS/('parent-'+name))
        target=saved['scene_id'];old=next(s for s in ir['scenes'] if s['id']==target)
        if saved.get('scene_instruction'):
            replacement=p.run_step('storyboard-plan',replan_scene,p,old,narration,ir,saved['scene_instruction'])
            old.update({k:replacement[k] for k in ['component','props','beats','purpose','visual_intent']})
        else:
            updated=p.run_step('storyboard-plan',apply_changes,old,saved['scene_changes']);old['props']=updated['props']
            if old['component']=='flow_diagram':
                from g2.process_diagram import layout_request,LAYOUT_SCRIPT
                req=p.ARTIFACTS/'scene-layout-request.json';p.write_json(req,layout_request({'scenes':[{'id':old['id'],**old['props']}]}))
                layout=json.loads(p._run_fixed_command('scene-layout',[p.NODE,LAYOUT_SCRIPT,req],p.PROJECT))
                old['props']['layout']=layout['scenes'][0]['layout']
            else:
                from g2.information_motion import validate_text_fit
                validate_text_fit(old)
        for scene in ir['scenes']:
            if scene['id']!=target and scene!=next(s for s in before['scenes'] if s['id']==scene['id']):
                raise p.G2Fault('REVISION_SCOPE_CHANGED','非目标场景出现变化')
        p.write_json(p.ARTIFACTS/'scene-revision.json',{'parent_job_id':saved['parent_job_id'],'scene_id':target,'changes':saved.get('scene_changes',{}),'instruction':saved.get('scene_instruction',''),'other_scenes_unchanged':True,'narration_unchanged':True,'audio_unchanged':True})
    elif voice_changed:
        # Preserve exact narration and screen props. Only timing follows the new voice.
        base=p.ASSETS/'voice-normal-speed.wav'
        if base.exists():base.unlink()
        alignment=p.run_step('tts-and-align',p.synthesize_and_align,narration)
        rows={r['id']:r for r in alignment['rows']};old_rows={r['id']:r for r in ir['narration']['segments']}
        board={'scenes':deepcopy(ir['scenes'])}
        for scene in board['scenes']:
            for b in scene['beats']:
                t=b['trigger'];old=old_rows[t['segment_id']];new=rows[t['segment_id']]
                t['offset_ms']=round(t['offset_ms']*(new['end_ms']-new['start_ms'])/max(1,old['end_ms']-old['start_ms']))
        generated=p.compile_ir(narration,board,alignment)
        # Presentation and chapter identities stay as saved in the parent.
        for key in ['presentation','content_structure']:
            if key in before:generated[key]=deepcopy(before[key])
        ir=generated
        ir=retime_ir(p,ir,p.OUTPUT_SPEED)
    elif saved['operation_type']=='media_revision':
        ir=retime_ir(p,ir,p.OUTPUT_SPEED)
    if 'playback' not in ir and (p.ARTIFACTS/'playback.json').exists():
        (p.ARTIFACTS/'playback.json').unlink()
    if saved['operation_type']=='media_revision':p.skip_step('storyboard-plan','复用原画面内容')
    p.skip_step('draw-things-backgrounds','本修订不生成背景图')
    compiled=p.run_step('compile-ir',validate_video_ir,ir,p.FIXTURE_ROOT)
    p.write_json(p.IR_PATH,ir);p.write_json(p.ARTIFACTS/'compiled-timeline.json',compiled)
    # Keep exported storyboard consistent for further revisions.
    p.write_json(p.ARTIFACTS/'storyboard-plan.json',{'scenes':[{k:v for k,v in s.items() if k not in {'start_ms','duration_ms'}} for s in ir['scenes']]})
    if 'content_structure' in ir:
        from g2.content_video import write_chapter_evidence
        write_chapter_evidence(p,narration,compiled)
    write_captions(p,ir['narration']['segments'])
    if saved['operation_type']=='media_revision':
        p.write_json(p.ARTIFACTS/'media-revision.json',{'parent_job_id':saved['parent_job_id'],'voice_id':p.VOICE,'video_speed':p.OUTPUT_SPEED,'voice_resynthesized':voice_changed,'narration_unchanged':True,'screen_content_unchanged':all(a['props']==b['props'] for a,b in zip(before['scenes'],ir['scenes']))})
    renderer=p.RUN_ROOT/'renderer'
    p.run_step('render',lambda:p.render(compiled,renderer_root=renderer))
    qa=p.run_step('verify-media',p.verify_media,compiled,alignment)
    qa.update(gate='REVISION',operation=saved['operation_type'],real_tts=voice_changed,real_llm=bool(p.CALL_RECORDS),reused_narration=True,video_form=saved['video_form'],voice_id=p.VOICE,video_speed=p.OUTPUT_SPEED)
    p.write_json(p.ARTIFACTS/'qa.json',qa);p.write_call_summary();p.write_manifest(renderer_root=renderer)
    return qa


def replan_scene(p,old,narration,ir,instruction):
    from g2 import information_motion as motion,process_diagram as diagram
    is_info=ir.get('presentation',{}).get('preset')=='information_motion'
    module=motion if is_info else diagram
    if old['component']=='source_card':raise p.G2Fault('SCENE_REVISION_SCOPE','来源卡的文字来自请求的 sources，不能重排；请修改 sources 后重新生成')
    ids=old['narration_segment_ids'];segments=[s for s in narration['segments'] if s['id'] in ids]
    source=p.PREPARED_SOURCE if p.PREPARED_SOURCE is not None else ir['source']['original_text']
    context={'source_text':source,'narration_plan':{'title':narration['title'],'core_message':narration['core_message'],'segments':segments}}
    if ir.get('presentation',{}).get('density')=='large':context['large_text']=True
    def messages(ctx,correction=None):
        result=module.messages(ctx,correction)
        result[0]['content']+='\n本次只重排一个选定场景，必须恰好返回一个场景，id=s1。旁白文字及覆盖段落不能改变。用户的画面要求是编辑偏好，不是新增事实来源。'
        payload=json.loads(result[-1]['content']);payload.update(current_scene=old,visual_edit_request=instruction)
        result[-1]['content']=json.dumps(payload,ensure_ascii=False);return result
    def check(plan,ctx):
        if len(plan.get('scenes',[]))!=1:raise p.G2Fault('SCENE_REVISION_SCOPE','必须只返回所选的一个场景')
        try:module.validate_plan(plan,ctx)
        except (motion.InformationFault,diagram.DiagramPlanFault) as exc:raise p.G2Fault(exc.code,exc.message) from exc
    plan=p.generate_validated_plan('scene-revision',messages,module.SCHEMA,check,context)
    plan['scenes'][0]['id']=old['id']
    rows=ir['narration']['segments'];alignment={'rows':rows}
    board=module.compile_storyboard(plan,alignment,p)
    return board['scenes'][0]
