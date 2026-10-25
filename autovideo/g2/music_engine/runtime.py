"""Create a private checkpoint view of pinned existing models, without altering the pilot."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import shutil
import sys

from g2.music_engine import GenerationError, manifest, model_root


def file_hash(path: Path) -> str:
    result = hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(4 * 1024 * 1024), b''):
            result.update(chunk)
    return result.hexdigest()


def prepare(work: Path) -> tuple[Path, dict]:
    """Verify installed weights/code and expose a job-local checkpoint tree."""
    root, pinned = model_root(), manifest()
    fingerprint = hashlib.sha256()
    for path in sorted((root / 'source/acestep').rglob('*.py')):
        fingerprint.update((str(path.relative_to(root / 'source')) + '\0' + file_hash(path) + '\n').encode())
    if fingerprint.hexdigest() != pinned['source_sha256']:
        raise GenerationError('MUSIC_RUNTIME_CHANGED', '本机音乐程序与已登记版本不一致，请检查模型配置')
    checkpoint = work / 'runtime/checkpoints'
    checkpoint.mkdir(parents=True)
    for item in pinned['weights']:
        path = root / 'models' / item['file']
        if not path.is_file() or path.stat().st_size != item['bytes'] or file_hash(path) != item['sha256']:
            raise GenerationError('MUSIC_MODEL_INVALID', '本机音乐模型缺失或哈希不匹配')
        target = checkpoint / item['file']; target.parent.mkdir(exist_ok=True)
        target.symlink_to(path)
    for path in (root / 'source/acestep/models/turbo').glob('*.py'):
        if path.name != '__init__.py':
            shutil.copyfile(path, checkpoint / 'acestep-v15-turbo' / path.name)
    cache = work / 'cache'
    for key, value in {
        'HF_HOME': cache / 'huggingface', 'HF_MODULES_CACHE': cache / 'modules',
        'TORCH_HOME': cache / 'torch', 'MPLCONFIGDIR': cache / 'matplotlib',
        'NUMBA_CACHE_DIR': cache / 'numba', 'XDG_CACHE_HOME': cache,
        'GRADIO_TEMP_DIR': work / 'tmp', 'ACESTEP_CHECKPOINTS_DIR': checkpoint,
    }.items():
        Path(value).mkdir(parents=True, exist_ok=True); os.environ[key] = str(value)
    os.environ.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', HF_DATASETS_OFFLINE='1',
                      HF_HUB_DISABLE_TELEMETRY='1', HF_HUB_DISABLE_IMPLICIT_TOKEN='1',
                      GRADIO_ANALYTICS_ENABLED='False', PYTORCH_ENABLE_MPS_FALLBACK='0',
                      TOKENIZERS_PARALLELISM='false')
    os.environ.pop('SPACE_ID', None)
    sys.path.insert(0, str(root / 'source'))
    return checkpoint, pinned
