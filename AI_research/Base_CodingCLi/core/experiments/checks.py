"""Why a draft or queued experiment may not start yet (spec §6, §7, §9). Shared by the tab and the queue."""
from __future__ import annotations

from core.experiments import registry
from core.experiments.definition import Experiment, conflicts
from core.experiments.store import ExperimentStore

CONFLICT = "写入位置重叠"
NO_PREVIEW = "画面类、封面类开关要先预览 B 并确认"


def start_problems(experiment: Experiment, store: ExperimentStore, config_dir: str, jobs_dir: str) -> list[str]:
    problems = []
    for path in experiment.write_paths():
        switch = registry.find(path)
        if switch and switch.status == "planned":
            problems.append(f"开关「{switch.label}」还没开发（等待开发）")
        elif switch and switch.status == "unconfirmed":
            problems.append(f"开关「{switch.label}」待确认，不能使用")
    if experiment.needs_preview() and not store.preview_confirmed(experiment.id):
        problems.append(NO_PREVIEW)
    for other in store.active():
        problems += conflicts(experiment, Experiment.model_validate(other["definition"]))
    render_keys = {p.split(".", 1)[1].split(".")[0] for p in experiment.write_paths() if p.startswith("render.")}
    if render_keys:
        from core.video import production
        for config in production.load_all(config_dir, jobs_dir).values():
            shared = render_keys & set(config.render or {})
            if shared:
                problems.append(f"生产配置「{config.key}」设了 {'、'.join(sorted(shared))}，会盖掉 B 组的值")
    return problems


def queue_problems(problems: list[str]) -> list[str]:
    """What still blocks queueing: a queued experiment may wait for a conflict to end or a preview to be confirmed."""
    return [p for p in problems if CONFLICT not in p and p != NO_PREVIEW]
