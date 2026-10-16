"""Fixed local MLX entry point; never loads a user-selected model or executes model code."""
from pathlib import Path
import json,os,sys
os.environ['HF_HUB_OFFLINE']='1'
os.environ['TOKENIZERS_PARALLELISM']='false'
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from g2.voices import QWEN_VOICES,qwen_paths


def generate(plan_path: Path, output_dir: Path, voice: str):
    import mlx.core as mx
    import numpy as np
    import soundfile as sf
    from mlx_audio.tts.utils import load_model
    if voice not in QWEN_VOICES:raise ValueError('Unsupported local voice')
    plan_path=plan_path.resolve();output_dir=output_dir.resolve()
    if not plan_path.is_relative_to(ROOT) or not output_dir.is_relative_to(ROOT):raise ValueError('Invalid voice workspace')
    output_dir.mkdir(parents=True,exist_ok=True)
    model=load_model(str(qwen_paths(ROOT)[1]));speaker=QWEN_VOICES[voice][0]
    for segment in json.loads(plan_path.read_text())['segments']:
        sid=segment['id']
        if not isinstance(sid,str) or not __import__('re').fullmatch(r'[a-z][a-z0-9_]{0,47}',sid):raise ValueError('Invalid segment id')
        mx.random.seed(42)
        chunks=[];sr=None
        for result in model.generate_custom_voice(text=segment['text'],speaker=speaker,language='Chinese',temperature=.7):
            chunks.append(np.asarray(result.audio,dtype=np.float32).reshape(-1));sr=result.sample_rate
        if not chunks:raise RuntimeError('Local voice returned no audio')
        audio=np.concatenate(chunks)
        if sr!=24000:
            from scipy.signal import resample_poly
            from math import gcd
            factor=gcd(sr,24000);audio=resample_poly(audio,24000//factor,sr//factor)
        sf.write(output_dir/f'{sid}.wav',audio,24000,subtype='PCM_16')
        mx.clear_cache()
        print('VOICE',sid,len(audio)/24000,flush=True)


if __name__=='__main__':generate(Path(sys.argv[1]),Path(sys.argv[2]),sys.argv[3])
