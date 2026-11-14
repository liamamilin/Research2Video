"""Real local narration, forced alignment and repeated emphasis; no model planner."""
import argparse,hashlib,json,os,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3];sys.path.insert(0,str(ROOT));HERE=Path(__file__).parent
p=argparse.ArgumentParser();p.add_argument('--split',required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args();out=a.output.resolve()
out.mkdir(parents=True,exist_ok=False)
os.environ['AUTOVIDEO_G2_RUN_DIR']=str(out);os.environ['AUTOVIDEO_G2_VOICE_ID']='voxcpm_anchor';os.environ['AUTOVIDEO_G2_DURATION_MODE']='content'
from g2 import run_g2 as engine,visual_language,information_motion,caption_alignment
for path in [engine.ARTIFACTS,engine.LOGS,engine.ASSETS]:path.mkdir(parents=True,exist_ok=True)
c=next(c for c in json.loads((HERE/f'{a.split}.json').read_text()) if c['family']=='timing')
plan={'segments':[{'id':f'n{i}','text':t} for i,t in enumerate(c['segments'])]}
alignment=engine.synthesize_and_align(plan);rows={r['id']:r for r in alignment['rows']}
scene={'id':'repeat','component':'concept_reveal','purpose':'对象先出现，随后强调结果','visual_intent':{},'narration_segment_ids':list(rows),
       'props':{'title':'重复短语：强调属于当前对象','lines':[c['introduction']+'：'+c['emphasis']],'visuals':[{'target':'line:0','icon':'file','action':'complete','reveal_phrase':c['introduction'],'phrase':c['emphasis']}]}}
timing=[];beats=visual_language.action_beats(scene,rows,timing)
beats.insert(0,{'id':'intro','action':'reveal','target_id':'line:0','trigger':{'segment_id':'n1','boundary':'start','offset_ms':0}})
timing.insert(0,{'scene_id':'repeat','beat_id':'intro','segment_id':'n1','character_index':0,'quote':c['introduction']})
scene['beats']=beats
audio=engine.ASSETS/'communications_noise.wav'
ir={'schema_version':'0.2','video':{'title':'真实语音同步验收','width':1920,'height':1080,'fps':30,'duration_ms':alignment['duration_ms']},
    'assets':[{'id':'audio','path':'assets/communications_noise.wav','sha256':hashlib.sha256(audio.read_bytes()).hexdigest()}],
    'narration':{'audio_asset_id':'audio','segments':[{'id':r['id'],'text':r['text'],'start_ms':r['start_ms'],'end_ms':r['end_ms']} for r in rows.values()]},
    'scenes':[scene],'presentation':{'preset':'explanation'}}
engine.write_json(engine.IR_PATH,ir)
cp=caption_alignment.prepare(engine,ir)
changes=information_motion.align_visual_beats(ir,cp,{'cues':timing})
engine.write_json(engine.ARTIFACTS/'timing-evidence.json',{'case_id':c['id'],'cues':timing,'changes':changes,'beat_frames':[{**b,'frame':round((rows[b['trigger']['segment_id']]['start_ms']+b['trigger']['offset_ms'])*.03)} for b in beats]})
scene.update(start_frame=0,end_frame=alignment['total_frames'])
scene['beats']=[{**b,'order':i,'frame':round((rows[b['trigger']['segment_id']]['start_ms']+b['trigger']['offset_ms'])*.03)} for i,b in enumerate(beats)]
compiled={**ir,'scenes':[scene],'total_frames':alignment['total_frames']}
engine.render(compiled,renderer_root=out/'renderer')
engine._run_fixed_command('decode',[engine.FFMPEG,'-v','error','-xerror','-i',engine.VIDEO_PATH,'-f','null','-'],ROOT,timeout=120)
print(json.dumps({'video':str(engine.VIDEO_PATH),'seconds':alignment['duration_seconds'],'changes':changes},ensure_ascii=False))
