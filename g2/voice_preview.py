from pathlib import Path
import sys,json,subprocess
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from g2.voices import VOICE_IDS,qwen_paths


def main(voice):
    import soundfile as sf
    if voice not in VOICE_IDS:raise ValueError('Unsupported voice')
    root=ROOT/'g3/data/voice-samples'/voice;root.mkdir(parents=True,exist_ok=True)
    target=root/'sample.wav'
    if target.exists():return
    text='你好，欢迎使用自动视频。先讲清楚目标，再安排画面和声音。'
    if voice.startswith('qwen_'):
        request=root/'request.json';request.write_text(json.dumps({'segments':[{'id':'s1','text':text}]},ensure_ascii=False))
        subprocess.run([str(qwen_paths(ROOT)[0]),str(ROOT/'g2/qwen_voice.py'),str(request),str(root),voice],check=True,timeout=240)
        (root/'s1.wav').replace(target)
    else:
        from kokoro_onnx import Kokoro
        from misaki.zh import ZHG2P
        engine=Kokoro(str(ROOT/'g0/assets/kokoro-v1.0.onnx'),str(ROOT/'g0/assets/voices-v1.0.bin'))
        phonemes,_=ZHG2P()(text);audio,sr=engine.create(phonemes,voice=voice,speed=1,is_phonemes=True,trim=True)
        sf.write(target,audio,sr)


if __name__=='__main__':main(sys.argv[1])
