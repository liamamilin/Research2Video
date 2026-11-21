"""Decode completed galleries and export every preselected scene's visual evidence."""
import argparse,json,subprocess
from pathlib import Path
from PIL import Image,ImageDraw,ImageFont
ROOT=Path(__file__).resolve().parents[3]
p=argparse.ArgumentParser();p.add_argument('folder',type=Path);a=p.parse_args();folder=a.folder.resolve();out=folder/'qa';out.mkdir(exist_ok=False)
ff=ROOT/'g0/darwin_arm64/ffmpeg';probe=ROOT/'g0/darwin_arm64/ffprobe';video=folder/'gallery.mp4'
meta=json.loads(subprocess.check_output([str(probe),'-v','error','-show_streams','-show_format','-of','json',str(video)]))
with (out/'full-decode.log').open('w') as log:subprocess.run([str(ff),'-v','error','-xerror','-i',str(video),'-f','null','-'],stdout=log,stderr=log,check=True)
shots=[]
for row in json.loads((folder/'schedule.json').read_text()):
 for index,frame in enumerate(row['sample_frames']):
  path=out/f'{row["id"]}-{index}.png'
  subprocess.run([str(ff),'-v','error','-ss',str(frame/30),'-i',str(video),'-frames:v','1',str(path)],check=True)
  shots.append({'id':row['id'],'frame':frame,'path':path.name})
font=ImageFont.truetype('/System/Library/Fonts/Hiragino Sans GB.ttc',18)
for start in range(0,len(shots),12):
 sheet=Image.new('RGB',(1440,4*300),'white');draw=ImageDraw.Draw(sheet)
 for i,row in enumerate(shots[start:start+12]):
  im=Image.open(out/row['path']);im.thumbnail((480,270));x=(i%3)*480;y=(i//3)*300;sheet.paste(im,(x,y));draw.text((x+8,y+272),f'{row["id"]} / frame {row["frame"]}',font=font,fill='black')
 sheet.save(out/f'contact-{start//12+1}.jpg',quality=92)
result={'full_decode':'PASS','duration_seconds':float(meta['format']['duration']),'streams':[{k:s[k] for k in ['codec_type','codec_name','width','height','nb_frames','sample_rate'] if k in s} for s in meta['streams']],'snapshots':shots,'note':'Contact sheets support inspection; readability uses full-resolution frames and geometry measurements.'}
(out/'results.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n');print(json.dumps({k:v for k,v in result.items() if k!='snapshots'}))
