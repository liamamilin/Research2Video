"""Install a neutral, locally generated demo brand; never create a personal brand.

Run with the media Python from autovideo/: g0/.venv/bin/python scripts/install_demo_brand.py --activate
"""
from __future__ import annotations

import argparse
import base64
import io
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from g3 import brand_profiles


def install(data_dir: Path, *, activate: bool = False) -> dict:
    import numpy as np
    from PIL import Image, ImageDraw
    import soundfile as sf
    if not (brand_profiles.root(data_dir) / "demo/profile.json").exists():
        brand_profiles.save(data_dir, "demo", {"name": "Demo Studio", "tagline": "把知识，变成画面。",
                            "accent": "#28775b", "background": "#f5f6f2", "text": "#172b23", "intro_seconds": 0, "outro_seconds": 4,
                            "corner_reveal_seconds": .6, "cta": {"enabled": True, "message": "喜欢这期内容，欢迎"}})
    config = brand_profiles.load(data_dir, "demo")
    if "logo" not in config["assets"]:
        image = Image.new("RGBA", (256, 256)); draw = ImageDraw.Draw(image)
        draw.rounded_rectangle((8, 8, 248, 248), 56, fill="#28775b")
        draw.rounded_rectangle((52, 59, 155, 197), 12, outline="#ffffff", width=10)
        draw.polygon([(116, 95), (116, 163), (181, 129)], fill="#ffffff")
        raw = io.BytesIO(); image.save(raw, "PNG")
        brand_profiles.upload(data_dir, "demo", "logo", base64.b64encode(raw.getvalue()).decode())
    if "sonic" not in config["assets"]:
        rate = 48000
        sound = np.zeros(round(3.6*rate))
        # A short public demonstration cue, synthesized without third-party recordings.
        for start, hz in ((0, 261.63), (.42, 329.63), (.84, 392), (1.4, 523.25)):
            t = np.arange(round((3.6-start)*rate))/rate
            tone = (np.sin(2*np.pi*hz*t) + .22*np.sin(2*np.pi*2*hz*t) + .07*np.sin(2*np.pi*3*hz*t))
            tone *= (1-np.exp(-t/.006))*np.exp(-t/0.65)*.19
            offset = round(start*rate); sound[offset:offset+len(tone)] += tone
        sound[-9600:] *= np.linspace(1, 0, 9600)
        raw = io.BytesIO(); sf.write(raw, np.column_stack((sound, sound)), rate, format="WAV", subtype="PCM_16")
        brand_profiles.upload(data_dir, "demo", "sonic", base64.b64encode(raw.getvalue()).decode())
    if activate:
        brand_profiles.activate(data_dir, {"profile": "demo"})
    return brand_profiles.load(data_dir, "demo")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=Path(__file__).resolve().parents[1] / "g3/data")
    parser.add_argument("--activate", action="store_true")
    args = parser.parse_args()
    config = install(args.data_dir, activate=args.activate)
    print(f"Demo brand ready: {config['id']}; active={args.activate}")
