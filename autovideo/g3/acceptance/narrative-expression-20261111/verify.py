"""Check real-voice expression trials and distinguish production from authored studies."""
import hashlib
import json
from pathlib import Path
import subprocess
import wave

import numpy as np

ROOT = Path(__file__).resolve().parents[3]
BASE = ROOT/'g3/data/validation/narrative-expression-20261111'
FF = ROOT/'g0/darwin_arm64/ffmpeg'
PROBE = ROOT/'g0/darwin_arm64/ffprobe'


def read(path):
    return json.loads(path.read_text())


def check_media(folder, frames):
    path = folder/'video.mp4'
    probe = json.loads(subprocess.check_output([str(PROBE), '-v', 'error', '-show_streams', '-of', 'json', str(path)]))
    video = next(s for s in probe['streams'] if s['codec_type'] == 'video')
    assert (video['width'], video['height'], int(video['nb_frames'])) == (1920, 1080, frames)
    assert any(s['codec_type'] == 'audio' for s in probe['streams'])
    with (folder/'decode.log').open('w') as log:
        subprocess.run([str(FF), '-v', 'error', '-xerror', '-i', str(path), '-f', 'null', '-'],
                       stdout=log, stderr=log, check=True)
    pcm = np.frombuffer(subprocess.check_output([str(FF), '-v', 'error', '-i', str(path), '-vn',
                                              '-ac', '1', '-ar', '8000', '-f', 'f32le', '-']), dtype=np.float32).astype(np.float64)
    return {'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
            'frames': frames, 'seconds': frames/30, 'decode': 'pass'}, pcm


groups = [('context-tutorial', ['before', 'all_native', 'release'], 1160),
          ('result-tutorial', ['before', 'after'], 1809),
          ('holdout-recorder', ['before', 'after'], 848),
          ('science-study', ['before', 'large_diagram', 'stages'], 1244)]
report = {'groups': []}
for name, variants, frames in groups:
    results, audio, props = {}, {}, {}
    for variant in variants:
        folder = (BASE/'final-tutorial/after' if (name, variant) == ('result-tutorial', 'after')
                  else BASE/name/variant)
        results[variant], audio[variant] = check_media(folder, frames)
        props[variant] = read(folder/'props.json')['ir']
    base = variants[0]
    correlations = {}
    for variant in variants[1:]:
        assert audio[base].shape == audio[variant].shape
        correlations[variant] = float(np.corrcoef(audio[base], audio[variant])[0, 1])
        assert correlations[variant] > .999
        assert props[base]['narration'] == props[variant]['narration']
        assert props[base].get('caption_cues') == props[variant].get('caption_cues')
        assert props[base]['total_frames'] == props[variant]['total_frames']
        if name != 'science-study':
            assert [(s['id'], s['start_frame'], s['end_frame'], s['narration_segment_ids']) for s in props[base]['scenes']] == [
                (s['id'], s['start_frame'], s['end_frame'], s['narration_segment_ids']) for s in props[variant]['scenes']]
    clip_start, clip_end = {'context-tutorial': (611, 1770), 'result-tutorial': (2976, 4784),
                            'holdout-recorder': (4093, 4940), 'science-study': (0, 1243)}[name]
    changes = [{'id': a['id'], 'before': a['component'], 'after': b['component']}
               for a, b in zip(props[base]['scenes'], props[variants[-1]]['scenes'])
               if a['component'] != b['component'] and a['start_frame'] <= clip_end and a['end_frame'] > clip_start]
    report['groups'].append({'name': name, 'clips': results, 'audio_correlation': correlations,
                             'narration_and_captions_unchanged': True,
                             'page_boundaries_unchanged': name != 'science-study', 'component_changes': changes if name != 'science-study' else None})

# The final scope drops flow handovers after the recap regression. The context
# interval is identical to the reviewed candidate; later scenes intentionally differ.
def rendered_interval(props, start, end):
    result = dict(props)
    result['ir'] = dict(props['ir'])
    result['ir']['scenes'] = [s for s in props['ir']['scenes']
                            if s['start_frame'] <= end and s['end_frame'] > start]
    result['ir']['beats'] = [b for b in props['ir']['beats'] if start <= b['frame'] <= end]
    return result

assert rendered_interval(read(BASE/'context-tutorial/release/props.json'), 611, 1770) == rendered_interval(
    read(BASE/'final-tutorial/after/props.json'), 611, 1770)
assert read(BASE/'holdout-recorder/after/props.json') == read(BASE/'final-recorder/after/props.json')
report['normal_integration_matches_candidate'] = True
report['superseded_flow_handover'], _ = check_media(BASE/'result-tutorial/after', 1809)
report['superseded_flow_handover']['decision'] = 'rejected: recap replaced procedural context with benefits/output; final scope keeps comparisons only'

# Science's isolated chapter reuses precisely the original PCM slice; the staged
# structure is manually authored, not passed off as a successful model run.
source = read(BASE/'science-study/before/source.json')
original = Path(source['source']['props']).parents[1]/'fixtures/assets/communications_noise.wav'
with wave.open(str(original), 'rb') as stream:
    stream.setpos(round(source['start_ms']*stream.getframerate()/1000))
    pcm = stream.readframes(round(1244*stream.getframerate()/30))
assert hashlib.sha256(pcm).hexdigest() == source['pcm_sha256']
for variant in ('before', 'large_diagram', 'stages'):
    with wave.open(str(BASE/f'science-study/{variant}/fixtures/assets/communications_noise.wav'), 'rb') as stream:
        assert stream.readframes(stream.getnframes()) == pcm
report['science_original_pcm_slice_verified'] = True
packing, packing_audio = check_media(BASE/'science-study/packing', 454)
assert read(BASE/'science-study/packing/props.json') == read(BASE/'science-study/stages/props.json')
reference = np.frombuffer(subprocess.check_output([str(FF), '-v', 'error', '-i', str(BASE/'science-study/stages/video.mp4'),
    '-ss', str(790/30), '-t', str(454/30), '-vn', '-ac', '1', '-ar', '8000', '-f', 'f32le', '-']), dtype=np.float32).astype(np.float64)
length = min(len(reference), len(packing_audio))
packing['audio_correlation_to_same_staged_interval'] = float(np.corrcoef(reference[:length], packing_audio[:length])[0, 1])
# A 790/30-second cut lands at a fractional 8 kHz sample. Independently
# encoding the interval changes waveform phase. Retain the raw measurement,
# compare 10 ms RMS envelopes, and bound any phase correction to one sample.
window = 80
usable = length//window*window
envelopes = [np.sqrt(np.mean(a[:usable].reshape(-1, window)**2, axis=1)) for a in (reference, packing_audio)]
packing['audio_envelope_correlation_10ms'] = float(np.corrcoef(*envelopes)[0, 1])
offsets = np.linspace(-1, 1, 61)
correlations = [float(np.corrcoef(reference[:length], np.interp(
    np.arange(length)+offset, np.arange(len(packing_audio)), packing_audio))[0, 1]) for offset in offsets]
best = int(np.argmax(correlations))
packing['fractional_sample_offset_8khz'] = float(offsets[best])
packing['audio_correlation_with_bounded_subsample_alignment'] = correlations[best]
assert packing['audio_envelope_correlation_10ms'] > .999
assert correlations[best] > .99
packing['manual_study_only'] = True
report['packing'] = packing
report['tests'] = {'passed': 1087, 'skipped': 1}
report['production_scope'] = 'Only existing planned comparisons may replace a previously shown single material. Process handovers rejected.'
report['limits'] = ['science stage and packing studies are manually authored, not an integrated automatic capability',
                    'DeepSeek returned HTTP 402 after explicit authorization; no new external plan was obtained',
                    'no viewer or retention study; no claim of universal tutorial or science improvement']
(BASE/'verification.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n')
print(json.dumps({'groups': len(report['groups']), 'videos': 12, 'packing_audio_correlation': packing['audio_correlation_to_same_staged_interval'],
                  'normal_integration_matches_candidate': True}, ensure_ascii=False))
