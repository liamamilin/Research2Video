"""Standalone card-video experiment. Reads saved media; never submits a production job.

The page copy is manually prepared for this sample. Timings measure local
page/audio preparation, encoding and QA, excluding editorial work and reused TTS.
"""
from __future__ import annotations

import argparse
from functools import lru_cache
import hashlib
import json
import math
from pathlib import Path
import re
import shutil
import subprocess
import time
import wave

import numpy as np
from PIL import Image, ImageDraw, ImageFont

PROJECT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
SOURCE = PROJECT / "g3/data/jobs/875a6e0c-3121-478a-89d1-27fa836324fe/attempts/1/g2-run"
FFMPEG = PROJECT / "g0/darwin_arm64/ffmpeg"
FFPROBE = PROJECT / "g0/darwin_arm64/ffprobe"
W, H, FPS, RATE = 1920, 1080, 30, 24000
INTRO, CONTENT, SOURCES, OUTRO = 45, 6117, 135, 90
TOTAL = INTRO + CONTENT + SOURCES + OUTRO
BG, INK, GREEN, MUTED = "#f3f5f1", "#172c24", "#1f7a4d", "#627269"
SOFT, LINE, AMBER = "#e3ece3", "#d7e1d6", "#9b641e"
FONT_PATH = "/System/Library/Fonts/Hiragino Sans GB.ttc"


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@lru_cache(maxsize=80)
def font(size: int, latin: bool = False):
    path = "/System/Library/Fonts/Supplemental/Arial Bold.ttf" if latin else FONT_PATH
    return ImageFont.truetype(path, size)


def wrap(text: str, size: int, width: int) -> list[str]:
    lines = []
    for paragraph in text.split("\n"):
        current = ""
        for token in re.findall(r"[A-Za-z0-9][A-Za-z0-9 .,+/-]*|[^A-Za-z0-9]", paragraph):
            if current and token in "，。；：！？、,;!?）】》…”’":
                current += token  # Hang closing punctuation rather than orphan it.
                continue
            if font(size).getlength(current + token) > width and current:
                lines.append(current.rstrip())
                current = token.lstrip()
            else:
                current += token
            while font(size).getlength(current) > width:
                cut = len(current) - 1
                while cut > 0 and font(size).getlength(current[:cut]) > width:
                    cut -= 1
                lines.append(current[:cut])
                current = current[cut:].lstrip()
        if current:
            lines.append(current.rstrip())
    return lines


def text(draw, value, xy, size, color=INK, width=1600, bold=False, gap=18, max_lines=None):
    lines = wrap(value, size, width)
    if max_lines is not None and len(lines) > max_lines:
        raise ValueError(f"text does not fit: {value!r} ({len(lines)} lines)")
    x, y = xy
    for line in lines:
        draw.text((x, y), line, font=font(size), fill=color, stroke_width=1 if bold else 0,
                  stroke_fill=color, anchor="lt")
        y += size + gap
    return y


def pill(draw, label, x, y, fill=SOFT, color=GREEN, size=27):
    width = int(font(size).getlength(label)) + 38
    draw.rounded_rectangle((x, y, x + width, y + 48), radius=24, fill=fill)
    draw.text((x + 19, y + 9), label, font=font(size), fill=color, anchor="lt")
    return x + width


def icon(draw, name: str, box=(1300, 252, 1795, 714)) -> None:
    x0, y0, x1, y1 = box
    draw.rounded_rectangle(box, radius=44, fill=SOFT)
    cx, cy = (x0+x1)//2, (y0+y1)//2
    stroke = GREEN
    if name == "phone":
        draw.rounded_rectangle((cx-85, cy-156, cx+85, cy+156), radius=28, outline=stroke, width=8)
        draw.line((cx-24, cy-136, cx+24, cy-136), fill=stroke, width=7)
        draw.rounded_rectangle((cx-43, cy-43, cx+43, cy+36), radius=9, outline=stroke, width=6)
        draw.rectangle((cx-12, cy-53, cx+12, cy-44), fill=stroke)
        draw.polygon([(cx+2,cy-29),(cx-20,cy+4),(cx-1,cy+4),(cx-5,cy+24),(cx+22,cy-9),(cx+3,cy-9)],fill=stroke)
    elif name == "headphones":
        # The script describes silicone eartips: use generic in-ear earbuds,
        # not an over-ear headset. These are explicitly labelled as diagrams.
        for sign in (-1,1):
            x=cx+sign*83
            draw.rounded_rectangle((x-15,cy-14,x+20,cy+108),radius=17,fill=stroke)
            draw.ellipse((x-45,cy-84,x+46,cy+4),fill=stroke)
            tip=x-sign*34
            draw.ellipse((tip-22,cy-60,tip+22,cy-12),fill="#658f73")
            draw.ellipse((tip-10,cy-46,tip+10,cy-27),fill=BG)
    elif name == "audio":
        draw.arc((cx-120,cy-120,cx+120,cy+125),180,360,fill=stroke,width=14)
        for sign in (-1,1):
            x=cx+sign*110
            draw.rounded_rectangle((x-24,cy-5,x+24,cy+100),radius=18,fill=stroke)
        if name == "audio":
            for n, height in enumerate([30,65,100,65,30]):
                x=cx-50+n*25
                draw.rounded_rectangle((x,cy-height//2+35,x+9,cy+height//2+35),radius=4,fill=stroke)
    elif name == "laptop":
        draw.rounded_rectangle((cx-147,cy-103,cx+147,cy+87),radius=16,outline=stroke,width=8)
        draw.polygon([(cx-146,cy+91),(cx-180,cy+126),(cx+180,cy+126),(cx+146,cy+91)],outline=stroke,fill=BG)
        draw.line((cx-180,cy+126,cx+180,cy+126),fill=stroke,width=7)
        draw.line((cx-75,cy-15,cx-25,cy-15),fill=stroke,width=7)
        draw.line((cx-75,cy+10,cx+50,cy+10),fill=stroke,width=7)
    else:
        draw.rounded_rectangle((cx-160,cy-116,cx+160,cy+116),radius=23,outline=stroke,width=8)
        draw.line((cx,cy-115,cx,cy+115),fill=stroke,width=5)
        draw.rounded_rectangle((cx-140,cy-95,cx-18,cy+95),radius=12,fill="#cadfce")
        draw.rounded_rectangle((cx+18,cy-95,cx+140,cy+95),radius=12,fill="#d8e7d8")
    label="形态示意"
    draw.text((cx-font(23).getlength(label)/2,y1-48),label,font=font(23),fill=MUTED,anchor="lt")


def base(page: dict, number: int, count: int) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    image = Image.new("RGB", (W,H), BG)
    draw = ImageDraw.Draw(image)
    draw.rectangle((0,0,15,H),fill=GREEN)
    draw.text((106,61),"专题速览",font=font(35),fill=GREEN,anchor="lt")
    draw.line((107,119,1813,119),fill=LINE,width=2)
    draw.text((294,69),"新品与行业变化",font=font(25),fill=MUTED,anchor="lt")
    draw.text((1660,69),f"{number:02d} / {count:02d}",font=font(25,True),fill=MUTED,anchor="lt")
    if page.get("review"):
        pill(draw,"来源待核实",1455,155,fill="#f3e5cc",color=AMBER,size=26)
    text(draw,page.get("label",""),(108,163),30,GREEN,width=1250,max_lines=1)
    if page.get("chapter") is not None:
        labels=["本期地图","续航手机","降噪耳机","品牌并购","翻转笔记本","宽幅折叠","下一步"]
        x=108
        for i,label in enumerate(labels):
            active=page["chapter"]==i
            if active:
                x=pill(draw,label,x,861,fill=GREEN,color="white",size=24)+18
            else:
                draw.text((x+14,874),label,font=font(24),fill=MUTED,anchor="lt")
                x+=int(font(24).getlength(label))+56
        draw.rounded_rectangle((104,930,1816,1038),radius=18,fill="#e8ede6")
    return image,draw


def draw_page(page: dict, path: Path, number: int, count: int) -> None:
    image,draw=base(page,number,count)
    kind=page["kind"]
    if kind in {"intro","outro"}:
        text(draw,page["title"],(108,271),102,bold=True,width=1640,max_lines=2,gap=34)
        text(draw,page["lead"],(114,600),42,MUTED,width=1520,max_lines=2)
        draw.line((114,754,502,754),fill=GREEN,width=8)
        text(draw,"续航 · 音频 · 品牌 · 电脑 · 折叠",(114,806),31,GREEN,max_lines=1)
    elif kind=="overview":
        text(draw,page["title"],(108,232),78,bold=True,max_lines=1)
        text(draw,page["lead"],(111,348),35,MUTED,max_lines=1)
        for i,label in enumerate(page["items"]):
            x=108+(i%3)*574
            y=447+(i//3)*163
            draw.rounded_rectangle((x,y,x+547,y+132),radius=25,fill="white",outline=LINE,width=2)
            draw.text((x+28,y+27),f"0{i+1}",font=font(36,True),fill=GREEN,anchor="lt")
            text(draw,label,(x+100,y+37),34,width=410,max_lines=1)
    elif kind=="source":
        text(draw,"来源与阅读提示",(108,235),76,bold=True,max_lines=1)
        for i,source in enumerate(page["items"]):
            y=370+i*116
            pill(draw,f"0{i+1}",108,y+2)
            text(draw,source["title"],(203,y),32,width=1460,max_lines=1)
            text(draw,f"{source['domain']}  ·  {source['date']}",(203,y+46),27,MUTED,max_lines=1)
        draw.rounded_rectangle((108,792,1812,906),radius=20,fill="#f3e5cc")
        text(draw,"耳机章节的来源仍待核实，相关提示已保留。",(137,828),29,AMBER,width=1600,max_lines=1)
    elif kind=="deal":
        title_end=text(draw,page["title"],(108,236),72,bold=True,max_lines=2)
        if page.get("lead"):
            text(draw,page["lead"],(111,title_end+23),34,MUTED,max_lines=2)
        labels=[(page.get("left","Bose"),page.get("left_sub","音频公司")),(page.get("right","Skullcandy"),page.get("right_sub","生活方式音频品牌"))]
        for i,(label,sub) in enumerate(labels):
            x=108+i*906
            draw.rounded_rectangle((x,481,x+799,697),radius=30,fill="white",outline=LINE,width=2)
            text(draw,label,(x+37,524),57,GREEN,width=720,max_lines=1,bold=True)
            text(draw,sub,(x+39,611),31,MUTED,width=720,max_lines=1)
        draw.line((933,584,980,584),fill=GREEN,width=7)
        draw.polygon([(980,584),(967,574),(967,594)],fill=GREEN)
    elif kind=="metric":
        text(draw,page["title"],(108,231),74,bold=True,width=1100,max_lines=2)
        draw.text((109,366),page["metric"],font=font(212,True),fill=GREEN,anchor="lt")
        text(draw,page["unit"],(276,476),53,width=880,max_lines=1)
        text(draw,page["lead"],(116,646),37,MUTED,width=1050,max_lines=2)
        icon(draw,page.get("icon","fold"))
    else:
        width=1640 if kind in {"caution","closing"} else 1100
        size=68 if kind=="caution" else 74
        end=text(draw,page["title"],(108,238),size,bold=True,width=width,max_lines=3,gap=23)
        if page.get("lead"):
            end=text(draw,page["lead"],(113,end+26),37,MUTED,width=width,max_lines=3,gap=16)
        items=page.get("items",[])
        if items:
            y=max(468,end+36)
            for item in items:
                color=AMBER if kind=="caution" else GREEN
                draw.ellipse((115,y+13,127,y+25),fill=color)
                y=text(draw,item,(150,y),36,color,width=width-65,max_lines=2,gap=12)+21
            assert y < 762, (page["title"],y)
        if page.get("icon"):
            icon(draw,page["icon"])
    note=page.get("note") or page.get("source")
    if note:
        color=AMBER if kind=="caution" or page.get("review") else MUTED
        draw.rounded_rectangle((108,762,1812,835),radius=15,fill="#eee5d5" if kind=="caution" else "#e8ede6")
        text(draw,note,(130,782),25,color,width=1660,max_lines=2,gap=6)
    image.save(path,optimize=False,compress_level=2)


def ass_time(seconds: float) -> str:
    cs=round(seconds*100)
    return f"{cs//360000}:{cs//6000%60:02}:{cs//100%60:02}.{cs%100:02}"


def normalize(value: str) -> str:
    return re.sub(r"\s+","",value)


def subtitle_chunks(value: str) -> list[str]:
    pieces=re.findall(r".*?[，。；：！？、,;!?]|.+$",value)
    chunks=[]
    current=""
    for piece in pieces:
        if current and font(42).getlength(current+piece)>2400:
            chunks.append(current)
            current=""
        if font(42).getlength(piece)>2400:
            for line in wrap(piece,42,2200):
                if current:chunks.append(current);current=""
                chunks.append(line)
        else:
            current+=piece
    if current:chunks.append(current)
    assert normalize("".join(chunks))==normalize(value)
    return chunks


def prepare(out: Path) -> None:
    out.mkdir(parents=True,exist_ok=False)
    (out/"cards").mkdir()
    (out/"logs").mkdir()
    started=time.monotonic()
    alignment=read_json(SOURCE/"artifacts/alignment.json")
    request=read_json(SOURCE.parents[2]/"input/input.json")
    package=read_json(SOURCE/"artifacts/package/package.json")
    source_text="\n\n".join("\n".join(p.strip() for p in c["paragraphs"]) for c in request["chapters"]).strip()
    segments=alignment["segments"]
    assert normalize(source_text)==normalize("".join(s["text"] for s in segments))
    times={s["id"]:round(s["start_ms"]*FPS/1000) for s in segments}
    pages=read_json(HERE/"pages.json")
    for page in pages:
        page["start_frame"]=INTRO+(times[page["at"]] if isinstance(page["at"],str) else int(page["at"]*FPS))
    pages=[{"kind":"intro","label":"专题速览","title":"续航手机最抢眼\n五件新品变化速览","lead":"五件变化，关注事实，也保留判断边界。","start_frame":0},*pages,
        {"kind":"source","label":"来源","items":package["packaging"]["source_card"]["sources"],"start_frame":INTRO+CONTENT},
        {"kind":"outro","label":"专题速览","title":"关注变化\n也关注证据","lead":"面向大众观众的专题速览","start_frame":TOTAL-OUTRO}]
    concat=["ffconcat version 1.0"]
    for i,page in enumerate(pages):
        page["end_frame"]=pages[i+1]["start_frame"] if i+1<len(pages) else TOTAL
        assert page["end_frame"]>page["start_frame"]
        page["file"]=f"cards/{i:02d}.png"
        draw_page(page,out/page["file"],i+1,len(pages))
        concat.extend([f"file '{page['file']}'","option framerate 30",f"duration {(page['end_frame']-page['start_frame'])/FPS:.9f}"])
    concat.extend([f"file '{pages[-1]['file']}'","option framerate 30"])
    (out/"cards.ffconcat").write_text("\n".join(concat)+"\n")
    save_json(out/"timeline.json",pages)
    header="""[Script Info]
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080
WrapStyle: 2
ScaledBorderAndShadow: yes
[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Caption,Hiragino Sans GB,42,&H00242C17,&H00242C17,&H00E6EDE8,&H00E6EDE8,0,0,0,0,100,100,0,0,1,0,0,2,165,165,58,1
Style: Progress,Arial,10,&H004D7A1F,&H004D7A1F,&H004D7A1F,&H004D7A1F,0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1
[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    events=[]
    captions=[]
    for segment in segments:
        chunks=subtitle_chunks(segment["text"])
        weights=[max(1,font(42).getlength(c)) for c in chunks]
        cumulative=0
        for chunk,weight in zip(chunks,weights):
            start=INTRO/FPS+(segment["start_ms"]+(segment["end_ms"]-segment["start_ms"])*cumulative/sum(weights))/1000
            cumulative+=weight
            end=INTRO/FPS+(segment["start_ms"]+(segment["end_ms"]-segment["start_ms"])*cumulative/sum(weights))/1000
            measured=font(42).getlength(chunk)
            balanced=min(1460,max(900,math.ceil(measured/2)+65)) if measured>1460 else 1460
            lines=wrap(chunk,42,balanced)
            if len(lines)>2:
                lines=wrap(chunk,42,1460)
            assert len(lines)<=2,chunk
            escaped="\\N".join(line.replace("{","（").replace("}","）") for line in lines)
            events.append(f"Dialogue: 2,{ass_time(start)},{ass_time(end)},Caption,,0,0,0,,{{\\fad(50,60)}}{escaped}")
            captions.append({"start":start,"end":end,"text":chunk,"segment_id":segment["id"]})
    progress=f"{{\\an7\\pos(0,1075)\\clip(0,0,0,1080)\\t(0,{round(TOTAL/FPS*1000)},\\clip(0,0,1920,1080))\\p1}}m 0 0 l 1920 0 l 1920 5 l 0 5{{\\p0}}"
    events.append(f"Dialogue: 0,0:00:00.00,{ass_time(TOTAL/FPS)},Progress,,0,0,0,,{progress}")
    (out/"captions.ass").write_text(header+"\n".join(events)+"\n",encoding="utf-8")
    save_json(out/"captions.json",captions)
    assert normalize("".join(c["text"] for c in captions))==normalize(source_text)
    def srt_time(t):
        ms=round(t*1000)
        return f"{ms//3600000:02}:{ms//60000%60:02}:{ms//1000%60:02},{ms%1000:03}"
    (out/"captions.srt").write_text("\n\n".join(f"{i+1}\n{srt_time(c['start'])} --> {srt_time(c['end'])}\n{c['text']}" for i,c in enumerate(captions))+"\n",encoding="utf-8")
    audio=SOURCE/"fixtures/assets/communications_noise.wav"
    with wave.open(str(audio)) as reader:
        voice=np.frombuffer(reader.readframes(reader.getnframes()),dtype="<i2").astype(np.float32)/32768
        assert reader.getframerate()==RATE and len(voice)==CONTENT*RATE//FPS
    track=np.zeros(TOTAL*RATE//FPS,dtype=np.float32)
    track[INTRO*RATE//FPS:(INTRO+CONTENT)*RATE//FPS]=voice
    import sys
    sys.path.insert(0,str(PROJECT))
    from g2.package_kit.synth_sounds import synth_sting
    from g2.package_stage import active_db
    cue=np.asarray(synth_sting(),dtype=np.float32)[::2]/32768
    gain=10**((active_db(voice,RATE)-6-active_db(cue,RATE))/20)
    for offset in (0,(TOTAL-OUTRO)*RATE//FPS):
        track[offset:offset+len(cue)]+=cue*gain
    with wave.open(str(out/"audio.wav"),"wb") as writer:
        writer.setparams((1,2,RATE,0,"NONE","not compressed"))
        writer.writeframes(np.round(np.clip(track,-.99,.99)*32768).astype("<i2").tobytes())
    save_json(out/"preparation.json",{"seconds":round(time.monotonic()-started,3),"pages":len(pages),
        "body_pages":len(pages)-3,"subtitle_cues":len(captions),"frames":TOTAL,"duration_seconds":TOTAL/FPS,
        "source_text_sha256":hashlib.sha256(source_text.encode()).hexdigest(),"source_audio_sha256":sha(audio),
        "tts_reused":True,"storyboard_reused":False,"manual_page_copy":True,"external_api_calls":0,
        "narration_and_subtitle_text":"PASS","source_review_flag":"preserved on headphone cards"})
    print(json.dumps(read_json(out/"preparation.json"),ensure_ascii=False),flush=True)


def command(out: Path, label: str, args: list, timeout=240) -> str:
    started=time.monotonic()
    log=out/"logs"/f"{label}.log"
    with log.open("wb") as stream:
        result=subprocess.run([str(a) for a in args],cwd=out,stdout=stream,stderr=subprocess.STDOUT,timeout=timeout)
    record={"label":label,"seconds":round(time.monotonic()-started,3),"exit_code":result.returncode,"argv":[str(a) for a in args]}
    with (out/"logs/commands.jsonl").open("a") as f:f.write(json.dumps(record)+"\n")
    if result.returncode:raise RuntimeError(f"{label} failed; see {log}")
    return log.read_text(errors="replace")


def encode(out: Path) -> None:
    assert not (out/"video.mp4").exists(),"preserve the existing output; use another directory"
    started=time.monotonic()
    selected=None
    for encoder,options in [("h264_videotoolbox",["-b:v","5500k","-maxrate","7500k","-bufsize","15000k","-allow_sw","0"]),
                            ("libx264",["-preset","veryfast","-crf","20"])]:
        target=out/f"attempt-{encoder}.mp4"
        try:
            command(out,f"encode-{encoder}",[FFMPEG,"-nostdin","-hide_banner","-v","warning","-n",
                "-f","concat","-safe","0","-i","cards.ffconcat","-i","audio.wav",
                "-vf","fps=30,ass=filename=captions.ass,format=yuv420p","-r","30","-fps_mode","cfr",
                "-frames:v",str(TOTAL),"-t",str(TOTAL/FPS),"-c:v",encoder,*options,"-profile:v","high",
                "-c:a","aac","-b:a","192k","-ar","48000","-ac","2","-movflags","+faststart",target])
            shutil.copyfile(target,out/"video.mp4")
            selected=encoder
            break
        except RuntimeError:
            if encoder=="libx264":raise
    encoding=time.monotonic()-started
    qa_started=time.monotonic()
    info=json.loads(command(out,"probe",[FFPROBE,"-v","error","-show_streams","-show_format","-of","json",out/"video.mp4"]))
    v=next(s for s in info["streams"] if s["codec_type"]=="video")
    a=next(s for s in info["streams"] if s["codec_type"]=="audio")
    assert (v["width"],v["height"],v["r_frame_rate"],int(v["nb_frames"]))==(W,H,"30/1",TOTAL)
    assert abs(float(info["format"]["duration"])-TOTAL/FPS)<.05
    assert a["codec_name"]=="aac" and int(a["sample_rate"])==48000 and a["channels"]==2
    command(out,"full-decode",[FFMPEG,"-nostdin","-v","error","-i",out/"video.mp4","-f","null","-"])
    command(out,"decoded-audio",[FFMPEG,"-nostdin","-v","error","-n","-i",out/"video.mp4","-vn","-ac","1","-ar",RATE,"-f","f32le",out/"decoded-audio.f32"])
    decoded=np.fromfile(out/"decoded-audio.f32",dtype="<f4")
    with wave.open(str(SOURCE/"fixtures/assets/communications_noise.wav")) as f:
        reference=np.frombuffer(f.readframes(f.getnframes()),dtype="<i2").astype(np.float32)/32768
    content=decoded[INTRO*RATE//FPS:(INTRO+CONTENT)*RATE//FPS]
    corr=float(np.corrcoef(reference,content)[0,1])
    assert corr>.995,corr
    all_pages=read_json(out/"timeline.json")
    # Check burned-in captions from decoded pixels. An output -r alone can
    # duplicate already-filtered still frames and freeze/fade the subtitles.
    # The fps filter must run before libass, and every cue must appear on screen.
    cues=read_json(out/"captions.json")
    cue_frames=[round((c["start"]+c["end"])/2*FPS) for c in cues]
    cue_select="+".join(f"eq(n\\,{n})" for n in cue_frames)
    (out/"caption-checks").mkdir()
    command(out,"caption-pixels",[FFMPEG,"-nostdin","-v","error","-n","-i",out/"video.mp4",
        "-vf",f"select='{cue_select}',crop=1712:108:104:930,scale=856:54","-fps_mode","vfr",out/"caption-checks/cue-%02d.png"])
    caption_images=sorted((out/"caption-checks").glob("cue-*.png"))
    assert len(caption_images)==len(cues),(len(caption_images),len(cues))
    caption_pixels=[]
    for path in caption_images:
        rgb=np.asarray(Image.open(path).convert("RGB"))
        dark=int(np.count_nonzero(np.max(rgb,axis=2)<125))
        assert dark>350,(path,dark)
        caption_pixels.append(dark)
    picks=[0,3,7,10,12,16,20,23,len(all_pages)-2]
    frames=[all_pages[i]["start_frame"]+min(50,(all_pages[i]["end_frame"]-all_pages[i]["start_frame"])//2) for i in picks]
    select="+".join(f"eq(n\\,{n})" for n in frames)
    command(out,"contact-sheet",[FFMPEG,"-nostdin","-v","error","-n","-i",out/"video.mp4","-vf",f"select='{select}',scale=640:360,tile=3x3:padding=10:margin=10:color=white","-frames:v","1",out/"contact-sheet.jpg"])
    prep=read_json(out/"preparation.json")
    result={"status":"PASS","encoder":selected,"preparation_seconds":prep["seconds"],"encoding_seconds":round(encoding,3),
        "qa_seconds":round(time.monotonic()-qa_started,3),"ready_assets_to_video_seconds":round(prep["seconds"]+encoding,3),
        "ready_assets_through_qa_seconds":round(prep["seconds"]+time.monotonic()-started,3),
        "duration_seconds":TOTAL/FPS,"frames":TOTAL,"width":W,"height":H,"audio_correlation":corr,
        "full_decode":"PASS","narration_and_subtitle_text":"PASS","tts_reused":True,"manual_page_copy":True,
        "burned_in_caption_cues_checked":len(caption_images),"minimum_caption_dark_pixels":min(caption_pixels),
        "external_api_calls":0,"source_audio_sha256":prep["source_audio_sha256"],"video_sha256":sha(out/"video.mp4"),
        "output":str(out/"video.mp4"),"timing_scope":"after manually preparing page copy; reuse existing TTS; not a fresh end-to-end production benchmark"}
    save_json(out/"qa.json",result)
    print(json.dumps(result,ensure_ascii=False,indent=2),flush=True)


def main() -> None:
    parser=argparse.ArgumentParser()
    parser.add_argument("--output",type=Path,required=True)
    parser.add_argument("--stage",choices=["prepare","encode"],required=True)
    args=parser.parse_args()
    out=args.output.resolve()
    assert out.is_relative_to(PROJECT/"g3/data"),"experiment outputs must stay under g3/data"
    (prepare if args.stage=="prepare" else encode)(out)


if __name__=="__main__":main()
