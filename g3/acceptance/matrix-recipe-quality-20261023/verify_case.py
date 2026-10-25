"""Verify real files, narration, captions and decoded frames for any sample."""
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import subprocess
import sys

OUT=Path(__file__).resolve().parent
ROOT=OUT.parents[2]
RESEARCH=ROOT.parent/"AI_research/Base_CodingCLi"
sys.path.insert(0,str(ROOT))
from g2.audience_copy import problem
from g2.caption_alignment import verify_plan,srt_text
from g2.card_editorial import validate
from PIL import Image,ImageDraw

key=sys.argv[1]
case=next(c for c in json.loads((OUT/"batch.json").read_text())["cases"] if c["key"]==key)
run=json.loads((OUT/"runs"/(key+"-final.json")).read_text())
assert run["status"]=="COMPLETED",run.get("error_message")
folder=RESEARCH/"state/video_renders"/run["id"]
dest=OUT/"verified"/key
dest.mkdir(parents=True,exist_ok=True)


def read(p):return json.loads(p.read_text())


def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()


def connection(path):
    db=sqlite3.connect(f"file:{path}?mode=ro",uri=True)
    db.row_factory=sqlite3.Row
    return db


def flat(text):return re.sub(r"\s+","",text)


def millis(stamp):
    hours,minutes,seconds=re.split(r"[:,]",stamp)[:3]
    ms=int(stamp.rsplit(',',1)[1])
    return ((int(hours)*60+int(minutes))*60+int(seconds))*1000+ms


adb=connection(ROOT/"g3/data/jobs.sqlite3")
rdb=connection(RESEARCH/"state/video.db")
attempt=dict(adb.execute("SELECT * FROM attempts WHERE job_id=? ORDER BY attempt_no DESC LIMIT 1",(run["autovideo_job_id"],)).fetchone())
registered={r["name"]:dict(r) for r in adb.execute("SELECT * FROM artifacts WHERE job_id=? AND attempt_no=?",(run["autovideo_job_id"],attempt["attempt_no"]))}
for artifact in run["artifacts"]:
    file=folder/artifact["name"]
    assert file.stat().st_size==artifact["bytes"]==registered[artifact["name"]]["bytes"]
    assert sha(file)==artifact["sha256"]==registered[artifact["name"]]["sha256"]
version=dict(rdb.execute("SELECT * FROM script_versions WHERE script_id=? AND version=?",(run["script_id"],run["version"])).fetchone())
sections=json.loads(version["sections"])
expected=''.join(p['text'] for s in sections for p in s['paragraphs'])
assert not any(p.get('evidence')=='NEEDS_REVIEW' for s in sections for p in s['paragraphs'])
ir=read(folder/"video-ir.json")
spoken=''.join(s['text'] for s in ir['narration']['segments'])
assert flat(spoken)==flat(expected)
assert not problem(spoken)
assert read(folder/"qa.json")["status"]==read(folder/"narration-check.json")["status"]=="PASS"
if (folder/"package-qa.json").exists():assert read(folder/"package-qa.json")["status"]=="PASS"
subtitle=folder/("content-captions.srt" if (folder/"content-captions.srt").exists() else "captions.srt")
cues=[]
for block in subtitle.read_text().strip().split('\n\n'):
    lines=block.splitlines()
    start,end=lines[1].split(' --> ')
    cues.append({'start_ms':millis(start),'end_ms':millis(end),'text':'\n'.join(lines[2:])})
assert flat(''.join(c['text'] for c in cues))==flat(spoken)
assert all(0<=c['start_ms']<c['end_ms']<=ir['video']['duration_ms']+50 for c in cues)
assert all(a['end_ms']<=b['start_ms'] for a,b in zip(cues,cues[1:]))
caption_mode='phrase'
editorial=None
if (folder/"caption-alignment.json").exists():
    plan=read(folder/"caption-alignment.json")
    verify_plan(ir,plan)
    assert subtitle.read_text()==srt_text(plan['cues'])
    caption_mode='short forced-aligned'
if (folder/"card-editorial.json").exists():
    editorial=read(folder/"card-editorial.json")
    validate({'pages':[{'id':p['id'],'points':p['points']} for p in editorial['pages']]},{'groups':editorial['pages']})

ffmpeg=str(ROOT/"g0/darwin_arm64/ffmpeg")
video=folder/"video.mp4"
decode=subprocess.run([ffmpeg,'-nostdin','-v','error','-i',str(video),'-f','null','-'],capture_output=True,text=True)
assert decode.returncode==0,decode.stderr
probe=subprocess.run([str(ROOT/"g0/darwin_arm64/ffprobe"),'-v','error','-show_streams','-show_format','-of','json',str(video)],capture_output=True,text=True,check=True)
streams=json.loads(probe.stdout)
vs=next(x for x in streams['streams'] if x['codec_type']=='video')
assert vs['width']==1920 and vs['height']==1080 and vs['r_frame_rate']=='30/1'
package=read(folder/"package.json") if (folder/"package.json").exists() else {}
intro=(package.get('intro') or {}).get('seconds',0)
frames=dest/"frames"
frames.mkdir(exist_ok=True)
scenes=ir['scenes']
contact=Image.new('RGB',(1280,360*((len(scenes)+1)//2)),'#dddddd')
positions=[]
for index,scene in enumerate(scenes):
    at=intro+(scene['start_ms']+scene['duration_ms']*.8)/1000
    path=frames/f'scene-{index+1:02d}.jpg'
    subprocess.run([ffmpeg,'-nostdin','-v','error','-y','-ss',str(at),'-i',str(video),'-frames:v','1',str(path)],check=True)
    frame=Image.open(path).convert('RGB');frame.thumbnail((640,360))
    contact.paste(frame,((index%2)*640,(index//2)*360))
    positions.append({'scene':scene['id'],'seconds':at,'frame':str(path)})
contact.save(dest/'contact.jpg',quality=94)
request=run['request']
result={'case':key,'technical_status':'PASS','manual_content_status':'PENDING','render_id':run['id'],
        'job_id':run['autovideo_job_id'],'script_id':run['script_id'],'version':run['version'],'title':version['title'],
        'mode':case['mode'],'video':str(video),'content_seconds':ir['video']['duration_ms']/1000,
        'duration_seconds':float(streams['format']['duration']),'scenes':len(scenes),
        'caption_mode':caption_mode,'caption_cues':len(cues),'max_caption_characters':max(len(flat(c['text'])) for c in cues),
        'max_caption_seconds':round(max(c['end_ms']-c['start_ms'] for c in cues)/1000,3),
        'narration_exact':True,'cue_order_and_coverage':True,'full_decode':True,'artifact_hashes':len(run['artifacts']),
        'source_cards':request.get('sources',[]),'decoded_frames':positions,'contact_sheet':str(dest/'contact.jpg'),
        'presentation':ir.get('presentation'),
        'notes':[]}
(dest/'verification.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
(dest/'script.txt').write_text(version['title']+'\n\n'+'\n\n'.join(s['title']+'\n'+'\n'.join(p['text'] for p in s['paragraphs']) for s in sections))
print(json.dumps({k:result[k] for k in ('case','technical_status','duration_seconds','scenes','caption_mode','caption_cues','max_caption_characters','max_caption_seconds')},ensure_ascii=False))
