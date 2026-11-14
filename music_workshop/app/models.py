"""Portable creative records; independent of research and render engines."""
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator


def new_id() -> str:
    return str(uuid4())


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Song(Record):
    id: str = Field(default_factory=new_id, max_length=80)
    label: str = Field(min_length=1, max_length=120)
    provider: str = Field(default="", max_length=120)
    duration_seconds: float | None = Field(default=None, gt=0, le=7200)
    source_url: str = Field(default="", max_length=2048)
    feedback: str = Field(default="", max_length=4000)
    lyrics: str = Field(default="", max_length=16000)
    style: str = Field(default="", max_length=8000)

    @model_validator(mode="after")
    def safe_url(self):
        if self.source_url and not self.source_url.startswith(("https://", "http://")):
            raise ValueError("歌曲链接需要以 https:// 或 http:// 开头")
        return self


class Video(Record):
    id: str = Field(default_factory=new_id, max_length=80)
    song_id: str = Field(max_length=80)
    title: str = Field(min_length=1, max_length=120)
    format: Literal["16:9", "9:16", "1:1"] = "16:9"
    treatment: str = Field(default="", max_length=12000)
    status: Literal["draft"] = "draft"


class Project(Record):
    title: str = Field(min_length=1, max_length=120)
    stage: Literal["idea", "song", "video"] = "idea"
    premise: str = Field(default="", max_length=4000)
    audience: str = Field(default="", max_length=1000)
    emotion: str = Field(default="", max_length=1000)
    perspective: str = Field(default="", max_length=1000)
    hook: str = Field(default="", max_length=2000)
    visuals: str = Field(default="", max_length=6000)
    ending: str = Field(default="", max_length=2000)
    inspiration: str = Field(default="", max_length=6000)
    lyrics: str = Field(default="", max_length=16000)
    style: str = Field(default="", max_length=8000)
    songs: list[Song] = Field(default_factory=list, max_length=200)
    selected_song_id: str | None = None
    videos: list[Video] = Field(default_factory=list, max_length=200)

    @model_validator(mode="after")
    def references(self):
        song_ids = {s.id for s in self.songs}
        if len(song_ids) != len(self.songs):
            raise ValueError("歌曲版本 ID 重复")
        if self.selected_song_id and self.selected_song_id not in song_ids:
            raise ValueError("选中的歌曲版本不存在")
        if any(v.song_id not in song_ids for v in self.videos):
            raise ValueError("MV 必须关联已有歌曲版本")
        if len({v.id for v in self.videos}) != len(self.videos):
            raise ValueError("MV ID 重复")
        return self


class Revision(Record):
    version: int = Field(ge=1)
    project: Project


class ImportBundle(Record):
    schema_version: Literal[1]
    project: Project


def creative_prompt(project: Project) -> str:
    return f"""请帮助发展一个原创音乐视频创意，先挖掘具体冲突和表达角度，不急着写整首歌词。

题目：{project.title}
经历或素材：{project.inspiration or '待补充'}
一句话故事：{project.premise or '待发展'}
观众：{project.audience or '待探索'}
情绪：{project.emotion or '待探索'}
人物视角：{project.perspective or '待探索'}
副歌钩子：{project.hook or '待创作'}
画面设想：{project.visuals or '待发展'}
结尾：{project.ending or '待发展'}

围绕素材给出三个真正不同的人物立场或叙事方向。每个方向提供：一句话故事、
具体共鸣人群、情绪变化、一句可跟唱的副歌钩子、三个能拍出来的画面、结尾。
允许幽默、温暖、爽感、怀念和荒诞等不同情绪，不限定为 AI 或科技题材。
说明各方向的看点与差异；不要编造热度或保证流量。推荐最值得发展的一个方向及理由。
已有歌词与歌曲是用户作品，只提出改写建议，不把建议冒充已认可版本。
"""
