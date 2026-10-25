"""Bounded, per-model caching of deterministic reference-audio encodings."""
from __future__ import annotations

import time


class ReferenceEncoderCache:
    """Cache only the immutable anchor, separately for left/right padding.

    Continuation audio is deliberately not cached: it changes with the previous
    sentence. No random generation result or language-model state is reused.
    """

    def __init__(self, model, anchor, evaluate):
        self.original = model._encode_wav
        self.anchor = anchor
        self.evaluate = evaluate
        self.values = {}
        self.hits = 0
        self.misses = 0
        self.encode_seconds = 0.0
        model._encode_wav = self.encode

    def encode(self, audio_input, padding_mode="right", trim_silence_vad=False):
        if audio_input is not self.anchor:
            return self.original(audio_input, padding_mode=padding_mode,
                                 trim_silence_vad=trim_silence_vad)
        key = (padding_mode, trim_silence_vad)
        if key not in self.values:
            started = time.monotonic()
            value = self.original(audio_input, padding_mode=padding_mode,
                                  trim_silence_vad=trim_silence_vad)
            self.evaluate(value)
            self.values[key] = value
            self.misses += 1
            self.encode_seconds += time.monotonic() - started
        else:
            self.hits += 1
        return self.values[key]

    def report(self) -> dict:
        return {"hits": self.hits, "misses": self.misses,
                "encode_seconds": round(self.encode_seconds, 3)}
