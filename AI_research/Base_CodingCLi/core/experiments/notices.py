"""Home-page reminders about experiments (spec §9a step ④). Read-only; the home board decides how to show them."""
from __future__ import annotations

from pathlib import Path

from core.experiments.store import ExperimentStore

#: event kind → reminder title (``{name}`` is the experiment's name). Others are not worth a reminder.
TITLES = {
    "start": "实验「{name}」开始了",
    "srm_alarm": "实验「{name}」分组失衡，结论不可信",
    "harm_hint": "实验「{name}」建议提前停止",
    "endpoint": "实验「{name}」到达终点，进入收尾期",
    "verdict": "实验「{name}」有结论了",
}
PREVIEW = {"done": "实验「{name}」的预览出好了，可以去看", "failed": "实验「{name}」的预览没出成"}


def recent(state_dir: str | Path, now: float, days: float) -> list[dict]:
    """Reminders from the last ``days``: keys are stable, so acknowledging one clears it."""
    path = Path(state_dir) / "experiments.db"
    if not path.is_file():
        return []
    store = ExperimentStore(state_dir)
    since = now - days * 86400
    names = {e["id"]: e["definition"].get("name") or e["id"] for e in store.list()}
    out = []
    with store.connect() as db:
        events = db.execute("SELECT * FROM events WHERE at >= ? ORDER BY at", (since,)).fetchall()
        previews = db.execute("SELECT * FROM previews WHERE finished_at >= ?", (since,)).fetchall()
    for e in events:
        if e["kind"] in TITLES and e["experiment_id"] in names:
            out.append({"key": f"experiment:{e['experiment_id']}:{e['kind']}:{e['id']}", "kind": "experiment",
                        "at": e["at"], "title": TITLES[e["kind"]].format(name=names[e["experiment_id"]]),
                        "detail": "", "link": "/analytics?tab=experiments"})
    for p in previews:
        if p["status"] in PREVIEW and p["experiment_id"] in names:
            out.append({"key": f"experiment-preview:{p['id']}", "kind": "experiment", "at": p["finished_at"],
                        "title": PREVIEW[p["status"]].format(name=names[p["experiment_id"]]),
                        "detail": p["error"] or "", "link": "/analytics?tab=experiments"})
    return out
