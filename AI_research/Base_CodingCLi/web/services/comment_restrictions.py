"""流量受影响 because of an undeclared promotion in the comments: delete the owner's link comments, pin a guide.

B站 limits a video once it passes review traffic (about 1000 views) when the comments carry an outside
purchase link without a 商业声明; the notice names 评论区 as the place. The creator list exposes this as
``limit_state`` plus a problem whose position is 评论区. Only that case is acted on, only the owner's own
comments that carry an outside link are deleted, and the guide comment carries no outside link. The notice
also asks for an appeal: that stays with the creator, the page lists the videos that need one.
"""

from __future__ import annotations

import json
import logging
import re
import time

from web.services import bilibili, platform_manager as pm

logger = logging.getLogger(__name__)

SCAN_EVERY = 1800
SCAN_PAGES = 10
STALE = 600
#: B站's own hosts; a guide pointing at the owner's whisper page is not an outside link.
OWN_HOSTS = re.compile(r"^(?:[\w-]+\.)*(?:bilibili\.com|b23\.tv|bilibili\.tv|hdslb\.com)$", re.I)
URL = re.compile(r"https?://([^/\s，。,]+)", re.I)
_last_scan = 0.0


def settings() -> dict:
    value = pm.store().settings().get("restricted_comment") or {}
    return {"enabled": bool(value.get("enabled")), "text": str(value.get("text") or "").strip(),
            "pin": bool(value.get("pin", True))}


def clean_settings(value: dict) -> dict:
    text = str(value.get("text") or "").strip()
    enabled = bool(value.get("enabled"))
    if len(text) > 1000 or (enabled and not text):
        raise pm.Error("启用限流换评时需填写 1–1000 字的引导评论。")
    if outside_link(text):
        raise pm.Error("引导评论不能带站外链接，否则会再次触发限流。")
    return {"enabled": enabled, "text": text, "pin": bool(value.get("pin", True))}


def outside_link(text: str) -> bool:
    return any(not OWN_HOSTS.match(host) for host in URL.findall(text or ""))


def promotion_limited(row: dict) -> bool:
    """The platform's limit flag, for a commercial-promotion problem located in the comments."""
    return bool(row.get("limit_state")) and any(
        p.get("position") == "评论区" and ("商业" in p.get("reason", "") + p.get("title", ""))
        for p in row.get("problems") or [])


def _db():
    db = pm.store()
    with db.connect(write=True) as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS restrictions (
            uid TEXT NOT NULL, bvid TEXT NOT NULL, title TEXT NOT NULL DEFAULT '',
            reason TEXT NOT NULL DEFAULT '', status TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1,
            deleted TEXT NOT NULL DEFAULT '[]', guide_rpid TEXT NOT NULL DEFAULT '', error TEXT NOT NULL DEFAULT '',
            detected_at REAL NOT NULL, updated_at REAL NOT NULL, PRIMARY KEY(uid, bvid))""")
    return db


def restrictions(uid: str) -> list[dict]:
    if not uid:
        return []
    with _db().connect() as conn:
        rows = conn.execute("SELECT * FROM restrictions WHERE uid=? ORDER BY detected_at DESC", (uid,)).fetchall()
    return [{**dict(r), "deleted": json.loads(r["deleted"]), "active": bool(r["active"])} for r in rows]


def record(uid: str, archives: list[dict], *, complete: bool) -> list[str]:
    """Insert newly limited videos; mark ones no longer limited as lifted (only when the scan saw them)."""
    now, new = time.time(), []
    limited = {a["bvid"]: a for a in archives if promotion_limited(a)}
    seen = {a["bvid"] for a in archives}
    with _db().connect(write=True) as conn:
        for bvid, a in limited.items():
            reason = next((p["reason"] for p in a["problems"] if p.get("position") == "评论区"), "")
            if conn.execute("INSERT OR IGNORE INTO restrictions (uid,bvid,title,reason,status,detected_at,updated_at) "
                            "VALUES (?,?,?,?,'DETECTED',?,?)", (uid, bvid, a.get("title", ""), reason, now, now)).rowcount:
                new.append(bvid)
            else:
                conn.execute("UPDATE restrictions SET active=1 WHERE uid=? AND bvid=?", (uid, bvid))
        for (bvid,) in conn.execute("SELECT bvid FROM restrictions WHERE uid=? AND active=1", (uid,)).fetchall():
            if bvid not in limited and (bvid in seen or complete):
                conn.execute("UPDATE restrictions SET active=0,updated_at=? WHERE uid=? AND bvid=?", (now, uid, bvid))
    return new


def _claim(uid: str, bvid: str, old: str, new: str) -> bool:
    with _db().connect(write=True) as conn:
        return bool(conn.execute("UPDATE restrictions SET status=?,error='',updated_at=? WHERE uid=? AND bvid=? AND status=?",
                                 (new, time.time(), uid, bvid, old)).rowcount)


def _set(uid: str, bvid: str, **fields) -> None:
    if "deleted" in fields:
        fields["deleted"] = json.dumps(fields["deleted"], ensure_ascii=False)
    keys = ",".join(f"{k}=?" for k in fields)
    with _db().connect(write=True) as conn:
        conn.execute(f"UPDATE restrictions SET {keys},updated_at=? WHERE uid=? AND bvid=?",
                     (*fields.values(), time.time(), uid, bvid))


def recover(uid: str) -> None:
    """Called under the worker lock. Deleting again is safe; posting again is not."""
    cutoff = time.time() - STALE
    with _db().connect(write=True) as conn:
        conn.execute("UPDATE restrictions SET status='DETECTED' WHERE uid=? AND status='CLEANING' AND updated_at<?",
                     (uid, cutoff))
        conn.execute("UPDATE restrictions SET status='UNKNOWN',error=? WHERE uid=? AND status='POSTING' AND updated_at<?",
                     ("引导评论发送中断，请到视频下核对；系统不会自动重发。", uid, cutoff))


def plan(api: bilibili.BilibiliClient, uid: str, bvid: str) -> tuple[dict, list[dict]]:
    """What a swap would delete on this video: the owner's comments that carry an outside link."""
    info = api.owned_video(bvid, uid)
    return info, [c for c in api.own_comments(info, uid) if outside_link(c["message"])]


def process(uid: str, bvid: str) -> str:
    """DETECTED -> CLEANING -> (CLEANED | POSTING -> SWAPPED); errors end in FAILED/UNKNOWN/CLOSED."""
    if not _claim(uid, bvid, "DETECTED", "CLEANING"):
        return ""
    api = pm.client(uid)
    try:
        info, targets = plan(api, uid, bvid)
    except bilibili.BilibiliRejected as exc:
        status = "CLOSED" if exc.code == 12002 else "FAILED"
        _set(uid, bvid, status=status, error="评论区已关闭，没有可删的评论。" if status == "CLOSED" else str(exc))
        return status
    except bilibili.BilibiliError as exc:
        _set(uid, bvid, status="DETECTED", error=str(exc))  # nothing changed yet; try on the next scan
        return "DETECTED"
    deleted = []
    for comment in targets:
        try:
            api.delete_comment(info, comment["rpid"])
        except bilibili.BilibiliError as exc:
            _set(uid, bvid, status="FAILED", deleted=deleted, error=f"删除评论 {comment['rpid']} 失败：{exc}")
            return "FAILED"
        deleted.append(comment)
    guide = settings()
    if not guide["enabled"]:
        _set(uid, bvid, status="CLEANED", deleted=deleted)
        return "CLEANED"
    _set(uid, bvid, status="POSTING", deleted=deleted)
    try:
        rpid = api.post_comment(info, guide["text"])
    except bilibili.BilibiliRejected as exc:
        _set(uid, bvid, status="FAILED", error=f"推广评论已删，引导评论发送失败：{exc}")
        return "FAILED"
    except Exception:
        _set(uid, bvid, status="UNKNOWN", error="引导评论发送结果未确认，请到视频下核对；系统不会自动重发。")
        return "UNKNOWN"
    _set(uid, bvid, guide_rpid=rpid)
    if guide["pin"]:
        try:
            api.pin_comment(info, rpid)
        except Exception as exc:
            _set(uid, bvid, status="SWAPPED", error=f"引导评论已发，置顶未确认：{exc}")
            return "SWAPPED"
    _set(uid, bvid, status="SWAPPED")
    return "SWAPPED"


def tick(uid: str, *, force: bool = False) -> None:
    """Called from the platform worker under its lock: scan at most every half hour, handle one video per tick."""
    global _last_scan
    recover(uid)
    if force or time.time() - _last_scan >= SCAN_EVERY:
        _last_scan = time.time()
        try:
            listing = pm.client(uid).archives(SCAN_PAGES)
        except bilibili.BilibiliError as exc:
            logger.warning("restriction scan failed: %s", exc)
        else:
            for bvid in record(uid, listing["archives"], complete=listing["complete"]):
                logger.warning("流量受影响（评论区商业推广）：%s", bvid)
    with _db().connect() as conn:
        row = conn.execute("SELECT bvid FROM restrictions WHERE uid=? AND active=1 AND status='DETECTED' "
                           "AND (error='' OR updated_at<?) ORDER BY detected_at LIMIT 1",
                           (uid, time.time() - SCAN_EVERY)).fetchone()
    if row:
        process(uid, row["bvid"])
