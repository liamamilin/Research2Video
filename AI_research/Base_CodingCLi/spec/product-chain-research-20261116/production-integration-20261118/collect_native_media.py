"""Decode returned media from the native production-entry rehearsal."""
from pathlib import Path
import json
import sqlite3
import subprocess
import time

SOURCE=Path(__file__).resolve().parents[3]
ROOT=SOURCE/'state/validation/chain-production-integration-20261118/native'
TOOLS=SOURCE.parent.parent/'autovideo/g0/darwin_arm64'
EXPECTED=['0047a0e7e1a647cf842d1a11464a1a61','e0772bab9227470494a27eab80b7d2a3']
destination=ROOT/'evidence/media-validation.json'
records=json.loads(destination.read_text()) if destination.exists() else {}
with sqlite3.connect((ROOT/'state/video.db').resolve().as_uri()+'?mode=ro',uri=True) as db:
    db.row_factory=sqlite3.Row
    for script in EXPECTED:
        row=db.execute("SELECT * FROM renders WHERE script_id=? AND status='COMPLETED' ORDER BY created_at DESC LIMIT 1",(script,)).fetchone()
        if not row or records.get(script,{}).get('render_id')==row['id']:
            continue
        video=ROOT/'state/video_renders'/row['id']/'video.mp4'
        with (ROOT/'evidence'/(script+'-ffprobe.json')).open('w') as stream:
            subprocess.run([str(TOOLS/'ffprobe'),'-v','error','-show_streams','-show_format','-of','json',str(video)],stdout=stream,check=True,timeout=180)
        probe=json.loads((ROOT/'evidence'/(script+'-ffprobe.json')).read_text())
        streams={item['codec_type']:item for item in probe['streams']}
        assert streams['video']['codec_name']=='h264' and streams['audio']['codec_name']=='aac'
        assert [streams['video']['width'],streams['video']['height']]==[1920,1080]
        with (ROOT/'evidence'/(script+'-full-decode.log')).open('w') as stream:
            subprocess.run([str(TOOLS/'ffmpeg'),'-nostdin','-hide_banner','-v','error','-xerror','-i',str(video),
                            '-map','0:v:0','-map','0:a:0','-f','null','-'],stdout=stream,stderr=subprocess.STDOUT,check=True,timeout=900)
        records[script]={'render_id':row['id'],'remote_job':row['autovideo_job_id'],'video':str(video),
                         'bytes':video.stat().st_size,'duration':float(probe['format']['duration']),
                         'dimensions':[1920,1080],'full_decode':'PASS','verified_at':time.time()}
        destination.write_text(json.dumps(records,ensure_ascii=False,indent=2))
        print(script,'full decode PASS',records[script]['duration'],flush=True)
print(len(records),'of',len(EXPECTED),'videos validated',flush=True)
