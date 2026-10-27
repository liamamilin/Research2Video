"""Platform-neutral call to action: trusted icon artwork and restrained animation."""
from __future__ import annotations

import math
from pathlib import Path

ACTIONS = (("like", "点赞"), ("save", "收藏"), ("follow", "关注"))
SECONDS = 1.5


def sound(sample_rate: int, duration: float):
    """Three quiet original plucks, timed to the icon entrances."""
    import numpy as np
    audio = np.zeros((round(sample_rate*duration), 2), dtype="float32")
    for index, frequency in enumerate((523.251, 659.255, 783.991)):
        start = round((.04+index*.12)*sample_rate)
        t = np.arange(round(.11*sample_rate))/sample_rate
        envelope = (1-np.exp(-t/.002))*np.exp(-t/.024)
        signal = .12*envelope*(np.sin(2*np.pi*frequency*t)+.2*np.sin(4*np.pi*frequency*t))
        length = min(len(t), len(audio)-start)
        audio[start:start+length] += signal[:length, None]
    return audio


def artwork(config: dict, folder: Path, font_path: str) -> list[Path]:
    from PIL import Image, ImageDraw, ImageFont
    background = Image.new("RGB", (1920, 1080), config["background"])
    draw = ImageDraw.Draw(background)
    message = config["cta"]["message"]
    size = 56
    while size > 24 and ImageFont.truetype(font_path, size).getlength(message) > 1560:
        size -= 1
    draw.text((960, 300), message, font=ImageFont.truetype(font_path, size), fill=config["text"], anchor="mm")
    draw.line((860, 858, 1060, 858), fill=config["accent"], width=4)
    base = folder / "cta-base.png"; background.save(base)
    paths = [base]
    for kind, label in ACTIONS:
        # Draw at triple resolution to keep the code-native outlines smooth.
        icon = Image.new("RGBA", (384, 384)); pen = ImageDraw.Draw(icon)
        color = config["accent"]
        def line(points, width=6):
            pen.line([(x*3, y*3) for x, y in points], fill=color, width=width*3, joint="curve")
        if kind == "like":
            pen.rounded_rectangle((17*3, 61*3, 37*3, 110*3), 5*3, outline=color, width=6*3)
            line([(44, 109), (44, 60), (57, 39), (62, 18), (74, 18), (80, 25),
                  (80, 48), (105, 48), (114, 58), (103, 102), (93, 110), (44, 109)])
        elif kind == "save":
            points = [(64 + (50 if i % 2 == 0 else 23)*math.cos(-math.pi/2+i*math.pi/5),
                       64 + (50 if i % 2 == 0 else 23)*math.sin(-math.pi/2+i*math.pi/5)) for i in range(10)]
            line(points + [points[0]])
        else:
            pen.ellipse((35*3, 18*3, 75*3, 58*3), outline=color, width=6*3)
            pen.arc((16*3, 63*3, 96*3, 125*3), 180, 360, fill=color, width=6*3)
            line([(94, 86), (118, 86)]); line([(106, 74), (106, 98)])
        icon = icon.resize((116, 116), Image.Resampling.LANCZOS)
        card = Image.new("RGBA", (360, 330)); pen = ImageDraw.Draw(card)
        pen.ellipse((100, 30, 260, 190), fill=config["accent"]+"14")
        card.alpha_composite(icon, (122, 52))
        pen.text((180, 265), label, font=ImageFont.truetype(font_path, 50), fill=config["text"], anchor="mm")
        path = folder / f"cta-{kind}.png"; card.save(path); paths.append(path)
    return paths


def filters(first_input: int, frames: int, config: dict) -> str:
    """Assemble one brief row with staggered, 12-pixel reveals, then the brand card."""
    parts = [f"[{first_input}:v]fps=30,trim=end_frame={frames},setpts=PTS-STARTPTS,format=rgba[cta_base];"]
    prior = "cta_base"
    for i in range(3):
        delay = .04 + i*.12
        parts.append(f"[{first_input+1+i}:v]fps=30,trim=end_frame={frames},setpts=PTS-STARTPTS,"
                     f"format=rgba,fade=t=in:st={delay}:d=0.16:alpha=1[cta_icon{i}];")
        parts.append(f"[{prior}][cta_icon{i}]overlay=x={420+i*360}:"
                     f"y='420+12*(1-min(max((t-{delay})/0.2,0),1))':shortest=1[cta_row{i}];")
        prior = f"cta_row{i}"
    parts.append(f"[{prior}]format=yuv420p,fade=t=in:st=0:d=0.12:color=0x{config['background'][1:]},"
                 f"fade=t=out:st={frames/30-.12}:d=0.12:color=0x{config['background'][1:]}[cta];")
    parts.append("[cta][brand_card]concat=n=2:v=1:a=0[tail];")
    return "".join(parts)
