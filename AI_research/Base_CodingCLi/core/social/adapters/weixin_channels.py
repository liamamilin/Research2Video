"""WeChat Channels adapter for the worker's guarded browser.

Identity and final mutation paths were checked against AiToEarn at
cb894a43b2d23cc1ad67d9e7729b1d86cf9360bd (shipinhao/index.ts and
wechat/wechat.service.ts). The uploader is the pinned social-auto-upload
TencentVideo. Neither source verifies an ID in the final post_create response:
they match later list entries. A successful response without an exact ID must
therefore remain UNKNOWN in the parent worker; it must never invite resubmission.
"""
from __future__ import annotations

import asyncio
import importlib
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from core.social.adapter import AdapterError


HOST = "channels.weixin.qq.com"
BASE = f"https://{HOST}"
AUTH_PATH = "/cgi-bin/mmfinderassistant-bin/auth/auth_data"
SUBMIT_PATHS = frozenset({
    "/cgi-bin/mmfinderassistant-bin/post/post_create",
    "/micro/content/cgi-bin/mmfinderassistant-bin/post/post_create",
})
# This is a documented validation rejection (missing horizontal cover data),
# not a transport, timeout or generic internal error.
VALIDATION_REJECTIONS = frozenset({300002})
IDENTITY_TIMEOUT = 12.0


def _provider_url(url: str, paths: frozenset[str] | set[str]) -> bool:
    try:
        parsed = urlsplit(url)
        return (
            parsed.scheme == "https"
            and parsed.hostname == HOST
            and parsed.port in (None, 443)
            and parsed.username is None
            and parsed.password is None
            and not parsed.fragment
            and parsed.path in paths
        )
    except (TypeError, ValueError):
        return False


def _parse_identity(data: Any) -> dict:
    if not isinstance(data, dict) or type(data.get("errCode")) is not int or data["errCode"] != 0:
        raise AdapterError("未取得微信视频号的已验证账号资料，请完成扫码登录。")
    details = data.get("data")
    finder = details.get("finderUser") if isinstance(details, dict) else None
    if not isinstance(finder, dict):
        raise AdapterError("微信视频号账号资料缺少当前视频号身份。")
    uid, name = finder.get("finderUsername"), finder.get("nickname")
    if (
        not isinstance(uid, str)
        or not re.fullmatch(r"[A-Za-z0-9_@.+=-]{1,512}", uid)
        or not isinstance(name, str)
        or not name.strip()
        or len(name) > 200
        or any(ord(char) < 32 for char in name)
    ):
        raise AdapterError("微信视频号账号资料缺少稳定账号 ID 或昵称。")
    # userAttr describes the WeChat administrator, not the publishing channel.
    return {"uid": uid, "name": name.strip()}


async def _submit_once(page: Any) -> None:
    """A single click with a response wait, never a force/evaluate retry."""
    button = page.get_by_role("button", name="发表", exact=True)
    if await button.count() != 1 or not await button.is_visible():
        raise AdapterError("未找到唯一可见的视频号发表按钮，已停止提交。")
    if await button.is_disabled():
        raise AdapterError("视频号发表按钮尚不可用，已停止提交。")
    # The injected parent guard enforces the persistent single-POST budget and
    # captures the final receipt. URL changes and button disappearance are not
    # evidence of acceptance. No fallback click is allowed on timeout.
    async with page.expect_response(
        lambda response: _provider_url(response.url, SUBMIT_PATHS)
        and response.request.method == "POST",
        timeout=30000,
    ) as pending:
        await button.click(timeout=10000)
    response = await pending.value
    await response.finished()


class WeixinChannelsAdapter:
    """Source-verified login and single-attempt video publishing integration."""

    key = "weixin_channels"
    label = "微信视频号"
    creator_url = f"{BASE}/platform"
    login_url = BASE
    title_min = 6
    title_limit = 16
    description_limit = 1000
    combined_limit = 1000
    tag_limit = 10  # Local product limit; included in the 1000-character description.
    supports_upload = True
    supports_public_check = False
    upload_note = "可提交一次；平台回执若未含可靠作品 ID，将待核对并阻止重复投稿。"
    public_check_note = "尚未接入能同时确认作品 ID、所属账号和审核公开状态的只读接口。"
    public_check_gap = public_check_note

    async def identity(self, context: Any) -> dict:
        """Observe a fresh platform auth_data response, never infer from cookies."""
        loop = asyncio.get_running_loop()
        verified = loop.create_future()
        tasks: set[asyncio.Task] = set()
        page = await context.new_page()

        async def inspect(response: Any) -> None:
            if verified.done() or not _provider_url(response.url, {AUTH_PATH}) or response.status != 200:
                return
            try:
                profile = _parse_identity(await response.json())
            except Exception:
                return
            if not verified.done():
                verified.set_result(profile)

        def observe(response: Any) -> None:
            task = asyncio.create_task(inspect(response))
            tasks.add(task)
            task.add_done_callback(tasks.discard)

        page.on("response", observe)
        try:
            await page.goto(self.creator_url, wait_until="domcontentloaded", timeout=30000)
            return await asyncio.wait_for(verified, timeout=IDENTITY_TIMEOUT)
        except Exception:
            raise AdapterError("未核实微信视频号账号，请完成扫码确认后重新检查连接。") from None
        finally:
            page.remove_listener("response", observe)
            for task in list(tasks):
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            await page.close()

    async def prepare_login(self, context: Any) -> Any:
        """Open the official login; the worker polls identity after human consent."""
        page = await context.new_page()
        try:
            await page.goto(self.login_url, wait_until="domcontentloaded", timeout=30000)
        except Exception:
            await page.close()
            raise AdapterError("微信视频号扫码页打开失败，请重新连接。") from None
        return page

    async def capture_qr(self, page: Any, destination: Path) -> bool:
        """Capture only a visible QR in an official login frame or login page."""
        candidates = []
        for frame in page.frames:
            try:
                parsed = urlsplit(frame.url)
                trusted = parsed.scheme == "https" and parsed.username is None and parsed.password is None
                wechat_login = parsed.hostname == "open.weixin.qq.com" and parsed.path == "/connect/qrconnect"
                channels_login = parsed.hostname == HOST and parsed.path in (
                    "/platform/login-for-iframe", "/platform/login.html", "/login.html",
                )
                if trusted and parsed.port in (None, 443) and (wechat_login or channels_login):
                    candidates.append(frame.locator("img.qrcode"))
            except (TypeError, ValueError):
                continue
        # The login QR can also be mounted directly on the official root page.
        if _provider_url(page.url, {"", "/", "/platform/login.html", "/login.html"}):
            candidates.append(page.locator("div.login-qrcode-wrap img.qrcode, div.qrcode-wrap img.qrcode"))
        for qr in candidates:
            try:
                if await qr.count() != 1 or not await qr.is_visible():
                    continue
                destination.parent.mkdir(parents=True, exist_ok=True)
                await qr.screenshot(path=str(destination), timeout=5000)
                destination.chmod(0o600)
                return True
            except Exception:
                continue
        return False

    def is_submit(self, url: str) -> bool:
        """Match only the official final post_create mutation, not file upload."""
        return _provider_url(url, SUBMIT_PATHS)

    def submission_receipt(self, data: Any) -> dict:
        """Keep successful ID-less receipts uncertain; never select a recent work."""
        if not isinstance(data, dict) or type(data.get("errCode")) is not int:
            raise AdapterError("视频号投稿回执格式未知，请核对创作中心，勿重复提交。")
        code = data["errCode"]
        inner = data.get("data")
        base = inner.get("baseResp") if isinstance(inner, dict) else None
        if code == 0 and isinstance(base, dict) and type(base.get("errcode")) is int:
            code = base["errcode"]
        if code in VALIDATION_REJECTIONS:
            return {"accepted": False, "confirmed_not_submitted": True, "code": str(code)}
        if code != 0:
            raise AdapterError(f"视频号返回投稿错误码 {code}，结果待核对，请勿重复提交。")
        raise AdapterError("视频号已返回投稿响应，但缺少可靠作品 ID；请核对创作中心，勿重复提交。")

    def validate_payload(self, payload: dict) -> None:
        """Validate the reviewed package before it can enter a publishing queue."""
        title, description, tags = payload.get("title"), payload.get("description", ""), payload.get("tags", [])
        if not isinstance(title, str) or not self.title_min <= len(title.strip()) <= self.title_limit:
            raise AdapterError("微信视频号短标题需要 6–16 个字，请先编辑标题。")
        if not isinstance(description, str) or not isinstance(tags, list) or len(tags) > self.tag_limit:
            raise AdapterError("微信视频号简介或标签格式不正确。")
        if any(not isinstance(tag, str) or not tag.strip() for tag in tags):
            raise AdapterError("微信视频号标签不能为空。")
        # TencentVideo writes title + Enter, each #tag + Space, then Enter +
        # description. Include both line breaks and hashtag separators.
        from core.social.publish_text import combined_text
        combined = combined_text("weixin_channels", title, description, tags)
        if len(combined) > self.combined_limit:
            raise AdapterError("微信视频号标题、简介和标签合计不能超过 1000 字。")

    async def upload(self, playwright: Any, payload: dict, storage: Path) -> None:
        """Use only the injected guard; isolate upstream overrides to one instance."""
        self.validate_payload(payload)
        title, description, tags = payload["title"], payload.get("description", ""), payload.get("tags", [])
        try:
            upstream = importlib.import_module("uploader.tencent_uploader.main")
        except ImportError:
            raise AdapterError("微信视频号投稿组件未安装，请先安装平台运行组件。") from None

        class SingleAttemptVideo(upstream.TencentVideo):
            async def validate_base_args(self) -> None:
                # Worker verifies the actual channel before supplying its guarded
                # browser. Upstream cookie_auth launches an unguarded browser.
                if not Path(self.account_file).is_file():
                    raise AdapterError("微信视频号账号连接已失效，请重新连接。")
                if self.publish_date != 0 or self.publish_strategy != "immediate" or self.is_draft:
                    raise AdapterError("当前仅支持微信视频号立即发表一次。")

            async def submit_publish(self, page: Any) -> None:
                await _submit_once(page)

            async def set_short_title(self, page: Any, title: str, short_title: str | None = None) -> None:
                # Preserve the reviewed short title. Upstream silently truncates
                # and pads it, which would change the user's publishing package.
                field = page.locator('input[placeholder="填写短标题有机会获得更多流量"]')
                if await field.count() != 1:
                    raise AdapterError("未找到唯一的视频号短标题输入框，已停止投稿。")
                await field.fill(short_title or title)

        try:
            app = SingleAttemptVideo(
                title=title.strip(), file_path=payload.get("video_path", ""), tags=tags,
                publish_date=0, account_file=str(storage), desc=description,
                thumbnail_path=payload.get("cover") or None, short_title=title.strip(),
                publish_strategy="immediate", debug=False, headless=True, is_draft=False,
            )
            await app.upload(playwright)
        except AdapterError:
            raise
        except Exception:
            raise AdapterError("微信视频号投稿流程未完成，请查看回执状态后处理，勿重复提交。") from None

    async def inspect_work(self, playwright: Any, storage: Path, owner_id: str, work_id: str) -> dict:
        """No source-verified public proof yet; do not imply ownership or publish."""
        if not isinstance(work_id, str) or not work_id.strip():
            raise AdapterError("微信视频号作品 ID 不能为空。")
        return {"work_id": work_id, "uid": owner_id, "owned": False, "public": False, "check_reason": self.public_check_note}
