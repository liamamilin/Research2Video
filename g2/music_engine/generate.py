"""One offline ACE-Step generation per process; no implicit download or retry."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import time

PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT))

from g2.music_engine import GenerationError
from g2.music_engine.runtime import file_hash, prepare


def milestone(stage: str) -> None:
    print('MUSIC_STAGE ' + stage, flush=True)


def main(request_file: Path) -> None:
    work = request_file.parent
    request = json.loads(request_file.read_text())
    milestone('VERIFYING')
    checkpoint, pinned = prepare(work)
    def offline(event, args):
        if event in {'socket.connect', 'socket.getaddrinfo', 'socket.bind'}:
            raise RuntimeError('Music inference is offline')
    sys.addaudithook(offline)
    milestone('LOADING')
    import psutil
    import torch
    from acestep.handler import AceStepHandler
    from acestep.inference import GenerationParams, GenerationConfig, generate_music
    if not torch.backends.mps.is_available():
        raise GenerationError('MUSIC_DEVICE_UNAVAILABLE', '本机 MPS 不可用，未启动音乐推理')
    available = psutil.virtual_memory().available
    if available < 12_000_000_000:
        raise GenerationError('MUSIC_MEMORY_LOW', '本机可用内存不足，请关闭较占内存的应用后重试')

    class LocalHandler(AceStepHandler):
        def _ensure_models_present(self, **kwargs):
            if Path(kwargs['checkpoint_path']).resolve() != checkpoint.resolve():
                raise RuntimeError('Unexpected checkpoint directory')
            return None

        @staticmethod
        def _sync_model_code_if_needed(config_path, checkpoint_path):
            from acestep.model_downloader import _check_code_mismatch
            if _check_code_mismatch(config_path, checkpoint_path):
                raise RuntimeError('Checkpoint source mismatch')

        def _initialize_mlx_backends(self, **kwargs):
            self.use_mlx_dit = self.use_mlx_vae = False
            return 'PyTorch MPS', 'PyTorch MPS'

    milestone('LOADING')
    started = time.monotonic(); handler = LocalHandler()
    status, ok = handler.initialize_service(project_root=str(checkpoint.parent), config_path='acestep-v15-turbo',
        device='mps', use_flash_attention=False, compile_model=False, offload_to_cpu=available < 24_000_000_000,
        offload_dit_to_cpu=False, quantization=None, use_mlx_dit=False)
    if not ok or handler.device != 'mps':
        raise GenerationError('MUSIC_LOAD_FAILED', '音乐模型加载失败：' + str(status)[-200:])
    loaded = time.monotonic()
    params = GenerationParams(caption=request['caption'], lyrics='[Instrumental]', instrumental=True,
        duration=request['duration_seconds'], bpm=request['bpm'], keyscale=request['keyscale'], timesignature='4',
        vocal_language='unknown', inference_steps=8, guidance_scale=1.0, shift=3.0, seed=request['seed'],
        thinking=False, use_cot_metas=False, use_cot_caption=False, use_cot_lyrics=False, use_cot_language=False)
    config = GenerationConfig(batch_size=1, use_random_seed=False, seeds=[request['seed']], audio_format='wav32')
    milestone('GENERATING')
    with torch.inference_mode():
        result = generate_music(handler, None, params, config, save_dir=str(work / 'output'))
    torch.mps.synchronize()
    if not result.success or len(result.audios) != 1:
        raise GenerationError('MUSIC_GENERATION_FAILED', '音乐生成未返回完整音频：' + str(result.error)[-200:])
    output = Path(result.audios[0]['path']).resolve()
    if not output.is_relative_to((work / 'output').resolve()) or output.is_symlink():
        raise GenerationError('MUSIC_OUTPUT_INVALID', '生成音乐的输出路径无效')
    report = {'status':'PASS', 'file':str(output.relative_to(work)), 'sha256':file_hash(output),
              'engine':pinned['engine'], 'code_revision':pinned['code_revision'], 'weight_revision':pinned['weight_revision'],
              'seed':request['seed'], 'device':handler.device, 'offload_to_cpu':handler.offload_to_cpu,
              'model_load_seconds':loaded-started, 'generation_seconds':time.monotonic()-loaded,
              'peak_rss_bytes':__import__('resource').getrusage(__import__('resource').RUSAGE_SELF).ru_maxrss,
              'network':'offline', 'generation_calls':1, 'human_review':'pending', 'instrumental_requested':True}
    (work / 'result.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
    milestone('CHECKING')


if __name__ == '__main__':
    try:
        main(Path(sys.argv[1]).resolve())
    except Exception as exc:
        print('MUSIC_ERROR ' + json.dumps({'code':getattr(exc,'code','MUSIC_GENERATION_FAILED'), 'message':str(exc)[-400:]},ensure_ascii=False),flush=True)
        raise
