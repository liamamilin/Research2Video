from pathlib import Path
import hashlib,json
root=Path(__file__).resolve().parent/'runtime'
source=root/'state/video_renders/f021f19f491f47cebe1b941599b4b3f7/video.mp4';download=root/'evidence/flash-browser-download.mp4'
def sha(path):
 h=hashlib.sha256()
 with path.open('rb') as stream:
  for chunk in iter(lambda:stream.read(1024*1024),b''):h.update(chunk)
 return h.hexdigest()
a,b=sha(source),sha(download);assert a==b
result={'source':str(source.resolve()),'browser_download':str(download.resolve()),'bytes':download.stat().st_size,'sha256':a,'identical':True,'browser_playback':{'duration':551.1,'observed_time_start':25.053792,'observed_time_end':37.349926,'ready_state':4,'dimensions':[1920,1080],'media_error':None}}
(root/'evidence/flash-browser-media.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
print('PASS: actual browser download matches returned final MP4',flush=True)
