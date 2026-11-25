"""Retiming applies to audio, captions, scenes and cues together."""
from copy import deepcopy
import math


def tempo_filter(ratio: float) -> str:
    factors=[]
    while ratio > 2.0: factors.append(2.0);ratio/=2
    while ratio < .5: factors.append(.5);ratio/=.5
    factors.append(ratio)
    return ','.join(f'atempo={value:.12g}' for value in factors)


def prepare_retimed_audio(pipeline, alignment: dict, speed: float) -> dict:
    """Prepare retimed WAV & segment timestamps ahead of BUILD_IR so forced alignment overlaps STORYBOARD."""
    import json
    import numpy as np
    import soundfile as sf

    base_path = alignment["audio_path"]
    segments = [{k: row[k] for k in ("id", "text", "start_ms", "end_ms")} for row in alignment["rows"]]
    if speed == 1:
        return {
            "audio_path": base_path,
            "audio_sha256": pipeline.digest(base_path),
            "duration_ms": alignment["duration_ms"],
            "segments": segments,
        }
    early_path = base_path.with_name("early-retimed-voice.wav")
    pipeline._run_fixed_command(
        "early-retime-audio",
        [pipeline.FFMPEG, "-nostdin", "-v", "error", "-y", "-i", base_path,
         "-af", tempo_filter(speed), "-ar", str(pipeline.SAMPLE_RATE), "-ac", "1", early_path],
        pipeline.PROJECT,
    )
    samples, sr = sf.read(early_path, dtype="float32")
    assert sr == pipeline.SAMPLE_RATE
    frames = math.ceil(len(samples) / (sr // pipeline.FPS))
    samples = np.pad(samples, (0, frames * (sr // pipeline.FPS) - len(samples)))
    sf.write(early_path, samples, sr, subtype="PCM_16")
    duration = round(frames * 1000 / pipeline.FPS)
    factor = duration / alignment["duration_ms"]
    t = lambda ms: round(ms * factor)
    retimed_segments = [{**s, "start_ms": t(s["start_ms"]), "end_ms": t(s["end_ms"])} for s in segments]
    retimed_sha = pipeline.digest(early_path)
    meta_path = early_path.with_suffix(".json")
    meta_path.write_text(
        json.dumps({"speed": speed, "base_sha256": pipeline.digest(base_path), "retimed_sha256": retimed_sha}),
        encoding="utf-8",
    )
    return {
        "audio_path": early_path,
        "audio_sha256": retimed_sha,
        "duration_ms": duration,
        "segments": retimed_segments,
    }


def retime_ir(pipeline, ir: dict, speed: float) -> dict:
    import json
    import numpy as np
    import soundfile as sf
    current=ir.get('playback',{}).get('speed',1.0)
    if speed==current:return ir
    result=deepcopy(ir)
    audio=next(a for a in result['assets'] if a['id']==result['narration']['audio_asset_id'])
    path=pipeline.FIXTURE_ROOT/audio['path'];temp=path.with_name('retimed-voice.wav')
    base=path.with_name('voice-normal-speed.wav')
    if not base.exists():
        if current!=1:raise ValueError('原正常语速音频缺失，无法无损恢复倍速设置')
        base.write_bytes(path.read_bytes())
    if speed==1:
        samples,sr=sf.read(base,dtype='float32')
    else:
        early=path.with_name('early-retimed-voice.wav')
        early_meta=early.with_suffix('.json')
        reused=False
        if early.is_file() and early_meta.is_file():
            try:
                meta=json.loads(early_meta.read_text(encoding='utf-8'))
                if meta.get('speed')==speed and meta.get('base_sha256')==pipeline.digest(base) and meta.get('retimed_sha256')==pipeline.digest(early):
                    samples,sr=sf.read(early,dtype='float32')
                    reused=True
            except (OSError, ValueError):
                reused=False
        if not reused:
            pipeline._run_fixed_command('retime-audio',[pipeline.FFMPEG,'-nostdin','-v','error','-y','-i',base,'-af',tempo_filter(speed),'-ar',str(pipeline.SAMPLE_RATE),'-ac','1',temp],pipeline.PROJECT)
            samples,sr=sf.read(temp,dtype='float32')
        for stale in (early, early_meta):
            if stale.is_file():
                stale.unlink()
    assert sr==pipeline.SAMPLE_RATE
    frames=math.ceil(len(samples)/(sr//pipeline.FPS))
    samples=np.pad(samples,(0,frames*(sr//pipeline.FPS)-len(samples)))
    sf.write(path,samples,sr,subtype='PCM_16')
    if temp.exists():temp.unlink()
    duration=round(frames*1000/pipeline.FPS);factor=duration/ir['video']['duration_ms']
    t=lambda ms:round(ms*factor)
    old_segments={s['id']:s for s in ir['narration']['segments']}
    for s in result['narration']['segments']:
        s['start_ms']=t(s['start_ms']);s['end_ms']=t(s['end_ms'])
    for s in result['scenes']:
        end=t(s['start_ms']+s['duration_ms']);s['start_ms']=t(s['start_ms']);s['duration_ms']=end-s['start_ms']
        for b in s['beats']:
            trigger=b['trigger'];old=old_segments[trigger['segment_id']]
            anchor=old[trigger['boundary']+'_ms']
            trigger['offset_ms']=t(anchor+trigger['offset_ms'])-t(anchor)
    result['video']['duration_ms']=duration
    audio['sha256']=pipeline.digest(path)
    result['playback']={'speed':speed,'source_duration_ms':ir.get('playback',{}).get('source_duration_ms',ir['video']['duration_ms']),
                        'method':'pitch-preserving audio tempo; synchronized captions and animation'}
    write_captions(pipeline,result['narration']['segments'])
    pipeline.write_json(pipeline.ARTIFACTS/'playback.json',result['playback'])
    alignment_path=pipeline.ARTIFACTS/'alignment.json'
    if alignment_path.exists():
        import json
        alignment=json.loads(alignment_path.read_text())
        pipeline.write_json(pipeline.ARTIFACTS/'alignment-before-speed.json',alignment)
        alignment.update(duration_ms=duration,duration_seconds=frames/pipeline.FPS,duration_frames=frames,
                         method='measured source phrases, retimed together with audio',
                         segments=result['narration']['segments'],playback=result['playback'])
        pipeline.write_json(alignment_path,alignment)
    return result


def write_captions(pipeline,segments):
    def stamp(ms):return f'{ms//3600000:02}:{ms//60000%60:02}:{ms//1000%60:02},{ms%1000:03}'
    (pipeline.ARTIFACTS/'captions.srt').write_text('\n\n'.join(f"{i+1}\n{stamp(s['start_ms'])} --> {stamp(s['end_ms'])}\n{s['text']}" for i,s in enumerate(segments))+'\n',encoding='utf-8')
