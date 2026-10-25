"""Fixed local MLX entry point for VoxCPM2; never loads a user-selected model or executes model code.

One voice for the whole video: every segment is cloned from the same anchor clip (``ref_audio``) and continues the
segment before it (``prompt_audio`` / ``prompt_text``), restarting from the anchor at each chapter. Without the
anchor the voice changes between sentences; continuation alone drifts into a second speaker.

Continuation also copies the pace: a sentence that comes out a little fast makes the next one faster, and so on up the
chapter until the anchor takes over again at the next one (C4 v10: every chapter sped up from ~4.6 to ~6.9 units per
second, every chapter start snapped back). ``chain`` says how the previous sentence is used:
  guarded  continue the previous sentence unless its pace is more than GUARD away from the anchor's, then use the anchor
  anchor   never continue, always the anchor
  chain    always continue the previous sentence (the drifting behaviour)
"""
from pathlib import Path
import json, os, re, sys, time
os.environ['HF_HUB_OFFLINE'] = '1'
os.environ['TOKENIZERS_PARALLELISM'] = 'false'
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from g2.tts_text import for_voice
from g2.voices import VOXCPM_ANCHOR_TEXT, VOXCPM_VOICES, voxcpm_paths

SEEDS = (42, 43, 44)
#: spoken units (CJK char, Latin word, digit) per second of speech; real speech is ~4–7, outside this the clip
#: has dropped words or stalled and is generated again with another seed. A phrase of fewer than MIN_UNITS
#: units is not checked: its pauses dominate the rate.
RATE_RANGE = (2.8, 9.5)
MIN_UNITS = 6
OUTPUT_RATE = 24000
CHAIN_MODES = ('guarded', 'anchor', 'chain')
#: a previous sentence whose pace is further than this (relative) from the anchor's is not continued; shorter
#: sentences than GUARD_MIN_UNITS have no reliable pace, so they are continued
GUARD = 0.12
GUARD_MIN_UNITS = 10
ACTIVITY = 0.003


def units(text: str) -> int:
    return len(re.findall('[㐀-鿿]', text)) + len(re.findall(r"[A-Za-z][A-Za-z0-9'’-]*", text)) + len(re.findall(r'\d', text))


def active_seconds(audio, rate: int) -> float:
    """Length of the speech itself, without the silence before and after it."""
    import numpy as np
    active = np.flatnonzero(np.abs(audio) > ACTIVITY)
    return (active[-1] - active[0] + 1) / rate if active.size else len(audio) / rate


def generate(plan_path: Path, output_dir: Path, voice: str):
    started = time.monotonic()
    if voice not in VOXCPM_VOICES:
        raise ValueError('Unsupported local voice')
    plan_path = plan_path.resolve(); output_dir = output_dir.resolve()
    if not plan_path.is_relative_to(ROOT) or not output_dir.is_relative_to(ROOT):
        raise ValueError('Invalid voice workspace')
    plan = json.loads(plan_path.read_text())
    steps = int(plan.get('voice_settings', {}).get('steps', 10))
    if not 4 <= steps <= 50:
        raise ValueError('Invalid diffusion steps')
    chain = plan.get('voice_settings', {}).get('chain', 'guarded')
    if chain not in CHAIN_MODES:
        raise ValueError('Invalid chain mode')
    options = {key: plan.get('voice_settings', {}).get(key, True)
               for key in ('compile', 'reference_cache')}
    if any(type(value) is not bool for value in options.values()):
        raise ValueError('Invalid voice optimization setting')
    for segment in plan['segments']:
        if not isinstance(segment['id'], str) or not re.fullmatch(r'[a-z][a-z0-9_]{0,47}', segment['id']):
            raise ValueError('Invalid segment id')
    output_dir.mkdir(parents=True, exist_ok=True)
    import mlx.core as mx
    import numpy as np
    import soundfile as sf
    from math import gcd
    from mlx_audio.tts.utils import load_model
    from scipy.signal import resample_poly
    _, model_dir, anchor_path = voxcpm_paths(ROOT)
    load_started = time.monotonic()
    model = load_model(str(model_dir))
    load_seconds = time.monotonic() - load_started
    if options['compile']:
        model.compile_model()
    anchor, anchor_rate = sf.read(str(anchor_path), dtype='float32')
    if anchor_rate != model.sample_rate:
        raise ValueError('Anchor clip must be recorded at the model sample rate')
    anchor_prompt = (anchor, VOXCPM_ANCHOR_TEXT)
    from g2.voice_performance import ReferenceEncoderCache
    reference_cache = ReferenceEncoderCache(model, anchor, mx.eval) if options['reference_cache'] else None
    anchor_rate = units(VOXCPM_ANCHOR_TEXT) / active_seconds(anchor, model.sample_rate)
    previous, previous_rate, chapter, report = anchor_prompt, None, None, []
    for segment in plan['segments']:
        segment_started = time.monotonic()
        sid = segment['id']
        if segment.get('chapter_id') != chapter:
            chapter, previous, previous_rate = segment.get('chapter_id'), anchor_prompt, None
        text = for_voice(segment['text'])
        prompt = previous
        if chain == 'anchor' or (chain == 'guarded' and previous_rate is not None
                                 and abs(previous_rate / anchor_rate - 1) > GUARD):
            prompt = anchor_prompt
        best, tries = None, 0
        for attempt, seed in enumerate(SEEDS, 1):
            tries = attempt
            mx.random.seed(seed)
            pieces = [np.asarray(r.audio, dtype=np.float32).reshape(-1) for r in model.generate(
                text=text, ref_audio=anchor, prompt_audio=prompt[0], prompt_text=prompt[1], inference_timesteps=steps)]
            mx.clear_cache()
            if not pieces:
                continue
            audio = np.concatenate(pieces)
            rate = units(text) / active_seconds(audio, model.sample_rate)
            if best is None or abs(rate - 5.5) < abs(best[1] - 5.5):
                best = (audio, rate, attempt)
            if units(text) < MIN_UNITS or RATE_RANGE[0] <= rate <= RATE_RANGE[1]:
                break
        if best is None:
            raise RuntimeError('Local voice returned no audio')
        audio, rate, _ = best
        previous = (audio, text)
        previous_rate = rate if units(text) >= GUARD_MIN_UNITS else None
        factor = gcd(model.sample_rate, OUTPUT_RATE)
        out = resample_poly(audio, OUTPUT_RATE // factor, model.sample_rate // factor).astype(np.float32)
        sf.write(output_dir / f'{sid}.wav', out, OUTPUT_RATE, subtype='PCM_16')
        report.append({'id': sid, 'chapter': chapter, 'seconds': round(len(out) / OUTPUT_RATE, 3), 'units': units(text),
                       'units_per_second': round(float(rate), 2), 'attempts': tries, 'prompt': 'anchor' if prompt is anchor_prompt else 'previous',
                       'generation_seconds': round(time.monotonic() - segment_started, 3),
                       'in_range': bool(units(text) < MIN_UNITS or RATE_RANGE[0] <= rate <= RATE_RANGE[1])})
        print('VOICE', sid, len(out) / OUTPUT_RATE, flush=True)
    (output_dir / 'voice-report.json').write_text(json.dumps({'voice': voice, 'steps': steps, 'chain': chain, 'anchor_units_per_second': round(float(anchor_rate), 2),
                                                                  'segments': report,
                                                                  'performance': {'compiled': options['compile'], 'load_seconds': round(load_seconds, 3),
                                                                      'total_seconds': round(time.monotonic() - started, 3),
                                                                      'reference_cache': reference_cache.report() if reference_cache else None}}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    generate(Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3])
