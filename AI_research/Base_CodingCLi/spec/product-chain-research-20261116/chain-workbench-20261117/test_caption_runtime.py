"""Regression: isolated caption environment selection preserves the voice runtime."""
from pathlib import Path
from tempfile import TemporaryDirectory
import sys
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parent/'runtime/renderer'))
from g2 import caption_alignment
class Invoked(Exception): pass
with TemporaryDirectory() as name:
    root=Path(name)
    class Pipeline:
        PROJECT=root
        ARTIFACTS=root/'artifacts'
        IR_PATH=root/'ir.json'
        G2Fault=RuntimeError
        def _run_fixed_command(self, label, command, cwd, timeout):
            self.command=command
            assert label=='caption-forced-alignment' and timeout==900
            raise Invoked()
    pipeline=Pipeline()
    voice=root/'g3/data/voice-runtime/bin/python'
    model=root/'g3/data/voice-models/qwen3-forced-aligner-0.6b-8bit/model.safetensors'
    for p in (voice,model):p.parent.mkdir(parents=True,exist_ok=True);p.touch()
    for dedicated in (False,True):
        caption=root/'g3/data/caption-runtime/bin/python'
        if dedicated:caption.parent.mkdir(parents=True);caption.touch()
        try:caption_alignment.prepare(pipeline,{})
        except Invoked:pass
        assert pipeline.command[0]==(caption if dedicated else voice)
print('Passed: dedicated caption interpreter, original voice fallback, unchanged fixed aligner step')
