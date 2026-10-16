"""Retiming applies to audio, captions, scenes and cues together."""
from copy import deepcopy
import math


def tempo_filter(ratio: float) -> str:
    factors=[]
    while ratio > 2.0: factors.append(2.0);ratio/=2
    while ratio < .5: factors.append(.5);ratio/=.5
    factors.append(ratio)
    return ','.join(f'atempo={value:.12g}' for value in factors)


def retime_ir(pipeline, ir: dict, speed: float) -> dict:
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
        pipeline._run_fixed_command('retime-audio',[pipeline.FFMPEG,'-nostdin','-v','error','-y','-i',base,'-af',tempo_filter(speed),'-ar',str(pipeline.SAMPLE_RATE),'-ac','1',temp],pipeline.PROJECT)
        samples,sr=sf.read(temp,dtype='float32')
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
