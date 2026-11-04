"""An experiment's definition (spec §9, §11) and the checks it must pass before it may be saved or started."""
from __future__ import annotations

import math
import re
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

from core.experiments import registry

#: Lower is better only for early exits; every other metric is higher-is-better.
DIRECTION = {"completion_adjusted": "higher", "completion": "higher", "exit3s": "lower", "cover_pass": "higher",
             "avg_seconds": "higher", "views_24h": "higher"}
#: Conclusions are drawn on ratio metrics only (§8): play counts follow one hit too easily.
PRIMARY = ("completion_adjusted", "completion", "exit3s", "cover_pass")
#: Guardrail thresholds (§8): points for ratios, a relative drop of the median for counts.
RATIO_THRESHOLD = 5.0
COUNT_THRESHOLD = 0.20
COUNT_METRICS = ("views_24h",)
AA_PER_ARM = 20
STATUSES = ("draft", "running", "paused", "ended", "decided")
_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{1,63}$")


class Arm(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    overrides: dict[str, Any] = Field(default_factory=dict)


class Metric(BaseModel):
    primary: str
    better: Literal["higher", "lower"]
    min_effect: float = Field(gt=0)

    @model_validator(mode="after")
    def _known(self):
        if self.primary not in PRIMARY:
            raise ValueError(f"主要指标只能是 {'、'.join(PRIMARY)}")
        if DIRECTION[self.primary] != self.better:
            raise ValueError(f"{self.primary} 的方向应为 {DIRECTION[self.primary]}")
        return self


class Guardrail(BaseModel):
    metric: str
    better: Literal["higher", "lower"]

    @model_validator(mode="after")
    def _known(self):
        if self.metric not in DIRECTION:
            raise ValueError(f"未知的护栏指标：{self.metric}")
        if DIRECTION[self.metric] != self.better:
            raise ValueError(f"{self.metric} 的方向应为 {DIRECTION[self.metric]}")
        return self


class Stop(BaseModel):
    per_arm: int = Field(ge=2, le=1000)
    max_days: int = Field(ge=1, le=120)


class Scope(BaseModel):
    """Which automatic scripts take part. Manual scripts never do. Empty lists mean no restriction."""
    automatic: Literal["all"] = "all"
    matrices: list[str] = Field(default_factory=list)
    columns: list[str] = Field(default_factory=list)
    content_mode: Optional[Literal["individual", "episode"]] = None

    def matches(self, context: dict, column: str) -> bool:
        """Whether a script with this frozen context (and stratum column) is in scope (§7)."""
        if self.columns and column not in self.columns:
            return False
        if self.matrices and (context.get("matrix") or context.get("source_matrix") or "") not in self.matrices:
            return False
        if self.content_mode and (context.get("content_mode") or "individual") != self.content_mode:
            return False
        return True


class Experiment(BaseModel):
    id: str
    name: str = Field(min_length=1, max_length=80)
    hypothesis: str = Field(default="", max_length=500)
    kind: Literal["compare", "aa"] = "compare"
    status: Literal["draft", "running", "paused", "ended", "decided"] = "draft"
    scope: Scope = Field(default_factory=Scope)
    share_b: int = 50
    arms: dict[Literal["A", "B"], Arm]
    metric: Metric
    guardrails: list[Guardrail] = Field(default_factory=list)
    stop: Stop

    @field_validator("id")
    @classmethod
    def _id(cls, value: str) -> str:
        if not _ID.fullmatch(value):
            raise ValueError("实验标识只能用小写字母、数字、下划线和连字符，2–64 个字符")
        return value

    @field_validator("share_b")
    @classmethod
    def _share(cls, value: int) -> int:
        if not 5 <= value <= 50 or value % 5:
            raise ValueError("B 组比例只能是 5%–50%，按 5% 一档")
        return value

    @model_validator(mode="after")
    def _arms(self):
        if set(self.arms) != {"A", "B"}:
            raise ValueError("要有 A、B 两组")
        if self.arms["A"].overrides:
            raise ValueError("A 组就是现状，不能改设置")
        paths = list(self.arms["B"].overrides)
        if self.kind == "aa":
            if paths:
                raise ValueError("A/A 检查两组设置相同，B 组不能改设置")
            return self
        if not paths:
            raise ValueError("对比实验的 B 组至少要改一个开关")
        for path, value in self.arms["B"].overrides.items():
            switch = registry.find(path)
            if switch is None:
                raise ValueError(f"「{path}」不在开关登记表里")
            if switch.prefix and path == switch.path:
                raise ValueError(f"「{path}」要写到具体的子项，比如「{path}.voice_id」，不能整块替换")
            if switch.values and value not in switch.values:
                raise ValueError(f"「{path}」只能取 {list(switch.values)}")
        for i, a in enumerate(paths):
            for b in paths[i + 1:]:
                if registry.overlaps(a, b):
                    raise ValueError(f"「{a}」和「{b}」写入位置重叠")
        return self

    # ---- derived -------------------------------------------------------------------------------------------------

    def write_paths(self) -> list[str]:
        return list(self.arms["B"].overrides)

    def switches(self) -> list[registry.Switch]:
        return [s for s in (registry.find(p) for p in self.write_paths()) if s]

    def block_size(self) -> int:
        """Scripts per block within a column: 50% → 2 (one each), 30% → 10 (three B)."""
        return 100 // math.gcd(self.share_b, 100)

    def counts_failures_before_writing(self) -> bool:
        """A switch read before writing can change who fails before writing, so those failures stay in (§7)."""
        return any(s.timing == "before_writing" for s in self.switches())

    def needs_column(self) -> bool:
        return any(s.reaches == "with_column" for s in self.switches())

    def needs_preview(self) -> bool:
        """Visual and cover switches are looked at before they go live (§9a step ③); writing ones need not be."""
        return any(p.startswith(("presentation", "render", "cover_style")) for p in self.write_paths())


def aa(id: str, name: str = "A/A 检查", *, scope: Scope | None = None, per_arm: int = AA_PER_ARM,
       max_days: int = 28) -> Experiment:
    """An A/A check: both arms identical, used to verify the whole chain (§9a)."""
    return Experiment(id=id, name=name, kind="aa", scope=scope or Scope(),
                      arms={"A": Arm(name="现状"), "B": Arm(name="现状（对照）")},
                      metric=Metric(primary="completion_adjusted", better="higher", min_effect=8),
                      stop=Stop(per_arm=per_arm, max_days=max_days))


def _scopes_may_overlap(a: Scope, b: Scope) -> bool:
    for left, right in ((a.columns, b.columns), (a.matrices, b.matrices)):
        if left and right and not set(left) & set(right):
            return False
    if a.content_mode and b.content_mode and a.content_mode != b.content_mode:
        return False
    return True


def conflicts(a: Experiment, b: Experiment) -> list[str]:
    """Why ``a`` and ``b`` cannot run at the same time: overlapping scope and overlapping write paths (§7)."""
    if a.id == b.id or not _scopes_may_overlap(a.scope, b.scope):
        return []
    return [f"「{p}」和实验 {b.id} 的「{q}」写入位置重叠"
            for p in a.write_paths() for q in b.write_paths() if registry.overlaps(p, q)]
