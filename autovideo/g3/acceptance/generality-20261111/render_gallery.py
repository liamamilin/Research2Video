"""Render preselected geometry fixtures through the trusted production renderer.
Silent track is intentional; speech acceptance is a separate real-TTS fixture.
"""
import argparse, copy, json, os, shutil, subprocess, sys, wave
from pathlib import Path
from PIL import Image, ImageDraw
ROOT=Path(__file__).resolve().parents[3];sys.path.insert(0,str(ROOT))
from g2 import process_diagram,shot_director
HERE=Path(__file__).parent

def main(split,measurement,output):
    output.mkdir(parents=True,exist_ok=False);public=output/'public';public.mkdir()
    renderer=output/'renderer';shutil.copytree(ROOT/'g1/renderer/src',renderer/'src')
    (renderer/'node_modules').symlink_to(ROOT/'g0/vendor/remotion-audiogram/node_modules',target_is_directory=True)
    cases=json.loads((HERE/f'{split}.json').read_text());inputs={c['id']:c for c in json.loads((measurement/'input.json').read_text())}
    selected=json.loads((HERE/'manifest.json').read_text())['representative_indices'];scenes=[];schedule=[];cursor=0
    for family,indices in selected.items():
        family_cases=[c for c in cases if c['family']==family]
        for index in indices:
            c=family_cases[index];length=c.get('duration_frames',300);entry=inputs.get(c['id']);p=copy.deepcopy(c.get('props',{}));beats=[]
            if family=='flow':
                scene=copy.deepcopy(entry['scene']);p=scene['props'];beats=scene['beats'];component='flow_diagram'
            elif family=='comparison':
                component='comparison';beats=[{'id':f'b{i}','action':'reveal','target_id':f'side:{side}','frame':30+i*180} for i,side in enumerate(['left','right'])]
            elif family=='material':
                component='material_collage' if c['pair'] else 'object_showcase';images=[]
                for k in range(2 if c['pair'] else 1):
                    name=f'{c["id"]}-{k}.png';w,h=c['width'],c['height'];im=Image.new('RGB',(w,h),'#eef3f0');draw=ImageDraw.Draw(im)
                    for x in range(0,w,max(1,w//8)):draw.line((x,0,x,h),fill='#b4c9bf',width=3)
                    for y in range(0,h,max(1,h//8)):draw.line((0,y,w,y),fill='#b4c9bf',width=3)
                    r=c['region'];box=(r['x1']*w/1000,r['y1']*h/1000,r['x2']*w/1000,r['y2']*h/1000);draw.rectangle(box,fill='#eca665');im.save(public/name)
                    images.append({'image_path':name,'caption':'合成区域定位材料','focus':{'width':w,'height':h,'regions':[r]}})
                p={'title':'不同纵横比与边角区域','text':'合成几何材料，仅验证区域完整可见。','images':images,'focus_indices':[0], 'shot_plan':entry['scene']['props']['shot_plan']};beats=[{**b,'action':'reveal','target_id':'image:0'} for b in entry['scene']['beats']]
            elif family=='motion':
                component='flow_diagram';n=c['count'];nodes=[{'id':f'n{i}','label':f'步骤{i+1}'} for i in range(n)]
                p={'title':'密集提及：所有对象持续可见','nodes':nodes,'edges':[{'id':f'e{i}','from':f'n{i}','to':f'n{i+1}','label':''} for i in range(n-1)],'paths':[],'takeaway':'快速提及不再静默丢失第五个以后的对象','visuals':[]}
                req=process_diagram.layout_request({'scenes':[{'id':c['id'],**p}]});path=output/'motion-layout-request.json';path.write_text(json.dumps(req,ensure_ascii=False))
                raw=subprocess.check_output(['node',str(process_diagram.LAYOUT_SCRIPT),str(path)],env={**os.environ,'NODE_PATH':str(ROOT/'g0/vendor/remotion-audiogram/node_modules')})
                p['layout']=json.loads(raw)['scenes'][0]['layout'];p['shot_plan']=entry['scene']['props']['shot_plan'];beats=[{**b,'action':'reveal'} for b in entry['scene']['beats']]
            else:
                component='concept_reveal';p={'title':'重复短语与跨段定位','lines':[c['introduction'],c['emphasis']],'visuals':[]}
                beats=[{'id':'a','action':'reveal','target_id':'line:0','frame':90},{'id':'b','action':'reveal','target_id':'line:1','frame':210}]
            beats.insert(0,{'id':'title','action':'reveal','target_id':'title','frame':0})
            scene={'id':c['id'],'component':component,'purpose':'长文本比较：保留完整内容' if family=='comparison' else c['id'],
                   'visual_intent':{},'props':p,'narration_segment_ids':[c['id']],'start_frame':cursor,'end_frame':cursor+length,
                   'beats':[{**b,'frame':cursor+b['frame'],'order':i} for i,b in enumerate(beats)]}
            scenes.append(scene);schedule.append({'id':c['id'],'family':family,'start_frame':cursor,'end_frame':cursor+length,'sample_frames':[cursor+min(length-1,45),cursor+length//2,cursor+length-30]});cursor+=length
    audio=public/'silence.wav'
    with wave.open(str(audio),'wb') as wav:wav.setnchannels(1);wav.setsampwidth(2);wav.setframerate(24000);wav.writeframes(b'\0\0'*(cursor*800))
    ir={'schema_version':'0.2','video':{'title':'通用性结构验收 · 合成材料 · 无旁白','width':1920,'height':1080,'fps':30,'duration_ms':round(cursor*1000/30)},'narration':{'audio_asset_id':'audio','segments':[]},'scenes':scenes,'total_frames':cursor,'audio_public_file':'silence.wav','presentation':{'preset':'explanation'}}
    props=output/'props.json';props.write_text(json.dumps({'ir':ir,'badge':{'text':f'{split} · 结构样例 · 无旁白','color':'#17784B'}},ensure_ascii=False));(output/'schedule.json').write_text(json.dumps(schedule,ensure_ascii=False,indent=2))
    cli=ROOT/'g0/vendor/remotion-audiogram/node_modules/@remotion/cli/remotion-cli.js'
    command=['node',str(cli),'render','src/index.ts','G1CommunicationNoise',str(output/'gallery.mp4'),f'--props={props}',f'--public-dir={public}','--codec=h264','--crf=20','--concurrency=3','--browser-executable=/Applications/Google Chrome.app/Contents/MacOS/Google Chrome','--overwrite=false']
    (output/'command.json').write_text(json.dumps(command))
    with (output/'render.log').open('w') as log:subprocess.run(command,cwd=renderer,stdout=log,stderr=subprocess.STDOUT,check=True,timeout=1800)
    ff=ROOT/'g0/darwin_arm64/ffmpeg'
    with (output/'decode.log').open('w') as log:subprocess.run([str(ff),'-v','error','-xerror','-i',str(output/'gallery.mp4'),'-f','null','-'],stdout=log,stderr=log,check=True)
    print(json.dumps({'output':str(output),'frames':cursor,'seconds':cursor/30,'decode':'PASS'}))
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--split',required=True);p.add_argument('--measurement',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args();main(a.split,a.measurement.resolve(),a.output.resolve())
