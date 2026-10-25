"""Post-QA music mix. Keep the verified narration and packaging as separate originals."""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import shutil

from g2.music_library import MusicError, frozen_input

RATE = 48000
BLOCK = 960  # 20 ms, bounded memory even for long videos
CEILING = .90


class MusicMixFault(RuntimeError):
    def __init__(self, code, message):
        self.code, self.message = code, message
        super().__init__(f'{code}: {message}')


class LoopReader:
    """Overlap the original tail and head at every repeat, without claiming a seamless source."""
    def __init__(self, audio, loop=True):
        self.audio, self.loop, self.cursor = audio, loop, 0
        self.fade = min(RATE // 4, len(audio) // 4)
        self.period = len(audio) - self.fade

    def read(self, count):
        import numpy as np
        out = np.zeros((count, 2), dtype='float32')
        at = 0
        while at < count:
            if not self.loop:
                if self.cursor >= len(self.audio):
                    break
                size = min(count-at, len(self.audio)-self.cursor)
                self.audio.seek(self.cursor); out[at:at+size] = self.audio.read(size, dtype='float32', always_2d=True)
            else:
                cycle, position = divmod(self.cursor, self.period)
                size = min(count-at, self.period-position)
                self.audio.seek(position); data = self.audio.read(size, dtype='float32', always_2d=True)
                overlap = min(size, max(0, self.fade-position)) if cycle else 0
                if overlap:
                    self.audio.seek(len(self.audio)-self.fade+position)
                    tail = self.audio.read(overlap, dtype='float32', always_2d=True)
                    blend = ((position + np.arange(overlap)) / max(1, self.fade))[:,None]
                    data[:overlap] = tail*(1-blend) + data[:overlap]*blend
                out[at:at+size] = data
            self.cursor += size; at += size
        return out


def _stats(path: Path, start=0, end=None) -> tuple[float, float]:
    import numpy as np
    import soundfile as sf
    energy, windows, peak = 0.0, 0, 0.0
    with sf.SoundFile(path) as audio:
        audio.seek(start); remaining = (len(audio) if end is None else min(end, len(audio))) - start
        while remaining > 0:
            block = audio.read(min(RATE//20, remaining), dtype='float32', always_2d=True)
            if not len(block): break
            remaining -= len(block); peak = max(peak, float(np.max(np.abs(block))))
            value = float(np.mean(block.astype('float64')**2))
            if value > 10**(-45/10):
                energy += value; windows += 1
    return math.sqrt(energy/max(1, windows)), peak


def write_mix(original: Path, music: Path, work: Path, output: Path, bed_output: Path, config: dict,
              *, samples: int, content_start: int, content_end: int) -> dict:
    import numpy as np
    import soundfile as sf
    voice_rms, _ = _stats(original, content_start, content_end)
    music_rms, _ = _stats(music)
    if voice_rms < .001 or music_rms < .0001:
        raise MusicMixFault('MUSIC_SILENT', '旁白或音乐近乎无声，无法可靠混音')
    gain = voice_rms * 10**(config['level_db']/20) / music_rms
    fade = round(config['fade_seconds']*RATE)
    premix, prebed = work/'pre-mix.wav', work/'pre-bed.wav'
    peak, envelope, cursor, voiced_gain, voiced_count, gap_gain, gap_count = 0.0, 1.0, 0, 0.0, 0, 0.0, 0
    with sf.SoundFile(original) as source, sf.SoundFile(music) as track, \
            sf.SoundFile(premix, 'w', samplerate=RATE, channels=2, subtype='FLOAT') as mixed, \
            sf.SoundFile(prebed, 'w', samplerate=RATE, channels=2, subtype='FLOAT') as bed:
        reader = LoopReader(track, config['loop'])
        music_end = content_end if config['loop'] else min(content_end, content_start+len(track))
        while cursor < samples:
            size = min(BLOCK, samples-cursor)
            voice = source.read(size, dtype='float32', always_2d=True)
            if len(voice)<size: voice=np.pad(voice,((0,size-len(voice)),(0,0)))
            music_block = np.zeros_like(voice)
            start, end = max(cursor, content_start), min(cursor+size, music_end)
            if start < end:
                local = voice[start-cursor:end-cursor]
                speaking = float(np.sqrt(np.mean(local.astype('float64')**2))) > max(.006, voice_rms*.16)
                target = .5 if config['ducking'] and speaking else 1.0
                seconds = (end-start)/RATE
                next_envelope = target + (envelope-target)*math.exp(-seconds/(.03 if target<envelope else .30))
                duck = np.linspace(envelope, next_envelope, end-start, dtype='float32')
                if speaking: voiced_gain += float(np.sum(duck)); voiced_count += len(duck)
                else: gap_gain += float(np.sum(duck)); gap_count += len(duck)
                envelope = next_envelope
                positions = np.arange(start-content_start, end-content_start)
                fades = np.minimum(np.clip(positions/max(1,fade),0,1), np.clip((music_end-content_start-1-positions)/max(1,fade),0,1))
                music_block[start-cursor:end-cursor] = reader.read(end-start)*gain*(duck*fades)[:,None]
            result = voice + music_block
            peak = max(peak,float(np.max(np.abs(result))))
            mixed.write(result);bed.write(music_block);cursor+=size
    master = min(1.0, CEILING/max(peak, 1e-9))
    for src, dest in [(premix,output),(prebed,bed_output)]:
        with sf.SoundFile(src) as inp, sf.SoundFile(dest,'w',samplerate=RATE,channels=2,subtype='FLOAT') as out:
            for block in inp.blocks(blocksize=RATE,dtype='float32',always_2d=True):out.write(block*master)
    return {'music_gain_db':20*math.log10(gain),'master_gain':master,'peak_before_headroom':peak,
            'pcm_peak':peak*master,'voice_active_rms':voice_rms,'music_active_rms':music_rms,
            'ducking':config['ducking'],'ducking_reduction_db':6.0206 if config['ducking'] else 0,
            'speech_average_envelope':voiced_gain/max(1,voiced_count),'gap_average_envelope':gap_gain/max(1,gap_count),
            'speech_samples':voiced_count,'gap_samples':gap_count,'fade_seconds':config['fade_seconds'],
            'loop_crossfade_seconds':min(RATE//4, int(music_rms>0)*RATE//4)/RATE if config['loop'] else 0}


class Correlation:
    def __init__(self):self.n=0;self.x=self.y=self.xx=self.yy=self.xy=0.0
    def add(self,x,y):
        import numpy as np
        x=x.astype('float64').ravel();y=y.astype('float64').ravel()
        self.n+=len(x);self.x+=float(np.sum(x));self.y+=float(np.sum(y));self.xx+=float(np.sum(x*x));self.yy+=float(np.sum(y*y));self.xy+=float(np.sum(x*y))
    def value(self):
        return (self.xy-self.x*self.y/max(1,self.n))/max(1e-12,math.sqrt(max(0,(self.xx-self.x*self.x/max(1,self.n))*(self.yy-self.y*self.y/max(1,self.n)))))


def verify_audio(decoded: Path, original: Path, expected: Path, bed: Path, *, samples: int, start: int, end: int, master: float) -> dict:
    import numpy as np
    import soundfile as sf
    mix_corr, voice_corr = Correlation(), Correlation()
    cursor=0;peak=0.0;voice_energy=music_energy=0.0;voiced=0;outside_peak=0.0
    with sf.SoundFile(decoded) as actual, sf.SoundFile(original) as voice, sf.SoundFile(expected) as mix, sf.SoundFile(bed) as bgm:
        if abs(len(actual)-samples)>RATE/30+1024:
            raise MusicMixFault('MIX_DURATION_MISMATCH','混音音轨时长与画面不一致')
        while cursor<samples:
            size=min(BLOCK,samples-cursor)
            blocks=[f.read(size,dtype='float32',always_2d=True) for f in [actual,voice,mix,bgm]]
            blocks=[np.pad(b,((0,size-len(b)),(0,0))) if len(b)<size else b for b in blocks]
            heard,source,want,music=blocks
            mix_corr.add(want,heard);voice_corr.add(source*master,heard-music)
            peak=max(peak,float(np.max(np.abs(heard))))
            lo,hi=max(cursor,start),min(cursor+size,end)
            if lo<hi:
                a,b=lo-cursor,hi-cursor
                energy=float(np.mean((source[a:b]*master).astype('float64')**2))
                if energy>10**(-40/10):
                    voice_energy+=float(np.sum((source[a:b]*master).astype('float64')**2));music_energy+=float(np.sum(music[a:b].astype('float64')**2));voiced+=(b-a)*2
            mask=(np.arange(cursor,cursor+size)<start)|(np.arange(cursor,cursor+size)>=end)
            if mask.any():outside_peak=max(outside_peak,float(np.max(np.abs(music[mask]))))
            cursor+=size
    margin=10*math.log10(max(voice_energy,1e-12)/max(music_energy,1e-12))
    result={'mixed_audio_correlation':mix_corr.value(),'recovered_narration_correlation':voice_corr.value(),
            'decoded_peak':peak,'speech_music_margin_db':margin,'music_outside_content_peak':outside_peak,
            'samples_checked':samples,'speech_samples_checked':voiced}
    if result['mixed_audio_correlation']<.98 or result['recovered_narration_correlation']<.98:
        raise MusicMixFault('MIX_AUDIO_MISMATCH','混音成片与预期音轨或保留旁白不一致')
    if peak>=1 or margin<10 or outside_peak>1e-7:
        raise MusicMixFault('MIX_LOUDNESS_INVALID','配乐响度、峰值或作用范围检查未通过')
    return result


def run(pipeline, saved: dict, *, force=False) -> dict | None:
    frozen, asset = frozen_input(pipeline.PROJECT_INPUT.parent,saved)
    if not force and (not frozen or not frozen['selection']['enabled']):
        pipeline.skip_step('mix-background-music','本任务未启用背景配乐')
        return None
    config=frozen['selection'] if frozen else {'enabled':False}
    root=pipeline.ARTIFACTS/'mix';root.mkdir(exist_ok=False)
    work=root/'work';work.mkdir()
    package_file=pipeline.ARTIFACTS/'package/package.json'
    packaged=bool(saved.get('package'))
    source=pipeline.ARTIFACTS/'package/video.mp4' if packaged else pipeline.VIDEO_PATH
    from g2.package_stage import probe
    def runner(label,args,timeout=900):return pipeline._run_fixed_command(label,args,pipeline.PROJECT,timeout=timeout)
    info=probe(runner,pipeline.FFPROBE,source,frames=True);frames=int(info['video']['nb_read_frames']);samples=round(frames*RATE/30)
    if packaged:
        package=json.loads(package_file.read_text());start=round(package['intro']['frames']*RATE/30);end=start+round(package['content']['frames']*RATE/30)
    else:start,end=0,samples
    original=work/'original.wav'
    runner('mix-source-audio',[pipeline.FFMPEG,'-nostdin','-v','error','-i',source,'-vn','-ar',str(RATE),'-ac','2','-c:a','pcm_f32le',original])
    video=root/'video.mp4'
    if config['enabled']:
        meta=write_mix(original,asset,work,root/'mixed-audio.wav',root/'music-bed.wav',config,samples=samples,content_start=start,content_end=end)
        runner('mix-mux',[pipeline.FFMPEG,'-nostdin','-v','error','-i',source,'-i',root/'mixed-audio.wav','-map','0:v:0','-map','1:a:0',
            '-c:v','copy','-c:a','aac','-b:a','192k','-ar',str(RATE),'-ac','2','-t',f'{frames/30:.9f}','-movflags','+faststart',video])
        decoded=work/'decoded.wav'
        runner('mix-decode-audio',[pipeline.FFMPEG,'-nostdin','-v','error','-i',video,'-vn','-ar',str(RATE),'-ac','2','-c:a','pcm_f32le',decoded])
        checks=verify_audio(decoded,original,root/'mixed-audio.wav',root/'music-bed.wav',samples=samples,start=start,end=end,master=meta['master_gain'])
    else:
        shutil.copyfile(source,video);meta={'music_disabled':True};checks={'unmixed_video_sha256':pipeline.digest(source),'unchanged_video':pipeline.digest(source)==pipeline.digest(video)}
    runner('mix-full-decode',[pipeline.FFMPEG,'-nostdin','-v','error','-xerror','-i',video,'-map','0:v:0','-map','0:a:0','-f','null','-'])
    final=probe(runner,pipeline.FFPROBE,video,frames=True)
    if int(final['video']['nb_read_frames'])!=frames or abs(float(final['format']['duration'])-frames/30)>.04:
        raise MusicMixFault('MIX_DURATION_MISMATCH','混音改变了成片帧数或时长')
    def video_hash(path, label):
        return runner(label,[pipeline.FFMPEG,'-nostdin','-v','error','-i',path,'-map','0:v:0',
                             '-c:v','copy','-f','hash','-hash','sha256','-']).strip()
    source_hash=video_hash(source,'mix-original-video-hash')
    final_hash=video_hash(video,'mix-final-video-hash')
    if source_hash!=final_hash:
        raise MusicMixFault('MIX_VIDEO_CHANGED','混音改变了原视频流')
    checks.update(video_stream_sha256=final_hash.split('=')[-1],unchanged_video_stream=True)
    report={'status':'PASS','selection':config,'resolved':frozen,'audio':meta,'checks':checks,'full_decode':'PASS',
        'video_frames':frames,'duration_seconds':float(final['format']['duration']),'content_start_sample':start,'content_end_sample':end,
        'video_stream':'copied without re-rendering','unmixed_sha256':pipeline.digest(source),'video_sha256':pipeline.digest(video),
        'narration_and_packaging_qa':'retained; music checked separately','music_generated':bool((frozen or {}).get('music_generated'))}
    pipeline.write_json(root/'music-mix.json',report);pipeline.write_json(root/'mix-qa.json',{'status':'PASS','checks':checks,'full_decode':'PASS'})
    shutil.rmtree(work)
    return report


def reuse_parent(pipeline) -> dict:
    """Music revisions reuse original pixels, voice and packaging, never a previously mixed track."""
    import os
    parent=Path(os.environ['AUTOVIDEO_G2_SOURCE_ATTEMPT_DIR'])/'g2-run'
    pipeline.RUN_ROOT.mkdir(parents=True,exist_ok=False);pipeline.ARTIFACTS.mkdir();pipeline.LOGS.mkdir()
    pipeline._copy_tree_without_symlinks(parent/'fixtures',pipeline.FIXTURE_ROOT)
    for path in (parent/'artifacts').iterdir():
        if path.is_symlink():raise MusicError('原制品路径不安全')
        if path.is_file() and path.suffix in {'.json','.srt','.txt','.mp4'} and path.name not in {'manifest.json','model-cost.json'}:
            shutil.copyfile(path,pipeline.ARTIFACTS/path.name)
    if (parent/'artifacts/package').exists():pipeline._copy_tree_without_symlinks(parent/'artifacts/package',pipeline.ARTIFACTS/'package')
    for label in ['narration-plan','tts-and-align','storyboard-plan','draw-things-backgrounds','compile-ir','render','verify-media','package']:
        pipeline.skip_step(label,'配乐修订复用已核验的原画面、旁白与包装')
    qa=json.loads((pipeline.ARTIFACTS/'qa.json').read_text());qa.update(operation='music_revision',real_llm=False,real_tts=False,reused_verified_baseline=True)
    pipeline.write_json(pipeline.ARTIFACTS/'qa.json',qa);pipeline.write_call_summary()
    return qa
