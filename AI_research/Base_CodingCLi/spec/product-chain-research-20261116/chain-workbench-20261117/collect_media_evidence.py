"""Validate completed, returned acceptance videos; leave all attempts intact."""
from pathlib import Path
import json,sqlite3,subprocess,time
ROOT=Path(__file__).resolve().parent/'runtime'
SCRIPTS={'ceaaf17bc5ff41849878b1159971c99e':'Flash 合集','115d4ebe6c1648dc8b2ede59a2e6542a':'Flash 单条','9541c940e2004129ab0a4288edfd822d':'新链路单条','4fd059213d4643cf9ea0d39b00e72687':'新链路合集'}
report=ROOT/'evidence/media-validation.json'
records=json.loads(report.read_text()) if report.exists() else {}
with sqlite3.connect(f'file:{ROOT / "state/video.db"}?mode=ro',uri=True,timeout=20) as db:
    db.row_factory=sqlite3.Row
    for sid,name in SCRIPTS.items():
        row=db.execute("SELECT id,autovideo_job_id,artifacts FROM renders WHERE script_id=? AND status='COMPLETED' ORDER BY created_at DESC LIMIT 1",(sid,)).fetchone()
        if not row or records.get(sid,{}).get('render_id')==row['id']:continue
        video=(ROOT/'state/video_renders'/row['id']/'video.mp4').resolve()
        probe_path=ROOT/'evidence'/f'{sid}-final-ffprobe.json'
        with probe_path.open('w') as output:
            subprocess.run([str(ROOT/'renderer/g0/darwin_arm64/ffprobe'),'-v','error','-show_streams','-show_format','-of','json',str(video)],stdout=output,check=True,timeout=180)
        probe=json.loads(probe_path.read_text())
        streams={s['codec_type']:s for s in probe['streams']}
        assert streams['video']['codec_name']=='h264' and streams['audio']['codec_name']=='aac'
        assert (streams['video']['width'],streams['video']['height'])==(1920,1080)
        log=ROOT/'evidence'/f'{sid}-final-decode.log'
        with log.open('w') as output:
            subprocess.run([str(ROOT/'renderer/g0/darwin_arm64/ffmpeg'),'-nostdin','-hide_banner','-v','error','-xerror','-i',str(video),'-map','0:v:0','-map','0:a:0','-f','null','-'],stdout=output,stderr=subprocess.STDOUT,check=True,timeout=900)
        record={'name':name,'script_id':sid,'render_id':row['id'],'remote_job':row['autovideo_job_id'],'video':str(video),'bytes':video.stat().st_size,'duration_seconds':float(probe['format']['duration']),'video_codec':streams['video']['codec_name'],'audio_codec':streams['audio']['codec_name'],'dimensions':[1920,1080],'full_decode':'PASS','ffprobe':str(probe_path.resolve()),'decode_log':str(log.resolve()),'verified_at':time.strftime('%Y-%m-%d %H:%M:%S')}
        records[sid]=record;report.write_text(json.dumps(records,ensure_ascii=False,indent=2))
        print(name,'PASS',record['duration_seconds'],flush=True)
print(f'{len(records)}/4 returned videos validated',flush=True)
