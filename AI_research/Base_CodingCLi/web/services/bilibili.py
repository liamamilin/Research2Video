"""Small Bilibili adapter: Web login/comments and an isolated biliup upload process.

API shapes were checked against bilibili-mcp, BiliCommentBot and biliup source.
No upstream automation scripts are executed. Never log credential payloads or subprocess output.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import platform
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from urllib.parse import urlencode

import httpx

BV = re.compile(r"BV[0-9A-Za-z]{10}")
API = "https://api.bilibili.com"
PASSPORT = "https://passport.bilibili.com"
MEMBER = "https://member.bilibili.com"
HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://www.bilibili.com/"}
COOKIE_NAMES = ("SESSDATA", "bili_jct", "DedeUserID", "buvid3", "DedeUserID__ckMd5")
# Public client signing constants used by biliup's BiliTV QR flow.
TV_APPKEY = "4409e2ce8ffd12b8"
TV_APPSEC = "59b43e04ad6965f34319062b478f83dd"


class BilibiliError(ValueError):
    pass


class BilibiliRejected(BilibiliError):
    """The API returned an explicit rejection, rather than an uncertain network outcome."""

    def __init__(self, message: str, *, code: int | None = None):
        super().__init__(message)
        self.code = code


class UploadFailed(BilibiliError):
    """A failure confirmed to occur before starting the upload command."""


def private_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=".bili-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False)
        os.replace(name, path)
        path.chmod(0o600)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def normalize_credentials(data: dict) -> dict:
    """Accept biliup or bilibili-mcp JSON; keep only known cookies."""
    aliases = {"sessdata": "SESSDATA", "bili_jct": "bili_jct", "dedeuserid": "DedeUserID", "buvid3": "buvid3"}
    if "cookie_info" in data:
        cookie_info = data["cookie_info"]
        if not isinstance(cookie_info, dict) or not isinstance(cookie_info.get("cookies"), list):
            raise BilibiliError("凭证的 cookie_info.cookies 需要是列表。")
        values = {c["name"]: str(c["value"]) for c in cookie_info["cookies"]
                  if isinstance(c, dict) and c.get("name") in COOKIE_NAMES and isinstance(c.get("value"), str)}
    else:
        values = {aliases.get(k, k): str(v) for k, v in data.items() if aliases.get(k, k) in COOKIE_NAMES}
    if not all(values.get(k) for k in ("SESSDATA", "bili_jct", "DedeUserID")) or not values["DedeUserID"].isdigit():
        raise BilibiliError("凭证需要 SESSDATA、bili_jct 和数字格式的 DedeUserID。")
    if any(";" in value or any(ord(c) < 33 or ord(c) > 126 for c in value) for value in values.values()):
        raise BilibiliError("凭证格式不正确。")
    token = data.get("token_info") or {}
    native = data.get("platform") in ("BiliTV", "Android", None) and isinstance(token, dict) and bool(token.get("access_token"))
    if native:
        if str(token.get("mid")) != values["DedeUserID"]:
            raise BilibiliError("上传授权与 Cookie 的账号 ID 不一致，请重新扫码连接。")
        if not isinstance(token.get("access_token"), str) or not isinstance(token.get("refresh_token"), str):
            raise BilibiliError("上传授权格式不正确，请重新扫码连接。")
        token = {"access_token": token["access_token"], "refresh_token": token["refresh_token"],
                 "expires_in": int(token.get("expires_in") or 0), "mid": int(values["DedeUserID"])}
    else:
        token = {"access_token": "", "refresh_token": "", "expires_in": 0, "mid": int(values["DedeUserID"])}
    return {"cookie_info": {"cookies": [{"name": k, "value": v} for k, v in values.items()]},
            "token_info": token, "sso": [], "platform": data.get("platform") if native else None}


def upload_authorized(credentials: dict | None) -> bool:
    """A Web cookie alone cannot pass biliup's OAuth validation."""
    return bool(credentials and credentials.get("platform") in ("BiliTV", "Android", None)
                and (credentials.get("token_info") or {}).get("access_token"))


class BilibiliClient:
    def __init__(self, credentials: dict | None = None):
        self.cookies = {c["name"]: c["value"] for c in (credentials or {}).get("cookie_info", {}).get("cookies", [])}

    def request(self, path: str, *, passport: bool = False, member: bool = False,
                data: dict | None = None, allowed_codes: tuple[int, ...] = (), timeout: float = 20, **params) -> tuple[dict, httpx.Cookies]:
        try:
            host = MEMBER if member else PASSPORT if passport else API
            headers = {**HEADERS, "Referer": MEMBER + "/"} if member else HEADERS
            with httpx.Client(headers=headers, cookies=self.cookies, timeout=timeout, follow_redirects=False) as client:
                response = client.request("POST" if data is not None else "GET", host + path,
                                          params=params, data=data)
                response.raise_for_status()
                result = response.json()
                if result.get("code") != 0 and result.get("code") not in allowed_codes:
                    code = result.get("code")
                    raise BilibiliRejected(f"B 站接口返回 {code}，请检查登录状态或稍后重试。", code=code)
                if result.get("code") in allowed_codes:
                    return {"pending_code": result["code"]}, response.cookies
                return result.get("data") or {}, response.cookies
        except (httpx.HTTPError, json.JSONDecodeError) as exc:
            error = BilibiliError("B 站连接失败，操作结果可能尚未确认。")
            error.http_status = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
            raise error from exc

    def verify(self) -> dict:
        data, _ = self.request("/x/web-interface/nav")
        if not data.get("isLogin"):
            raise BilibiliRejected("B 站登录已失效，请重新扫码。", code=-101)
        return {"uid": str(data["mid"]), "name": data.get("uname", ""), "verified_at": time.time()}

    def categories(self) -> list[dict]:
        """Read selectable submission categories from the creator's own catalog."""
        data, _ = self.request("/x/vupre/web/archive/pre", member=True)
        result = []
        seen = set()

        def visit(rows, parents=(), depth=0):
            if not isinstance(rows, list) or depth > 6:
                return
            for row in rows:
                if not isinstance(row, dict) or row.get("show") is False:
                    continue
                name = str(row.get("name") or "").strip()
                if not name:
                    continue
                if row.get("children"):
                    visit(row["children"], (*parents, name), depth + 1)
                    continue
                try:
                    tid = int(row.get("id", 0))
                except (TypeError, ValueError):
                    continue
                if 0 < tid <= 65535 and tid not in seen:
                    seen.add(tid)
                    result.append({"tid": tid, "name": name, "path": " / ".join((*parents, name)),
                                   "description": str(row.get("desc") or row.get("description") or "")})

        visit(data.get("typelist"))
        if not result:
            raise BilibiliError("B 站暂未返回可投稿分区，请重新获取分区或检查登录状态。")
        return result

    def archives(self, max_pages: int = 3, *, keyword: str = "") -> dict:
        """Read bounded creator pages, including pending and rejected submissions."""
        if not 1 <= max_pages <= 10:
            raise BilibiliError("稿件查询页数应在 1 到 10 之间。")
        rows, total = [], None
        for page in range(1, max_pages+1):
            data, _ = self.request("/x/web/archives", member=True, status="is_pubing,pubed,not_pubed",
                                   pn=page, ps=20, timeout=8, **({"keyword": keyword} if keyword else {}))
            if not isinstance(data, dict):
                raise BilibiliError("B 站稿件列表格式异常，本次不改变投稿状态。")
            metadata, entries = data.get("page"), data.get("arc_audits")
            if isinstance(metadata, dict) and metadata.get("count") == 0 and entries is None:
                entries = []  # Bilibili returns null, rather than [], for an empty keyword search.
            if (not isinstance(metadata, dict) or not isinstance(entries, list)
                    or not isinstance(metadata.get("count"), int) or not isinstance(metadata.get("ps"), int)
                    or metadata["count"] < 0 or metadata["ps"] < 1):
                raise BilibiliError("B 站稿件列表格式异常，本次不改变投稿状态。")
            if total is not None and total != metadata["count"]:
                raise BilibiliError("B 站稿件列表正在变化，稍后重新核对。")
            total = metadata["count"]
            for entry in entries:
                if not isinstance(entry, dict) or not isinstance(entry.get("Archive"), dict):
                    raise BilibiliError("B 站稿件条目格式异常，本次不改变投稿状态。")
                archive = entry["Archive"]
                if archive.get("mid") and str(archive["mid"]) != self.cookies.get("DedeUserID"):
                    raise BilibiliError("稿件列表账号不一致，本次不改变投稿状态。")
                rows.append({k: archive[k] for k in ("bvid", "title", "desc", "tid", "ctime", "state", "state_desc") if k in archive})
            if page*metadata["ps"] >= total:
                break
        return {"archives": rows, "total": total, "pages": page, "complete": page*metadata["ps"] >= total}

    def owned_video(self, bvid: str, uid: str) -> dict:
        if not BV.fullmatch(bvid):
            raise BilibiliError("请输入完整的 BV 号。")
        data, _ = self.request("/x/web-interface/view", bvid=bvid)
        if str((data.get("owner") or {}).get("mid")) != uid:
            raise BilibiliError("只能管理当前登录账号发布的视频。")
        return data

    def comments(self, info: dict, page: int = 1) -> tuple[list[dict], bool]:
        data, _ = self.request("/x/v2/reply", oid=info["aid"], type=1, pn=page, ps=20, sort=0)
        roots = data.get("replies") or []
        rows = []
        for root in roots:
            rows.append(root)
            for child in root.get("replies") or []:
                rows.append({**child, "root": root["rpid"]})
        count = int((data.get("page") or {}).get("count") or 0)
        return rows, page * 20 < count

    def thread(self, info: dict, root: str, page: int) -> tuple[list[dict], bool]:
        data, _ = self.request("/x/v2/reply/reply", oid=info["aid"], type=1, root=root, pn=page, ps=20)
        rows = [{**r, "root": root} for r in data.get("replies") or []]
        return rows, page * 20 < int((data.get("page") or {}).get("count") or 0)

    def reply(self, info: dict, row: dict, text: str) -> str:
        result, _ = self.request("/x/v2/reply/add", data={"oid": info["aid"], "type": 1, "root": row["root"],
            "parent": row["rpid"], "message": text, "csrf": self.cookies.get("bili_jct", "")})
        rpid = str(result.get("rpid") or (result.get("reply") or {}).get("rpid") or "")
        if not rpid.isdigit():
            raise BilibiliError("B 站没有返回回复 ID，请到原评论核对发送结果。")
        return rpid

    def post_comment(self, info: dict, text: str) -> str:
        """Post a top-level comment; do not confuse it with replying to a viewer."""
        result, _ = self.request("/x/v2/reply/add", data={"oid": info["aid"], "type": 1,
            "message": text, "plat": 1, "csrf": self.cookies.get("bili_jct", "")})
        rpid = str(result.get("rpid") or (result.get("reply") or {}).get("rpid") or "")
        if not rpid.isdigit() or int(rpid) <= 0:
            raise BilibiliError("B 站未返回评论 ID，请到视频下核对，避免重复发送。")
        return rpid

    def pin_comment(self, info: dict, rpid: str) -> None:
        if not rpid.isdigit() or int(rpid) <= 0:
            raise BilibiliError("没有可置顶的评论 ID。")
        self.request("/x/v2/reply/top", data={"oid": info["aid"], "type": 1, "rpid": rpid,
                     "action": 1, "csrf": self.cookies.get("bili_jct", "")})


def tv_form(**fields) -> dict:
    """Sign the same QR authorization requests as the pinned biliup client."""
    form = dict(sorted({"appkey": TV_APPKEY, "local_id": "0", "ts": int(time.time()), **fields}.items()))
    form["sign"] = hashlib.md5((urlencode(form) + TV_APPSEC).encode()).hexdigest()
    return form


def start_login(path: Path) -> dict:
    try:
        import qrcode
    except ImportError as exc:
        raise BilibiliError("扫码组件尚未安装，请安装项目 requirements.txt 中的 qrcode。") from exc
    data, _ = BilibiliClient().request("/x/passport-tv-login/qrcode/auth_code", passport=True, data=tv_form())
    image = qrcode.make(data["url"])
    out = io.BytesIO()
    image.save(out, format="PNG")
    private_json(path, {"key": data["auth_code"], "created_at": time.time(), "flow": "BiliTV"})
    return {"status": "waiting", "image": "data:image/png;base64," + base64.b64encode(out.getvalue()).decode()}


def poll_login(path: Path) -> dict:
    if not path.exists():
        raise BilibiliError("没有进行中的扫码登录。")
    session = json.loads(path.read_text())
    if time.time() - session["created_at"] > 180 or session.get("flow") != "BiliTV":
        return {"status": "expired"}
    data, _ = BilibiliClient().request("/x/passport-tv-login/qrcode/poll", passport=True,
                                     data=tv_form(auth_code=session["key"]), allowed_codes=(86039, 86038))
    if data.get("pending_code"):
        return {"status": "waiting" if data["pending_code"] == 86039 else "expired"}
    credentials = normalize_credentials({**data, "platform": "BiliTV"})
    if not upload_authorized(credentials):
        raise BilibiliError("扫码未返回上传授权，请重新扫码连接。")
    return {"status": "done", "credentials": credentials}


def biliup_binary() -> str | None:
    configured = os.environ.get("AREC_BILIUP_BIN")
    if configured:
        return shutil.which(configured)
    installed = Path(__file__).resolve().parents[2] / "state/platforms/runtime/bin/biliup"
    return str(installed) if installed.is_file() and os.access(installed, os.X_OK) else shutil.which("biliup")


def ffmpeg_binary() -> str | None:
    configured = os.environ.get("AREC_FFMPEG_BIN")
    if configured:
        return shutil.which(configured)
    binary = shutil.which("ffmpeg")
    if binary:
        return binary
    bundled = Path(__file__).resolve().parents[4] / "autovideo/g0/darwin_arm64/ffmpeg"
    if platform.system() == "Darwin" and platform.machine() == "arm64" and bundled.is_file():
        return str(bundled)
    return None


def prepare_cover(video_path: Path, work_dir: Path, provided: str = "") -> Path:
    if provided and Path(provided).is_file():
        return Path(provided)
    cover = work_dir / "cover.jpg"
    if cover.is_file() and cover.stat().st_size:
        return cover
    ffmpeg = ffmpeg_binary()
    if not ffmpeg:
        raise BilibiliError("成片没有封面，需要配置 ffmpeg 才能自动截取投稿封面。")
    for second in (3, 0):
        try:
            result = subprocess.run([ffmpeg, "-nostdin", "-v", "error", "-y", "-ss", str(second), "-i", str(video_path),
                "-frames:v", "1", "-vf", "scale=1280:720:force_original_aspect_ratio=decrease,pad=1280:720:(ow-iw)/2:(oh-ih)/2",
                "-q:v", "3", str(cover)], capture_output=True, timeout=30, check=False)
        except (OSError, subprocess.SubprocessError) as exc:
            raise BilibiliError("投稿封面截取失败，请检查成片文件与 ffmpeg 配置。") from exc
        if result.returncode == 0 and cover.is_file() and cover.stat().st_size:
            return cover
    raise BilibiliError("未能从成片读取投稿封面。")


def cli_diagnostic(output: str) -> str:
    """Extract allowlisted reasons only; never retain arbitrary CLI output or secrets."""
    if "未知平台" in output:
        return "未知平台：登录凭证格式与 biliup 不兼容，请重新扫码连接。"
    if "open cookies file:" in output and "No such file or directory" in output:
        return "biliup 找不到登录凭证文件，请检查上传组件的文件路径。"
    match = re.search(r"credential (?:validation|refresh) failed with Bilibili code (-?\d{1,10})\b", output)
    if match:
        return f"上传授权验证失败，B 站错误码 {match[1]}；请重新扫码连接。"
    codes = re.findall(r'ResponseData\s*\{[^\n]*?code:\s*(-?\d{1,10})\b', output)
    if codes and codes[-1] != "0":
        return f"B 站投稿接口返回错误码 {codes[-1]}。"
    if "error decoding response body" in output:
        return "B 站响应格式异常。"
    if any(message in output.lower() for message in ("error sending request", "connection refused", "connection reset", "connection closed",
                                                     "dns error", "timed out", "timedout", "broken pipe", "unexpected eof", "incompletemessage")):
        return "连接 B 站失败或超时。"
    return "biliup 执行失败，未返回可识别的错误码。"


def upload_diagnostic(work_dir: Path, data: dict) -> None:
    """Keep sanitized attempt evidence as well as the latest outcome."""
    latest = work_dir / "diagnostic.json"
    # Preserve the previous outcome, including records made before history existed.
    if latest.exists():
        private_json(work_dir / f"diagnostic-{time.time_ns()}.json", json.loads(latest.read_text()))
    private_json(latest, {"recorded_at": time.time(), **data})


def upload(credentials: dict, video_path: Path, payload: dict, work_dir: Path) -> str:
    """Validate native authorization before upload; preserve uncertain submit outcomes."""
    binary = biliup_binary()
    if binary is None:
        raise UploadFailed("未找到 biliup，请安装原生 CLI 并将它加入 PATH。")
    # The process changes cwd; all file arguments must remain valid from there.
    binary = str(Path(binary).resolve())
    work_dir = work_dir.resolve()
    work_dir.mkdir(parents=True, exist_ok=True)
    work_dir.chmod(0o700)
    if not upload_authorized(credentials):
        reason = "当前账号只有网页登录 Cookie，缺少上传授权；请到「账号与策略」重新扫码连接。"
        upload_diagnostic(work_dir, {"phase": "authorization", "reason": reason, "confirmed_not_submitted": True})
        raise UploadFailed(reason)
    cover = prepare_cover(video_path, work_dir, payload.get("cover", "")).resolve()
    with tempfile.TemporaryDirectory(prefix="biliup-", dir=work_dir) as folder:
        cookie_path = Path(folder) / "cookies.json"
        private_json(cookie_path, normalize_credentials(credentials))
        options = dict(cwd=work_dir, capture_output=True, stdin=subprocess.DEVNULL, check=False,
                       env={**os.environ, "RUST_LOG": "info", "NO_COLOR": "1"})
        # The same native login/refresh path as upload, with no remote write operations.
        try:
            checked = subprocess.run([binary, "-u", str(cookie_path), "list", "--from-page", "1", "--max-pages", "1"], timeout=45, **options)
        except (OSError, subprocess.SubprocessError) as exc:
            reason = "上传前登录检查未完成，尚未开始上传，请检查 biliup 和网络连接。"
            upload_diagnostic(work_dir, {"phase": "preflight", "reason": reason, "confirmed_not_submitted": True})
            raise UploadFailed(reason) from exc
        try:
            refreshed = normalize_credentials(json.loads(cookie_path.read_text()))
            if refreshed["token_info"]["mid"] != credentials["token_info"]["mid"]:
                raise ValueError("owner changed")
        except (OSError, ValueError, KeyError, TypeError) as exc:
            reason = "上传前凭证检查失败，请重新扫码连接；尚未开始上传。"
            upload_diagnostic(work_dir, {"phase": "preflight", "reason": reason, "confirmed_not_submitted": True})
            raise UploadFailed(reason) from exc
        # Persist a successful token refresh even if the subsequent archive query failed.
        credentials.clear()
        credentials.update(refreshed)
        if checked.returncode:
            reason = cli_diagnostic((checked.stdout + checked.stderr).decode("utf-8", errors="replace"))
            upload_diagnostic(work_dir, {"phase": "preflight", "exit_code": checked.returncode,
                                                       "reason": reason, "confirmed_not_submitted": True})
            raise UploadFailed("上传前检查失败：" + reason)
        args = [binary, "-u", str(cookie_path), "upload", "--submit", "web", "--copyright", "1",
                "--tid", str(payload["tid"]), "--title", payload["title"], "--desc", payload["description"],
                "--tag", ",".join(payload["tags"])]
        args += ["--cover", str(cover)]
        args += ["--", str(video_path.resolve())]
        from web.services.bilibili_recovery import failure_evidence

        for attempt in range(1, 4):
            started = time.monotonic()
            upload_diagnostic(work_dir, {"phase": "upload", "attempt": attempt, "started_at": time.time(),
                                         "confirmed_not_submitted": False})
            try:
                result = subprocess.run(args, timeout=7200, **options)
            except OSError as exc:
                upload_diagnostic(work_dir, {"phase": "launch", "attempt": attempt,
                                             "reason": "上传进程未能启动。", "confirmed_not_submitted": True})
                raise UploadFailed("上传进程未能启动，可以重新检查并排队。") from exc
            except subprocess.SubprocessError as exc:
                upload_diagnostic(work_dir, {"phase": "upload", "attempt": attempt,
                                             "reason": "投稿进程中断。", "confirmed_not_submitted": False})
                raise BilibiliError("投稿进程中断，系统将自动核对 B 站稿件；结果确认前不会重投。") from exc
            output = (result.stdout + result.stderr).decode("utf-8", errors="replace")
            # A positive platform receipt remains valid even if the process exits abnormally afterwards.
            response = re.findall(r'ResponseData\s*\{[^\n]*code:\s*0\s*,[^\n]*', output)
            bvids = re.findall(r'bvid["\s:]+(?:String\(")?(BV[0-9A-Za-z]{10})', response[-1]) if response else []
            evidence = {**failure_evidence(output), "attempt": attempt, "exit_code": result.returncode,
                        "elapsed_seconds": round(time.monotonic()-started, 2)}
            if bvids:
                upload_diagnostic(work_dir, {**evidence, "phase": "submitted", "bvid": bvids[-1],
                                             "confirmed_not_submitted": False, "retryable": False})
                return bvids[-1]
            reason = cli_diagnostic(output) if result.returncode else "biliup 未返回可确认的投稿 BV 号。"
            upload_diagnostic(work_dir, {**evidence, "reason": reason})
            if evidence["retryable"] and attempt < 3:
                time.sleep(2**attempt)
                continue
            if evidence["confirmed_not_submitted"]:
                raise UploadFailed(reason + f" 尚未提交稿件，已尝试 {attempt} 次，可重新排队。")
            raise BilibiliError(reason + " 投稿结果尚未确认，系统将自动查稿；结果确认前不会重投。")
