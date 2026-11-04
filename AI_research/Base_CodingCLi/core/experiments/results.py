"""From assignments to a conclusion (spec §7, §8): who counts, what was measured, and what the programme concludes.

Everything existing is opened read-only: ``video.db`` (scripts, versions, publications) and ``platform_analytics.db``
(videos, daily watch readings, play snapshots). Only the module's own database is written, by ``evaluate_all``.
"""
from __future__ import annotations

import json
import re
import sqlite3
import time
from pathlib import Path
from typing import Optional

from core.experiments import analysis as an
from core.experiments import definition as defs
from core.experiments.definition import Experiment
from core.experiments.store import ExperimentStore

READ_AFTER = 48 * 3600          # every metric is the first reading at least 48 hours after publishing (§8)
SETTLE = 72 * 3600              # wind-down: the last counted video has been public this long ...
WIND_DOWN_MAX = 7 * 86400       # ... or a week has passed since the end point
_BV = re.compile(r"BV[0-9A-Za-z]{10}")
TERMINAL = {"FAILED"}


def _ro(path: Path) -> sqlite3.Connection:
    db = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=2)
    db.row_factory = sqlite3.Row
    return db


def _ts(text: str | None) -> Optional[float]:
    if not text:
        return None
    from datetime import datetime
    for fmt in ("%Y-%m-%dT%H:%M:%S%z",):
        try:
            return datetime.strptime(text, fmt).timestamp()
        except ValueError:
            pass
    try:
        return datetime.fromisoformat(text).timestamp()
    except ValueError:
        return None


# ---- reading ------------------------------------------------------------------------------------------------------

def _scripts(video_db: Path, ids: list[str]) -> dict[str, dict]:
    if not ids or not video_db.is_file():
        return {}
    out = {}
    with _ro(video_db) as db:
        for chunk in range(0, len(ids), 500):
            part = ids[chunk:chunk + 500]
            marks = ",".join("?" * len(part))
            for r in db.execute(f"SELECT id, status, archived, content_context FROM scripts WHERE id IN ({marks})", part):
                out[r["id"]] = {"status": r["status"], "archived": r["archived"],
                                "context": json.loads(r["content_context"] or "{}"), "wrote": False, "bvid": None,
                                "removed": False}
            for r in db.execute(f"SELECT DISTINCT script_id FROM script_versions WHERE script_id IN ({marks})", part):
                out[r["script_id"]]["wrote"] = True
            for r in db.execute(f"SELECT script_id, url, removed_at FROM publications WHERE platform='bilibili' "
                                f"AND script_id IN ({marks}) ORDER BY created_at", part):
                s = out[r["script_id"]]
                if s["bvid"] is None:  # only the first upload counts (§8「重新投稿」)
                    found = _BV.search(r["url"] or "")
                    s["bvid"] = found.group() if found else ""
                    s["removed"] = bool(r["removed_at"])
    return out


def _measure(analytics_db: Path, bvids: list[str], now: float) -> dict[str, dict]:
    """Per video: pubdate, duration and the first readings at least 48 hours after publishing."""
    if not bvids or not analytics_db.is_file():
        return {}
    from core.telemetry.weekly_review import views_at
    out: dict[str, dict] = {}
    with _ro(analytics_db) as db:
        for bvid in bvids:
            video = db.execute("SELECT pubdate, duration, state FROM analytics_videos WHERE bvid=?", (bvid,)).fetchone()
            if not video or not video["pubdate"]:
                continue
            pub = float(video["pubdate"])
            row: dict = {"pubdate": pub, "duration": video["duration"] or None}
            for r in db.execute("SELECT data, collected_at FROM analytics_watch WHERE bvid=? AND collected_at>=? "
                                "ORDER BY collected_at", (bvid, pub + READ_AFTER)):
                data = json.loads(r["data"] or "{}")
                # completion only from the per-video endpoint: that row also carries avg_seconds (§8)
                if "completion" not in row and data.get("avg_seconds") is not None and data.get("completion") is not None:
                    row["completion"], row["avg_seconds"] = data["completion"], data["avg_seconds"]
                elif "completion" not in row and data.get("completion") is not None:
                    row["completion_from_list"] = True  # a diagnosis-list reading: not used (§8), but counted
                if "views_48h" not in row and data.get("views") is not None:
                    row["views_48h"] = data["views"]
                for key in ("exit3s", "cover_pass"):
                    if key not in row and data.get(key) is not None:
                        row[key] = data[key]
            snaps = [(s["observed_at"], json.loads(s["metrics"] or "{}").get("view") or 0) for s in
                     db.execute("SELECT observed_at, metrics FROM analytics_snapshots WHERE bvid=? ORDER BY observed_at",
                                (bvid,))]
            state, views = views_at(snaps, pub, 24, now)
            if state == "ok":
                row["views_24h"] = views
            if "completion" in row:
                row.pop("completion_from_list", None)
            out[bvid] = row
    return out


def duration_fit(analytics_db: Path, before: float) -> Optional[dict]:
    """The length → completion line from videos published before the experiment started (§8)."""
    if not analytics_db.is_file():
        return None
    points = []
    with _ro(analytics_db) as db:
        for r in db.execute("SELECT v.duration, w.data FROM analytics_videos v JOIN analytics_watch w ON w.bvid=v.bvid "
                            "WHERE v.pubdate < ? AND v.duration > 0 ORDER BY w.collected_at", (before,)):
            data = json.loads(r["data"] or "{}")
            if data.get("avg_seconds") is not None and data.get("completion") is not None:
                points.append((r["duration"], data["completion"]))
    return an.fit_duration(points)


# ---- time ---------------------------------------------------------------------------------------------------------

def paused_seconds(events: list[dict], master: list[dict], start: float, until: float) -> float:
    """Time within [start, until] the experiment was paused or the master switch was off (not counted in max_days)."""
    def spans(rows, off, on):
        out, opened = [], None
        for e in rows:
            if e["kind"] == off and opened is None:
                opened = e["at"]
            elif e["kind"] == on and opened is not None:
                out.append((opened, e["at"]))
                opened = None
        if opened is not None:
            out.append((opened, until))
        return out
    total = 0.0
    for a, b in spans(events, "pause", "resume") + spans(master, "master_off", "master_on"):
        total += max(0.0, min(b, until) - max(a, start))
    return total


# ---- one experiment -----------------------------------------------------------------------------------------------

def applied(experiment: Experiment, script_id: str, script: dict, state_dir: Path) -> Optional[bool]:
    """Whether B's change was really used on this video (§6「怎么判断生效」); None when it cannot be told."""
    paths = experiment.write_paths()
    if paths == ["cover_style"]:
        value = experiment.arms["B"].overrides["cover_style"]
        copies = list((state_dir / "platforms" / "covers").glob("*-copy.json"))
        used = False
        renders = _renders_of(state_dir / "video.db", script_id)
        for path in copies:
            if path.name.split("-")[0] in renders:
                try:
                    used = used or bool(json.loads(path.read_text(encoding="utf-8")).get("copy"))
                except (OSError, ValueError):
                    pass
        return (not used) if value == "title" else used
    if paths == ["presentation.opening_card"]:
        flags = _opening_flags(state_dir / "video.db", script_id)
        return any(flags) if flags else None
    return None


def _opening_flags(video_db: Path, script_id: str) -> list[bool]:
    if not video_db.is_file():
        return []
    with _ro(video_db) as db:
        rows = db.execute("SELECT visuals FROM renders WHERE script_id=? AND status='COMPLETED'", (script_id,)).fetchall()
    out = []
    for r in rows:
        note = (json.loads(r["visuals"] or "{}") or {}).get("opening_card")
        if isinstance(note, dict) and "applied" in note:
            out.append(bool(note["applied"]))
    return out


def _renders_of(video_db: Path, script_id: str) -> set[str]:
    if not video_db.is_file():
        return set()
    with _ro(video_db) as db:
        return {r[0] for r in db.execute("SELECT id FROM renders WHERE script_id=?", (script_id,))}


def production_gaps(video_db: Path, start: float, until: float) -> int:
    """Days in [start, until) with no automatic video published to bilibili at all (§8「产量起伏」)."""
    if not video_db.is_file() or until <= start:
        return 0
    try:
        with _ro(video_db) as db:
            days = {r[0] for r in db.execute(
                "SELECT DISTINCT substr(p.created_at, 1, 10) FROM publications p JOIN scripts s ON s.id = p.script_id "
                "WHERE p.platform='bilibili' AND s.created_by='autopilot'")}
    except sqlite3.Error:
        return 0
    gaps, day = 0, start
    while day < until - 3600:
        if time.strftime("%Y-%m-%d", time.localtime(day)) not in days:
            gaps += 1
        day += 86400
    return gaps


def costs(config_dir: str, script_ids: dict[str, str], since: float) -> dict:
    """Mean model cost and render minutes per arm from the existing usage records (§8「成本」); empty when unknown."""
    try:
        from core.telemetry import query
        bills = query.scripts(config_dir, since=since - 86400, limit=100000).get("items") or []
    except Exception:  # noqa: BLE001 - cost is shown when it can be read
        return {}
    by = {b["script_id"]: b for b in bills}
    out = {}
    for arm in ("A", "B"):
        mine = [by[s] for s, a in script_ids.items() if a == arm and s in by]
        if mine:
            out[arm] = {"scripts": len(mine), "cost_usd": sum(b.get("cost_usd") or 0 for b in mine) / len(mine),
                        "render_minutes": sum(b.get("render_seconds") or 0 for b in mine) / len(mine) / 60,
                        "partial": any(b.get("partial") for b in mine)}
    return out


def evaluate(store: ExperimentStore, experiment_id: str, state_dir: str | Path, now: float | None = None,
             config_dir: str | None = None) -> dict:
    """Everything the tab shows for one experiment, computed now. Writes nothing. Costs only with ``config_dir``."""
    now = now or time.time()
    state_dir = Path(state_dir)
    found = store.get(experiment_id)
    experiment = Experiment.model_validate(found["definition"])
    start = found["started_at"] or found["created_at"]
    events = store.events(experiment_id)
    master = store.events("")
    ended_at = next((e["at"] for e in events if e["kind"] == "end"), None)
    assignments = [a for a in store.assignments(experiment_id) if a["script_id"]]
    scripts = _scripts(state_dir / "video.db", [a["script_id"] for a in assignments])
    measured = _measure(state_dir / "platform_analytics.db",
                        [s["bvid"] for s in scripts.values() if s.get("bvid")], now)
    fit = duration_fit(state_dir / "platform_analytics.db", start)
    keep_failures = experiment.counts_failures_before_writing()
    videos = []
    for a in assignments:
        s = scripts.get(a["script_id"])
        if s is None or (not keep_failures and not s["wrote"]):
            continue  # failed before any arm-dependent step: not in the denominator (§7「资格」)
        m = measured.get(s["bvid"] or "", {}) if s.get("bvid") and not s["removed"] else {}
        row = {"script_id": a["script_id"], "arm": a["arm"], "column": a["column_key"],
               "matrix": s["context"].get("matrix") or s["context"].get("source_matrix") or "",
               "bvid": s.get("bvid") or "", "removed": s["removed"], **m}
        if "completion" in row:
            row["completion_adjusted"] = an.adjusted_completion(row["completion"], row.get("duration"), fit)
        if s["removed"] or (s["status"] in TERMINAL and not s.get("bvid")):
            row["state"] = "missing"
        elif not s.get("bvid") or "pubdate" not in row or now < row["pubdate"] + READ_AFTER:
            row["state"] = "pending"
        else:
            row["state"] = "measured"
        videos.append(row)
    keys = an.stratum_keys([{"column": v["column"], "matrix": v["matrix"], "pubdate": v.get("pubdate", start)}
                            for v in videos], start)
    for v, k in zip(videos, keys):
        v["stratum"] = k

    def rows(metric: str) -> list[dict]:
        return [{"arm": v["arm"], "stratum": v["stratum"], "value": v.get(metric)} for v in videos]

    primary = experiment.metric.primary
    main = an.estimate(rows(primary))
    guards = []
    for g in experiment.guardrails:
        counted = g.metric in ("views_24h", "avg_seconds")
        result = an.estimate(rows(g.metric), "ratio" if counted else "difference")
        status = an.guardrail_status(result, g.better, defs.COUNT_THRESHOLD if counted else defs.RATIO_THRESHOLD)
        guards.append({"metric": g.metric, "status": status, **result})
    # settled first: once the wind-down is over, anything still without a reading counts as missing (§8「终点」)
    active = (ended_at or now) - start - paused_seconds(events, master, start, ended_at or now)
    counted = [v for v in videos if v["state"] != "missing"]
    last_pub = max((v.get("pubdate") or 0 for v in counted), default=0)
    in_flight = any(v["state"] == "pending" and "pubdate" not in v for v in counted)
    settled = ended_at is not None and (now >= ended_at + WIND_DOWN_MAX
                                         or (not in_flight and now >= last_pub + SETTLE))
    if settled:
        for v in videos:
            if v["state"] == "pending":
                v["state"] = "missing"
    n = {arm: sum(1 for v in videos if v["arm"] == arm) for arm in ("A", "B")}
    with_data = {arm: sum(1 for v in videos if v["arm"] == arm and v.get(primary) is not None) for arm in ("A", "B")}
    missing = {arm: sum(1 for v in videos if v["arm"] == arm and v["state"] == "missing") for arm in ("A", "B")}
    srm = an.srm_p(n["A"], n["B"], experiment.share_b)
    rate = lambda arm: missing[arm] / n[arm] if n[arm] else 0.0  # noqa: E731
    harm = an.harm_hint([v["views_24h"] for v in videos if v["arm"] == "A" and v.get("views_24h") is not None],
                        [v["views_24h"] for v in videos if v["arm"] == "B" and v.get("views_24h") is not None],
                        rate("A"), rate("B"))
    flags = [applied(experiment, v["script_id"], scripts[v["script_id"]], state_dir) for v in videos if v["arm"] == "B"]
    known = [f for f in flags if f is not None]
    applied_rate = (sum(known) / len(known)) if known else None
    reached = min(with_data.values()) >= experiment.stop.per_arm or active >= experiment.stop.max_days * 86400
    verdict = an.verdict(main, experiment.metric.better, ended=settled, srm=srm, guardrails=guards,
                         applied_rate=applied_rate if experiment.kind == "compare" else None)
    measured_n = {arm: sum(1 for v in videos if v["arm"] == arm and v["state"] == "measured") for arm in ("A", "B")}
    no_metric = {arm: sum(1 for v in videos if v["arm"] == arm and v["state"] == "measured" and v.get(primary) is None)
                 for arm in ("A", "B")}
    gap = lambda counts, base: abs(counts["A"] / base["A"] - counts["B"] / base["B"]) if base["A"] and base["B"] else 0.0  # noqa: E731
    missing_alarm = (min(n.values()) >= 5 and gap(missing, n) > an.MISSING_GAP) or \
        (min(measured_n.values()) >= 5 and gap(no_metric, measured_n) > an.MISSING_GAP)
    if missing_alarm:
        verdict["notes"] = [*verdict.get("notes", []), "数据缺失不均，结论不可信"]
    reference = an.estimate([{"arm": v["arm"], "stratum": v["stratum"], "value": v.get(primary)}
                             for v in videos if (v.get("views_48h") or 0) >= an.REFERENCE_VIEWS], resamples=2000)
    skipped = {arm: sum(1 for v in videos if v["arm"] == arm and v.get("completion_from_list")) for arm in ("A", "B")}
    code = next((e["detail"] for e in reversed(events) if e["kind"] == "code_changed"), None)
    return {"experiment_id": experiment_id, "computed_at": now, "n": n, "with_data": with_data, "missing": missing,
            "srm_p": srm, "primary": {"metric": primary, **main}, "guardrails": guards, "harm": harm,
            "applied_rate": applied_rate, "active_days": active / 86400, "endpoint_reached": reached,
            "ended_at": ended_at, "settled": settled, "verdict": verdict, "fit": fit,
            "median_views": {arm: _median([v.get("views_24h") for v in videos if v["arm"] == arm]) for arm in ("A", "B")},
            "median_duration": {arm: _median([v.get("duration") for v in videos if v["arm"] == arm]) for arm in ("A", "B")},
            "no_metric": no_metric, "missing_alarm": missing_alarm, "reference": {"min_views": an.REFERENCE_VIEWS, **reference},
            "completion_skipped": skipped, "code_changed": code,
            "production_gaps": production_gaps(state_dir / "video.db", start, ended_at or now),
            "costs": costs(config_dir, {v["script_id"]: v["arm"] for v in videos}, start) if config_dir else {},
            "videos": videos}


def _median(values: list) -> Optional[float]:
    import statistics
    kept = [x for x in values if x is not None]
    return statistics.median(kept) if kept else None


def _check_code(store: ExperimentStore, row: dict, kinds: set) -> None:
    """Warn once when files that read the tested switch changed between assignment and the running code (§7)."""
    if "code_changed" in kinds or row["status"] not in ("running", "paused"):
        return
    from core.experiments import background
    experiment = Experiment.model_validate(row["definition"])
    files = sorted({f for s in experiment.switches() for f in s.readers})
    current = background.code_version()
    versions = {a["code_version"] for a in store.assignments(row["id"])} - {current, "未知", ""}
    if not files or current == "未知" or not versions:
        return
    changed = set()
    for old in sorted(versions):
        changed |= set(background.changed_files(old, current, files))
    if changed:
        with store.connect(write=True) as db:
            store._event(db, row["id"], "code_changed", "system", {"files": sorted(changed), "from": sorted(versions),
                                                                  "to": current})


def evaluate_all(state_dir: str | Path, now: float | None = None) -> None:
    """The timer's pass: raise alarms once, stop at the end point, and freeze the result after the wind-down."""
    now = now or time.time()
    store = ExperimentStore(state_dir)
    for row in store.list():
        if row["status"] not in ("running", "paused", "ended"):
            continue
        if row["status"] == "ended" and store.result(row["id"]):
            continue
        result = evaluate(store, row["id"], state_dir, now)
        kinds = {e["kind"] for e in store.events(row["id"])}
        with store.connect(write=True) as db:
            if result["srm_p"] < an.SRM_ALPHA and "srm_alarm" not in kinds and sum(result["n"].values()) >= 10:
                store._event(db, row["id"], "srm_alarm", "system", {"p": result["srm_p"]})
            if result["harm"] and "harm_hint" not in kinds:
                store._event(db, row["id"], "harm_hint", "system", {"message": result["harm"]})
            if result["missing_alarm"] and "missing_alarm" not in kinds:
                store._event(db, row["id"], "missing_alarm", "system", {"missing": result["missing"],
                                                                       "no_metric": result["no_metric"]})
        _check_code(store, row, kinds)
        if row["status"] in ("running", "paused") and result["endpoint_reached"]:
            store.move(row["id"], "end", "system", {"reason": "endpoint"})
            with store.connect(write=True) as db:
                store._event(db, row["id"], "endpoint", "system", {"with_data": result["with_data"]})
        elif row["status"] == "ended" and result["settled"]:
            slim = {k: v for k, v in result.items() if k != "videos"}
            store.save_result(row["id"], {**slim, "videos": [{k: v.get(k) for k in ("script_id", "arm", "bvid", "state")}
                                                             for v in result["videos"]]})
            with store.connect(write=True) as db:
                store._event(db, row["id"], "verdict", "system", {"verdict": result["verdict"]["verdict"]})
