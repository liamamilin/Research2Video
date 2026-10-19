"""How much of a generated draft survives to the approved script: a measure of draft quality.

A person's edits are the only judgement of the draft that does not come from a model. For each approved script the
baseline is the newest generated version at or before the approved one (a restyle is generated too, so a script
written again in another style is measured against that rewrite). The comparison is by paragraph and by character:
the share of the draft's words kept, how many paragraphs were left alone, edited, replaced or dropped, and the
edits that changed the most, so the person tuning a prompt sees what the model got wrong.

Small numbers: a handful of scripts say little; the report gives the count with every figure.
"""

from __future__ import annotations

import difflib
import statistics
from typing import Optional

PAIR_SIMILARITY = 0.5   # a draft paragraph and a final one this alike are the same paragraph, edited
EXAMPLES = 5


def _paragraphs(version: dict) -> list[str]:
    return [str(p.get("text") or "").strip() for s in version.get("sections") or [] for p in s.get("paragraphs") or []
            if str(p.get("text") or "").strip()]


def baseline(versions: list[dict], approved: int) -> Optional[dict]:
    """The newest generated version numbered at most ``approved``; None when there is none."""
    generated = [v for v in versions if v["edit_type"] == "GENERATED" and v["version"] <= approved]
    return max(generated, key=lambda v: v["version"]) if generated else None


def _similar(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a, b, autojunk=False).ratio()


def compare(draft: dict, final: dict) -> dict:
    """Paragraph and character differences between two versions (full rows with sections)."""
    before, after = _paragraphs(draft), _paragraphs(final)
    unchanged = edited = 0
    pairs: list[tuple[str, str]] = []
    matcher = difflib.SequenceMatcher(None, before, after, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            unchanged += i2 - i1
            continue
        old, new = before[i1:i2], after[j1:j2]
        used: set[int] = set()
        for text in old:
            best = max(((_similar(text, cand), k) for k, cand in enumerate(new) if k not in used), default=(0, -1))
            if best[0] >= PAIR_SIMILARITY:
                used.add(best[1])
                edited += 1
                pairs.append((text, new[best[1]]))
    paired_old = unchanged + edited
    dropped = len(before) - paired_old
    added = len(after) - paired_old
    joined_before, joined_after = "".join(before), "".join(after)
    chars = difflib.SequenceMatcher(None, joined_before, joined_after, autojunk=False)
    kept = sum(block.size for block in chars.get_matching_blocks())
    examples = sorted(({"before": a, "after": b, "similarity": round(_similar(a, b), 2)} for a, b in pairs),
                      key=lambda e: e["similarity"])[:EXAMPLES]
    return {
        "draft_paragraphs": len(before), "final_paragraphs": len(after),
        "unchanged": unchanged, "edited": edited, "dropped": dropped, "added": added,
        "kept_share": round(kept / len(joined_before), 2) if joined_before else None,
        "new_share": round((len(joined_after) - kept) / len(joined_after), 2) if joined_after else None,
        "examples": examples,
    }


def draft_signals(draft: dict) -> dict:
    """What the draft already knew about itself: its length verdict, the model audit's findings, lint problems."""
    meta = draft.get("meta") or {}
    summary = (meta.get("checks") or {}).get("summary") or {}
    return {
        "length_status": (draft.get("length_check") or {}).get("status"),
        "serious": summary.get("serious"),
        "must": summary.get("must"),
        "problems": len(meta.get("problems") or []),
        "audited": bool((meta.get("checks") or {}).get("model_ran")),
    }


def script_report(script: dict, versions: list[dict]) -> Optional[dict]:
    """One approved script's draft-to-final comparison; None for a script not yet approved or with no draft."""
    approved = script.get("approved_version")
    if not approved:
        return None
    by_number = {v["version"]: v for v in versions}
    final = by_number.get(approved)
    draft = baseline(versions, approved)
    if final is None or draft is None:
        return None
    return {
        "script_id": script["id"], "column_key": script["column_key"], "title": final["title"],
        "draft_version": draft["version"], "approved_version": approved, "edited_by_hand": final["edit_type"] == "EDITED",
        "signals": draft_signals(draft), **compare(draft, final),
    }


def summarize(reports: list[dict]) -> dict:
    """Per column: how many scripts, the median share of the draft kept, and the share of paragraphs left alone."""
    out: dict[str, dict] = {}
    for column in sorted({r["column_key"] for r in reports}):
        rows = [r for r in reports if r["column_key"] == column]
        kept = [r["kept_share"] for r in rows if r["kept_share"] is not None]
        total = sum(r["draft_paragraphs"] for r in rows)
        out[column] = {
            "scripts": len(rows),
            "edited_by_hand": sum(r["edited_by_hand"] for r in rows),
            "median_kept_share": round(statistics.median(kept), 2) if kept else None,
            "paragraphs_untouched": round(sum(r["unchanged"] for r in rows) / total, 2) if total else None,
        }
    return out
