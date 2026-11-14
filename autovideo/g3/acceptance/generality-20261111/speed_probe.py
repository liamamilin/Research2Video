"""Exercise production audio retiming + real forced alignment on the same spoken object."""
import argparse,json,os,shutil,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3];sys.path.insert(0,str(ROOT))
p=argparse.ArgumentParser();p.add_argument('--source',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--speed',type=float,required=True);a=p.parse_args();out=a.output.resolve();source=a.source.resolve();out.mkdir(parents=True,exist_ok=False)
os.environ['AUTOVIDEO_G2_RUN_DIR']=str(out);os.environ['AUTOVIDEO_G2_DURATION_MODE']='content'
from g2 import run_g2 as engine,caption_alignment,information_motion
from g2.media_settings import retime_ir
for path in [engine.ARTIFACTS,engine.LOGS,engine.ASSETS]:path.mkdir(parents=True,exist_ok=True)
shutil.copy2(source/'fixtures/assets/communications_noise.wav',engine.ASSETS/'communications_noise.wav')
ir=json.loads((source/'artifacts/render-props.json').read_text())['ir']
for scene in ir['scenes']:scene.update(start_ms=0,duration_ms=ir['video']['duration_ms'])
ir=retime_ir(engine,ir,a.speed);engine.write_json(engine.IR_PATH,ir)
cp=caption_alignment.prepare(engine,ir)
timing=json.loads((source/'artifacts/timing-evidence.json').read_text())
changes=information_motion.align_visual_beats(ir,cp,{'cues':timing['cues']})
rows={r['id']:r for r in ir['narration']['segments']};total=round(ir['video']['duration_ms']*.03)
for scene in ir['scenes']:
 scene.update(start_frame=0,end_frame=total)
 for b in scene['beats']:b['frame']=round((rows[b['trigger']['segment_id']]['start_ms']+b['trigger']['offset_ms'])*.03)
ir['total_frames']=total
engine.write_json(engine.ARTIFACTS/'timing-evidence.json',{'speed':a.speed,'cues':timing['cues'],'changes':changes,'beat_frames':ir['scenes'][0]['beats']})
engine.render(ir,renderer_root=out/'renderer')
engine._run_fixed_command('decode',[engine.FFMPEG,'-v','error','-xerror','-i',engine.VIDEO_PATH,'-f','null','-'],ROOT,timeout=120)
print(json.dumps({'speed':a.speed,'video':str(engine.VIDEO_PATH),'duration_ms':ir['video']['duration_ms'],'changes':changes}))
