import sys,json,subprocess
from pathlib import Path
import numpy as np
import soundfile as sf
from PIL import Image
AUTO=Path('/Users/milin/2026/软件开发/视频workflow/autovideo');sys.path.insert(0,str(AUTO))
from g2.package_stage import build_package,default_runner
from g2.packaging_library import resolve
from g2.packaging_library.templates import visual_theme
E=AUTO/'g3/acceptance/outro-match-20261025'
ff=AUTO/'g0/darwin_arm64/ffmpeg';fp=AUTO/'g0/darwin_arm64/ffprobe'
rate=24000;n=rate*2
rng=np.random.default_rng(7);t=np.arange(n)/rate
voice=(rng.normal(0,.09,n)*(.6+.4*np.sin(2*np.pi*3*t))).astype('float32')
results=[]
for mode,ir,style in [('fast',{'presentation':{'preset':'information_cards'}},{'id':'editorial_light'}),('dark',{}, {'id':'legacy'})]:
 out=E/f'{mode}-smoke';out.mkdir(exist_ok=False)
 theme=visual_theme(ir,style);sf.write(out/'voice.wav',voice,rate)
 log=out/'commands.log'
 def runner(label,argv,timeout=900):
  with log.open('a') as f:f.write(json.dumps({'label':label,'argv':[str(x) for x in argv]},ensure_ascii=False)+'\n')
  result=default_runner(label,argv,timeout)
  with log.open('a') as f:f.write(label+' PASS\n')
  return result
 runner('fixture-content',[ff,'-nostdin','-v','error','-y','-f','lavfi','-i',f"color=c={theme['background']}:s=1920x1080:r=30",'-i',out/'voice.wav','-t','2','-c:v','libx264','-pix_fmt','yuv420p','-c:a','aac','-ar','48000','-ac','2','-b:a','192k',out/'content.mp4'])
 record=resolve({'label':'包装验收','intro':{'enabled':False},'outro':{'seconds':1},'source_card':{'seconds':1},'sound':{'enabled':False}},sources=[{'title':'包装测试来源','url':'https://example.com/test','date':'2026-10-25'}])
 result=build_package(runner,ff,fp,config=record['package'],content_video=out/'content.mp4',voice_wav=out/'voice.wav',artifacts=out,packaging_record=record,visual_theme=theme)
 for part,at in [('source',2.7),('outro',3.7)]:
  path=out/f'{part}.png'
  runner('inspect-'+part,[ff,'-nostdin','-v','error','-y','-ss',str(at),'-i',out/'package/video.mp4','-frames:v','1',path])
  with Image.open(path) as image:
   expect=tuple(int(theme['background'][i:i+2],16) for i in (1,3,5))
   assert max(abs(a-b) for a,b in zip(image.getpixel((0,540))[:3],expect))<=3
 results.append({'mode':mode,'status':result['status'],'checks':result['checks'],'theme':theme,'fixture':'synthetic 2-second content/audio; packaging smoke only'})
(E/'theme-smoke.json').write_text(json.dumps(results,ensure_ascii=False,indent=2)+'\n')
print('PASS: fast/dark source cards and outros, full decode and pixel checks')
