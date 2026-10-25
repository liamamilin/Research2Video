"""Provider contract tests; no network, browser, credentials, or real publishing."""
import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
from pathlib import Path
import stat
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.social.adapter import AdapterError
from core.social.adapters import xiaohongshu as xhs
from core.social.adapters.xiaohongshu import (
    CREATOR_URL, LOGIN_URL, PROFILE_URL, XiaohongshuAdapter, _profile,
)


UID = "6433a78b000000000e01d2bf"
NOTE = "64477a32000000000800db6b"
SUBMIT = "https://edith.xiaohongshu.com/web_api/sns/v2/note"
ADAPTER = XiaohongshuAdapter()


def account(**overrides):
    return {"code": 0, "success": True,
            "data": {"userId": UID, "userName": "测试作者", "redId": "editable-name", **overrides}}


def response(data, url=PROFILE_URL, status=200, method="GET"):
    return SimpleNamespace(status=status, url=url, json=AsyncMock(return_value=data),
                           request=SimpleNamespace(method=method))


def test_capabilities_describe_only_implemented_paths():
    assert ADAPTER.key == "xiaohongshu"
    assert ADAPTER.title_min == 1
    assert ADAPTER.title_limit == 20
    assert ADAPTER.description_limit == 1000
    assert ADAPTER.tag_limit == 10
    assert ADAPTER.combined_limit is None
    assert ADAPTER.supports_upload is True
    assert ADAPTER.supports_public_check is True
    assert "审核" in ADAPTER.public_check_gap


def test_stable_uid_is_not_editable_red_id_and_output_contains_no_extra_data():
    data = account(access_token="private-token", cookie="secret", avatar="secret-query")
    assert _profile(data) == {"uid": UID, "name": "测试作者"}
    assert "private-token" not in str(_profile(data))
    assert _profile(account(userName="", nickName=" 昵称 ")) == {"uid": UID, "name": "昵称"}


@pytest.mark.parametrize("data", [
    None, [], {}, {"code": 0, "success": True},
    {"code": False, "success": True, "data": account()["data"]},
    {"code": "0", "success": True, "data": account()["data"]},
    {"code": 0.0, "success": True, "data": account()["data"]},
    {"code": 0, "success": 1, "data": account()["data"]},
    {"code": -1, "success": False, "msg": "private-cookie"},
    account(userId=None), account(userId=123), account(userId="editable-name"),
    account(userId=""), account(userId=UID.upper()), account(guest=True),
    account(userName=" "), account(userName=12), account(userName="x" * 201),
])
def test_identity_rejects_guest_partial_malformed_or_error_profile(data):
    with pytest.raises(AdapterError) as error:
        _profile(data)
    assert "private-cookie" not in str(error.value)


def test_identity_checks_authenticated_creator_endpoint_directly():
    api = response(account())
    context = SimpleNamespace(request=SimpleNamespace(get=AsyncMock(return_value=api)),
                              new_page=AsyncMock())
    assert asyncio.run(ADAPTER.identity(context)) == {"uid": UID, "name": "测试作者"}
    context.new_page.assert_not_called()
    args, kwargs = context.request.get.call_args
    assert args == (PROFILE_URL,)
    assert kwargs["max_redirects"] == kwargs["max_retries"] == 0
    assert kwargs["headers"] == {"Referer": CREATOR_URL}


def fallback_context(direct, navigation, *, goto_error=None):
    page = SimpleNamespace(goto=AsyncMock(side_effect=goto_error), close=AsyncMock())

    @asynccontextmanager
    async def expect_response(predicate, timeout):
        assert timeout <= 15000
        if not predicate(navigation):
            raise TimeoutError("provider-private-token")
        pending = asyncio.get_running_loop().create_future()
        pending.set_result(navigation)
        yield SimpleNamespace(value=pending)

    page.expect_response = expect_response
    context = SimpleNamespace(request=SimpleNamespace(get=AsyncMock(return_value=direct)),
                              new_page=AsyncMock(return_value=page))
    return context, page


def test_identity_browser_fallback_reads_real_signed_page_profile_and_closes_page():
    context, page = fallback_context(response({"success": False}), response(account()))
    assert asyncio.run(ADAPTER.identity(context))["uid"] == UID
    page.goto.assert_awaited_once_with(CREATOR_URL, wait_until="domcontentloaded", timeout=15000)
    page.close.assert_awaited_once()


@pytest.mark.parametrize("navigation", [
    response(account(), url="https://evil.example/api/galaxy/user/info"),
    response(account(), url=PROFILE_URL + "/fake"),
    response(account(), status=403), response(account(), method="POST"),
    response(account(userId="")),
])
def test_identity_fallback_rejects_untrusted_response_or_partial_profile(navigation):
    context, page = fallback_context(response(account(), status=302), navigation)
    with pytest.raises(AdapterError) as error:
        asyncio.run(ADAPTER.identity(context))
    assert "provider-private-token" not in str(error.value)
    page.close.assert_awaited_once()


def test_identity_rejects_redirect_even_if_destination_returns_valid_shape():
    context, page = fallback_context(response(account(), url="https://evil.example"),
                                     response(account()), goto_error=RuntimeError("secret"))
    with pytest.raises(AdapterError) as error:
        asyncio.run(ADAPTER.identity(context))
    assert "secret" not in str(error.value)
    page.close.assert_awaited_once()


@pytest.mark.parametrize("url", [SUBMIT, SUBMIT + "?scene=web", SUBMIT.replace(".com/", ".com:443/")])
def test_only_exact_provider_final_submission_url_matches(url):
    assert ADAPTER.is_submit(url)


@pytest.mark.parametrize("url", [
    None, "", "http://edith.xiaohongshu.com/web_api/sns/v2/note",
    "https://evil.example/web_api/sns/v2/note",
    "https://edith.xiaohongshu.com.evil.example/web_api/sns/v2/note",
    "https://edith.xiaohongshu.com@evil.example/web_api/sns/v2/note",
    "https://secret@edith.xiaohongshu.com/web_api/sns/v2/note",
    "https://edith.xiaohongshu.com:444/web_api/sns/v2/note",
    "https://edith.xiaohongshu.com:bad/web_api/sns/v2/note",
    "https://creator.xiaohongshu.com/web_api/sns/v2/note",
    SUBMIT + "/", SUBMIT + "/delete", SUBMIT.replace("note", "%6eote"),
    "https://creator.xiaohongshu.com/api/media/v1/upload/web/permit",
    "https://creator.xiaohongshu.com/api/galaxy/user/info",
    "https://edith.xiaohongshu.com/api/sns/web/v1/comment/post",
])
def test_login_upload_and_lookalike_endpoints_are_not_final_publication(url):
    assert ADAPTER.is_submit(url) is False


def test_receipt_requires_explicit_acceptance_and_keeps_provider_data_private():
    receipt = ADAPTER.submission_receipt({"code": 0, "success": True,
                                          "data": {"id": NOTE, "xsec_token": "private"},
                                          "cookie": "secret"})
    assert receipt == {"accepted": True, "confirmed_not_submitted": False, "code": 0,
                       "work_id": NOTE, "url": f"https://www.xiaohongshu.com/explore/{NOTE}",
                       "public": False}


def test_explicit_platform_refusal_is_safe_to_retry_and_never_exposes_message():
    receipt = ADAPTER.submission_receipt({"code": -1, "success": False, "msg": "secret"})
    assert receipt == {"accepted": False, "confirmed_not_submitted": True, "code": -1}


@pytest.mark.parametrize('extra', [{}, {'code': 0}, {'success': True}, {'code': 0, 'success': True}])
def test_creator_result_envelope_is_accepted_without_requiring_profile_fields(extra):
    receipt = ADAPTER.submission_receipt({'result': 0, 'data': {'id': NOTE}, **extra})
    assert receipt['accepted'] and receipt['work_id'] == NOTE and not receipt['public']


def test_creator_explicit_refusal_without_note_id_is_not_success():
    assert ADAPTER.submission_receipt({'result': -100, 'msg': 'private-token'}) == {
        'accepted': False, 'confirmed_not_submitted': True, 'code': -100,
    }


@pytest.mark.parametrize("data", [
    None, [], {}, {"success": True, "data": {"id": NOTE}},
    {"code": False, "success": True, "data": {"id": NOTE}},
    {"code": "0", "success": True, "data": {"id": NOTE}},
    {"code": 0.0, "success": True, "data": {"id": NOTE}},
    {"code": 0, "success": 1, "data": {"id": NOTE}},
    {"code": 0, "success": True, "data": {"id": 123}},
    {"code": 0, "success": True, "data": {"id": NOTE.upper()}},
    {"code": 0, "success": True, "data": {"id": "fake"}},
    {"code": 0, "success": True, "data": {}},
    {"code": 0, "success": True, "data": {"note_id": NOTE}},
    {"code": 0, "success": False, "data": {"id": NOTE}},
    {"code": -1, "success": False, "data": {"id": NOTE}},
    {"code": -1, "success": True, "data": {"id": NOTE}},
    {"code": 0, "success": True, "result": -1, "data": {"id": NOTE}},
    {"code": 0, "success": True, "result": False, "data": {"id": NOTE}},
    {"code": -1, "msg": "secret"},
    {'result': 0, 'success': False, 'data': {'id': NOTE}},
    {'result': 0, 'code': -1, 'data': {'id': NOTE}},
    {'result': False, 'data': {'id': NOTE}},
    {'result': '0', 'data': {'id': NOTE}},
    {'result': 0.0, 'data': {'id': NOTE}},
    {'result': 0, 'data': {'id': 123}},
    {'result': 0, 'data': {'id': NOTE.upper()}},
    {'result': 0, 'data': {'note_id': NOTE}},
    {'result': 0, 'data': {}},
    {'result': -1, 'success': True},
    {'result': -1, 'code': 0},
    {'result': -1, 'data': {'id': NOTE}},
])
def test_partial_contradictory_or_unknown_receipt_is_never_retried(data):
    with pytest.raises(AdapterError) as error:
        ADAPTER.submission_receipt(data)
    assert "secret" not in str(error.value)


def locator():
    item = SimpleNamespace(count=AsyncMock(return_value=1), is_visible=AsyncMock(return_value=True),
                           wait_for=AsyncMock(), click=AsyncMock())
    item.first = item
    item.locator = MagicMock(return_value=item)
    item.get_by_text = MagicMock(return_value=item)
    return item


def test_prepare_login_opens_provider_and_switches_to_real_qr_mode():
    item = locator()
    item.count.return_value = 0
    page = SimpleNamespace(url=LOGIN_URL, goto=AsyncMock(), close=AsyncMock(), locator=MagicMock(return_value=item))
    context = SimpleNamespace(new_page=AsyncMock(return_value=page))
    assert asyncio.run(ADAPTER.prepare_login(context)) is page
    page.goto.assert_awaited_once_with(LOGIN_URL, wait_until="domcontentloaded", timeout=30000)
    item.click.assert_awaited_once()
    page.close.assert_not_called()


def test_prepare_login_handles_failure_without_credential_leak_or_page_leak():
    page = SimpleNamespace(goto=AsyncMock(side_effect=RuntimeError("secret-token")), close=AsyncMock())
    context = SimpleNamespace(new_page=AsyncMock(return_value=page))
    with pytest.raises(AdapterError) as error:
        asyncio.run(ADAPTER.prepare_login(context))
    assert "secret-token" not in str(error.value)
    page.close.assert_awaited_once()


def test_prepare_login_refuses_provider_redirect_to_an_unreviewed_site():
    page = SimpleNamespace(url="https://evil.example/login", goto=AsyncMock(), close=AsyncMock())
    context = SimpleNamespace(new_page=AsyncMock(return_value=page))
    with pytest.raises(AdapterError):
        asyncio.run(ADAPTER.prepare_login(context))
    page.close.assert_awaited_once()


def qr_page(size):
    item = locator()
    item.bounding_box = AsyncMock(return_value=size)

    async def screenshot(**options):
        Path(options["path"]).write_bytes(b"fake-qr-image")

    item.screenshot = AsyncMock(side_effect=screenshot)
    page = SimpleNamespace(url=LOGIN_URL, locator=MagicMock(return_value=item), screenshot=AsyncMock())
    return page, item


def test_capture_qr_only_captures_labelled_qr_element_with_private_file_mode(tmp_path):
    page, item = qr_page({"width": 150, "height": 150})
    destination = tmp_path / "qr" / "login.png"
    assert asyncio.run(ADAPTER.capture_qr(page, destination)) is True
    assert destination.read_bytes() == b"fake-qr-image"
    assert stat.S_IMODE(destination.stat().st_mode) == 0o600
    item.get_by_text.assert_called_once_with("APP扫一扫登录", exact=True)
    page.screenshot.assert_not_called()


@pytest.mark.parametrize("size", [None, {"width": 30, "height": 30},
                                  {"width": 1200, "height": 180},
                                  {"width": 180, "height": 1200}])
def test_capture_qr_refuses_avatar_or_full_page_dimensions(tmp_path, size):
    page, item = qr_page(size)
    destination = tmp_path / "qr.png"
    assert asyncio.run(ADAPTER.capture_qr(page, destination)) is False
    item.screenshot.assert_not_called()
    page.screenshot.assert_not_called()
    assert not destination.exists()


@pytest.mark.parametrize("url", ["https://evil.example/login", CREATOR_URL,
                                  "http://creator.xiaohongshu.com/login"])
def test_capture_qr_refuses_other_pages_even_if_they_contain_login_label(tmp_path, url):
    page, item = qr_page({"width": 150, "height": 150})
    page.url = url
    assert asyncio.run(ADAPTER.capture_qr(page, tmp_path / "qr.png")) is False
    item.screenshot.assert_not_called()


def mock_upstream(monkeypatch, *, page=None):
    uploader = ModuleType("uploader")
    package = ModuleType("uploader.xiaohongshu_uploader")
    module = ModuleType("uploader.xiaohongshu_uploader.main")
    calls, instances, guards = [], [], []

    class Uploader:
        def __init__(self, **options):
            calls.append(options)
            instances.append(self)
            for name, value in options.items():
                setattr(self, name, value)
            self.fill_meta = AsyncMock()
            self.set_thumbnail = AsyncMock()
            self.check_original_declaration = AsyncMock()

        async def upload(self, guarded):
            guards.append(guarded)
            if page is not None:
                await self.upload_video_content(page)

    module.XiaoHongShuVideo = Uploader
    for name, value in (("uploader", uploader), ("uploader.xiaohongshu_uploader", package),
                        ("uploader.xiaohongshu_uploader.main", module)):
        monkeypatch.setitem(sys.modules, name, value)
    return calls, instances, guards


def test_upload_uses_exact_injected_guard_and_pinned_class_without_own_browser(monkeypatch, tmp_path):
    calls, instances, guards = mock_upstream(monkeypatch)
    guarded = object()
    storage = tmp_path / "isolated-account.json"
    payload = {"title": "视频标题", "description": "正文", "tags": ["科技"],
               "video_path": "/validated/video.mp4", "cover": "/validated/cover.png"}
    asyncio.run(ADAPTER.upload(guarded, payload, storage))
    assert calls == [{"title": "视频标题", "file_path": "/validated/video.mp4", "desc": "正文", "tags": ["科技"],
                      "publish_date": 0, "account_file": str(storage), "thumbnail_path": "/validated/cover.png",
                      "publish_strategy": "immediate", "debug": False, "headless": False}]
    assert len(instances) == 1
    assert guards == [guarded]
    assert not storage.exists()


@pytest.mark.parametrize("failure", [None, TimeoutError("new mutation path; no success redirect")])
def test_final_publish_is_clicked_once_even_if_provider_mutation_path_changes(monkeypatch, tmp_path, failure):
    file_input = SimpleNamespace(set_input_files=AsyncMock())
    title_input = SimpleNamespace(wait_for=AsyncMock())
    publish_button = SimpleNamespace(click=AsyncMock())
    page = SimpleNamespace(
        goto=AsyncMock(), wait_for_url=AsyncMock(side_effect=failure),
        locator=MagicMock(side_effect=[file_input, title_input]),
        get_by_role=MagicMock(return_value=publish_button),
    )
    _, instances, _ = mock_upstream(monkeypatch, page=page)
    payload = {"title": "标题", "video_path": "/video.mp4"}
    if failure is None:
        asyncio.run(ADAPTER.upload(object(), payload, tmp_path / "account.json"))
    else:
        with pytest.raises(TimeoutError):
            asyncio.run(ADAPTER.upload(object(), payload, tmp_path / "account.json"))
    publish_button.click.assert_awaited_once()
    page.get_by_role.assert_called_once_with("button", name="发布", exact=True)
    file_input.set_input_files.assert_awaited_once_with("/video.mp4")
    instances[0].fill_meta.assert_awaited_once_with(page)
    instances[0].check_original_declaration.assert_not_called()
    page.wait_for_url.assert_awaited_once()


def test_upload_does_not_click_publish_if_editor_or_asset_has_not_become_ready(monkeypatch, tmp_path):
    file_input = SimpleNamespace(set_input_files=AsyncMock())
    title_input = SimpleNamespace(wait_for=AsyncMock(side_effect=TimeoutError("not ready")))
    page = SimpleNamespace(goto=AsyncMock(), locator=MagicMock(side_effect=[file_input, title_input]),
                           get_by_role=MagicMock())
    _, instances, _ = mock_upstream(monkeypatch, page=page)
    with pytest.raises(TimeoutError):
        asyncio.run(ADAPTER.upload(object(), {"title": "标题", "video_path": "/video.mp4"},
                                   tmp_path / "account.json"))
    page.get_by_role.assert_not_called()
    instances[0].fill_meta.assert_not_called()


@pytest.mark.parametrize("overrides", [
    {"title": "x" * 21}, {"title": ""}, {"title": 3},
    {"description": "x" * 1001}, {"description": []},
    {"tags": ["科技"] * 11}, {"tags": "科技"}, {"tags": [2]}, {"tags": [" "]},
])
def test_upload_refuses_invalid_package_before_upstream_import(overrides, tmp_path):
    payload = {"title": "标题", "description": "正文", "tags": ["科技"],
               "video_path": "/video.mp4", **overrides}
    with pytest.raises(AdapterError):
        ADAPTER.validate_payload(payload)
    with pytest.raises(AdapterError):
        asyncio.run(ADAPTER.upload(object(), payload, tmp_path / "account.json"))


@pytest.mark.parametrize("payload", [
    {"title": "一"},
    {"title": "一" * 20, "description": "文" * 1000, "tags": ["科技"] * 10},
    {"title": "正常标题", "description": "", "tags": []},
])
def test_validate_payload_accepts_boundaries_without_normalizing_or_mutating(payload):
    before = deepcopy(payload)
    assert ADAPTER.validate_payload(payload) is None
    assert payload == before


def test_validate_payload_does_not_import_uploader_or_access_paths(monkeypatch, tmp_path):
    import builtins

    original_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        if name == "uploader" or name.startswith("uploader."):
            pytest.fail("queue validation must not load the provider runtime")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    payload = {"title": "排队前校验", "description": "正文", "tags": ["科技"],
               "video_path": str(tmp_path / "not-created.mp4"),
               "cover": str(tmp_path / "not-created.png")}
    before = deepcopy(payload)
    assert ADAPTER.validate_payload(payload) is None
    assert payload == before
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("payload", [None, [], "标题", 3])
def test_validate_payload_rejects_invalid_structure_with_safe_provider_error(payload):
    with pytest.raises(AdapterError):
        ADAPTER.validate_payload(payload)


def test_unknown_review_enum_never_infers_publication():
    records = xhs.work_records({'code': 0, 'success': True, 'data': {'notes': [
        {'id': NOTE, 'tab_status': 99, 'permission_code': 0, 'high_self': 0, 'schedule_post_time': 0},
    ]}}, UID, NOTE)
    assert records[0]['state'] == 'unknown'


@pytest.mark.parametrize("owner,work", [("editable-red-id", NOTE), (UID, 123), (123, NOTE)])
def test_public_check_rejects_invalid_or_numeric_identifiers(owner, work):
    with pytest.raises(AdapterError):
        asyncio.run(ADAPTER.inspect_work(object(), Path("/unused.json"), owner, work))
