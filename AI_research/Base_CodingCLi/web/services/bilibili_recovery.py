"""Conservative recovery evidence for the pinned native Bilibili uploader.

Never save arbitrary native output: request URLs can contain upload credentials.
An empty archive search is not evidence that a submission was never accepted.
"""
from __future__ import annotations

import html
import re
import unicodedata
from urllib.parse import parse_qs, urlsplit


def failure_evidence(output: str) -> dict:
    """Classify the terminal native error, not earlier retry messages in its log."""
    output = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", output)
    terminal = re.split(r"(?:^|\n)Error:\s*", output)[-1]
    has_terminal = terminal != output or output.startswith("Error:")
    urls = re.findall(r'(?:error sending request for url|url[:=])\s*[\(\"]?(https?://[^\s\)\"<>]+)',
                      terminal, re.I) if has_terminal else []
    phase, host = "upload", ""
    for value in urls:
        url = urlsplit(value.rstrip(",}"))
        name, path = url.hostname or "", url.path
        if name == "member.bilibili.com":
            host = name
            phase = {"/preupload": "preupload", "/x/vu/web/cover/up": "cover",
                     "/x/vu/web/add/v3": "submit"}.get(path, "upload")
        elif name.startswith("upos-") and name.endswith((".bilivideo.com", ".bilivideo.cn")):
            host, phase = name, "transfer"
            query = parse_qs(url.query, keep_blank_values=True)
            if "uploads" in query:
                phase = "upload_init"
            elif "uploadId" in query and "partNumber" not in query and "biz_id" in query:
                phase = "merge"
        else:
            # An unrecognized terminal request must never inherit an earlier safe phase.
            host, phase = "", "upload"
    lower = terminal.lower()
    kind = next((label for label, markers in (
        ("dns", ("dns error", "failed to lookup address")),
        ("timeout", ("timed out", "timedout", "deadline has elapsed")),
        ("connection", ("error sending request", "connection refused", "connection reset", "connection closed", "broken pipe", "unexpected eof", "incompletemessage")),
        ("response", ("error decoding response body",)),
    ) if any(marker in lower for marker in markers)), "native_error")
    statuses = re.findall(r"(?:status(?: code)?[:=( ]+|HTTP[/\d.]*\s+)([45]\d\d)\b", terminal, re.I)
    status = int(statuses[-1]) if statuses else None
    safe = phase in ("preupload", "cover", "upload_init", "transfer", "merge")
    retryable = safe and (kind in ("dns", "timeout", "connection") or status == 429 or bool(status and status >= 500))
    if status and status < 500 and status != 429:
        retryable = False
    return {"phase": phase, "request_host": host, "error_kind": kind,
            "http_status": status, "confirmed_not_submitted": safe, "retryable": retryable}


def normalized(value: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", html.unescape(value))).strip()


def matching_archives(task: dict, archives: list[dict]) -> list[dict]:
    """Require the account-bound title, description, category and submission window."""
    payload = task["payload"]
    start = (task.get("recovery") or {}).get("started_at") or task["created_at"]
    result = {}
    for item in archives:
        if (not re.fullmatch(r"BV[0-9A-Za-z]{10}", str(item.get("bvid", "")))
                or normalized(str(item.get("title", ""))) != normalized(payload["title"])
                or "desc" not in item or normalized(str(item["desc"])) != normalized(payload["description"])
                or item.get("tid") != payload["tid"]):
            continue
        created = item.get("ctime")
        if not isinstance(created, (int, float)) or not start-300 <= created <= start+10800:
            continue
        result[item["bvid"]] = item
    return list(result.values())
