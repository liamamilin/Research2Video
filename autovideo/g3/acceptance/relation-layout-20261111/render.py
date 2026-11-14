"""Production rendering of frozen relation scenes; keep original frame/beat timing."""
import argparse,copy,json,shutil,subprocess,wave
from pathlib import Path
from PIL import Image,ImageDraw,ImageFont
ROOT=Path(__file__).resolve().parents[3];HERE=Path(__file__).parent
p=argparse.ArgumentParser();p.add_argument('--measurement',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--hybrid',action='store_true');p.add_argument('--legacy',action='store_true');p.add_argument('--indices');p.add_argument('--stills',action='store_true');a=p.parse_args()
out=a.output.resolve();out.mkdir(parents=True,exist_ok=False);public=out/'public';public.mkdir();renderer=out/'renderer';shutil.copytree(ROOT/'g1/renderer/src',renderer/'src');(renderer/'node_modules').symlink_to(ROOT/'g0/vendor/remotion-audiogram/node_modules',target_is_directory=True)
inputs=json.loads((a.measurement/'input.json').read_text());indices=[int(i) for i in a.indices.split(',')] if a.indices else json.loads((HERE/'manifest.json').read_text())['representative_indices'];scenes=[];schedule=[];backgrounds={};cursor=0
for index in indices:
 entry=inputs[index];scene=copy.deepcopy(entry['scene']);length=scene['end_frame'];scene.update(purpose='复杂关系结构验收',visual_intent={},narration_segment_ids=[scene['id']],start_frame=cursor,end_frame=cursor+length)
 for i,b in enumerate(scene['beats']):b.update(frame=b['frame']+cursor,order=i)
 scene['beats'].insert(0,{'id':'surface_title','action':'reveal','target_id':'title','frame':cursor,'order':-2})
 scene['beats'].insert(0,{'id':'surface_scene','action':'reveal','target_id':'scene','frame':cursor,'order':-1})
 scenes.append(scene);schedule.append({'id':scene['id'],'start_frame':cursor,'end_frame':cursor+length,'samples':[cursor+min(45,length-1),cursor+length//2,cursor+length-15]})
 if a.hybrid:
  image=Image.new('RGB',(1920,1080),'#F7F8F5');d=ImageDraw.Draw(image);font='/System/Library/Fonts/Hiragino Sans GB.ttc';d.text((140,70),scene['id']+' · 混合卡片',font=ImageFont.truetype(font,25),fill='#17784B');d.text((140,155),scene['props']['title'],font=ImageFont.truetype(font,58),fill='#202D28');name=f'{index}.png';image.save(public/name);backgrounds[scene['id']]={'path':name,'still':False}
 cursor+=length
with wave.open(str(public/'silence.wav'),'wb') as wav:wav.setnchannels(1);wav.setsampwidth(2);wav.setframerate(24000);wav.writeframes(b'\0\0'*(cursor*800))
ir={'schema_version':'0.2','video':{'title':'关系结构验收','width':1920,'height':1080,'fps':30,'duration_ms':round(cursor*1000/30)},'narration':{'audio_asset_id':'audio','segments':[]},'scenes':scenes,'total_frames':cursor,'audio_public_file':'silence.wav'}
if not a.legacy:ir['presentation']={'preset':'explanation'}
props={'ir':ir,'badge':{'text':'关系结构验收 · 合成样例 · 无旁白','color':'#17784B'}}
if a.hybrid:props['fastCardBackgrounds']=backgrounds
(out/'props.json').write_text(json.dumps(props,ensure_ascii=False));(out/'schedule.json').write_text(json.dumps(schedule,indent=2))
cli=ROOT/'g0/vendor/remotion-audiogram/node_modules/@remotion/cli/remotion-cli.js';common=[f'--props={out/"props.json"}',f'--public-dir={public}','--browser-executable=/Applications/Google Chrome.app/Contents/MacOS/Google Chrome','--bundle-cache=false']
if a.stills:
 for row in schedule:
  frame=row['samples'][1];cmd=['node',str(cli),'still','src/index.ts','G1CommunicationNoise',str(out/f'{row["id"]}.png'),f'--frame={frame}',*common]
  with (out/f'{row["id"]}.log').open('w') as log:subprocess.run(cmd,cwd=renderer,stdout=log,stderr=log,check=True)
else:
 cmd=['node',str(cli),'render','src/index.ts','G1CommunicationNoise',str(out/'video.mp4'),'--codec=h264','--crf=20','--concurrency=3','--overwrite=false',*common];(out/'command.json').write_text(json.dumps(cmd))
 with (out/'render.log').open('w') as log:subprocess.run(cmd,cwd=renderer,stdout=log,stderr=log,check=True,timeout=1800)
 with (out/'decode.log').open('w') as log:subprocess.run([str(ROOT/'g0/darwin_arm64/ffmpeg'),'-v','error','-xerror','-i',str(out/'video.mp4'),'-f','null','-'],stdout=log,stderr=log,check=True)
print(json.dumps({'output':str(out),'frames':cursor,'seconds':cursor/30,'stills':a.stills,'hybrid':a.hybrid}))
