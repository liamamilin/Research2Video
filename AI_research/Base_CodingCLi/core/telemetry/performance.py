"""What a published video cost against how it was watched: each video's bill joined to its play count."""
from __future__ import annotations

from typing import Optional

from core.telemetry import query, store

GROUP_BY = query.COMPARE_BY


def _views(video: dict) -> Optional[int]:
    """The play count, or None when the platform has not given a usable one (hidden, removed, not yet read)."""
    if not video.get("available"):
        return None
    value = (video.get("metrics") or {}).get("view")
    return value if type(value) is int else None


def _per_thousand(amount: float, views: int) -> Optional[float]:
    return round(amount / views * 1000, 6) if views > 0 else None


def report(config_dir: str, videos: list[dict], by: str = "column_key") -> dict:
    """Cost, tokens and plays per published video, and the same grouped by one attribute.

    ``videos`` are platform rows with a ``publication`` naming the script they were made from. A script published
    as several videos splits its bill evenly between them. A bill with some unpriced calls still gives a cost, marked
    ``partial`` (the true cost is higher); a bill with nothing priced gives none, so a missing price never reads as a
    free video.
    """
    if by not in GROUP_BY:
        raise ValueError(f"cannot compare by {by!r}")
    linked = [v for v in videos if (v.get("publication") or {}).get("script_id")]
    with store.connect() as conn:
        if conn is None:
            return {"enabled": False, "by": by, "items": [], "groups": []}
        bills = {b["script_id"]: b for b in query._script_bills(conn, config_dir, None, None, {})}  # noqa: SLF001
    shares: dict = {}
    for video in linked:
        sid = video["publication"]["script_id"]
        shares[sid] = shares.get(sid, 0) + 1
    items = []
    for video in linked:
        publication = video["publication"]
        bill = bills.get(publication["script_id"])
        share = shares[publication["script_id"]]
        views = _views(video)
        row = {"bvid": video["bvid"], "title": video.get("title", ""), "pubdate": video.get("pubdate", 0),
               "view": views, "script_id": publication["script_id"], "billed": bill is not None,
               "column_key": "", "form": "", "matrix": "", "recipe": "", "path": "",
               "tokens": None, "cost_usd": None, "partial": False, "produce_seconds": None, "render_seconds": None,
               "cost_per_kview": None, "tokens_per_kview": None}
        if bill:
            row.update({key: bill[key] for key in GROUP_BY})
            nothing_priced = bill["partial"] and not bill["cost_usd"]
            row.update(tokens=round(bill["total_tokens"] / share), partial=bill["partial"],
                       cost_usd=None if nothing_priced else round(bill["cost_usd"] / share, 6),
                       produce_seconds=bill["produce_seconds"], render_seconds=bill["render_seconds"])
        if not row["matrix"]:
            row["matrix"] = publication.get("matrix") or ""
        if views and bill:
            row["tokens_per_kview"] = _per_thousand(row["tokens"], views)
            if row["cost_usd"] is not None:
                row["cost_per_kview"] = _per_thousand(row["cost_usd"], views)
        items.append(row)
    items.sort(key=lambda i: -(i["pubdate"] or 0))
    return {"enabled": True, "by": by, "items": items, "groups": _groups(items, by)}


def _groups(items: list[dict], by: str) -> list[dict]:
    buckets: dict = {}
    for item in items:
        buckets.setdefault(item[by], []).append(item)
    out = []
    for value, members in buckets.items():
        watched = [m for m in members if m["view"] is not None and m["billed"]]
        priced = [m for m in watched if m["cost_usd"] is not None]
        views = sum(m["view"] for m in watched)
        priced_views = sum(m["view"] for m in priced)
        out.append({
            "value": value, "videos": len(members), "watched": len(watched), "views": views,
            "view_median": query._median([m["view"] for m in watched]),  # noqa: SLF001
            "tokens_per_kview": _per_thousand(sum(m["tokens"] for m in watched), views),
            "cost_samples": len(priced), "cost_partial": any(m["partial"] for m in priced),
            "cost_avg_usd": round(sum(m["cost_usd"] for m in priced) / len(priced), 6) if priced else None,
            "cost_per_kview": _per_thousand(sum(m["cost_usd"] for m in priced), priced_views)})
    out.sort(key=lambda g: (-g["views"], str(g["value"])))
    return out
