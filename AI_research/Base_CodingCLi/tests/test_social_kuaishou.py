"""Kuaishou adapter contracts: mocked providers, isolated state, no real posting."""
import asyncio
import hashlib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from core.social.adapter import AdapterError
from core.social.adapters import kuaishou as ks


OWNER = "90071992547409931234"
WORK = "3xwn3kkerxj6g9n"
PROFILE = {"data": {"userInfo": {"userId": int(OWNER), "id": "opaque", "name": "测试作者", "token": "never-return"}}}


def response(data, *, status=200, url=""):
    return SimpleNamespace(status=status, url=url, json=AsyncMock(return_value=data))


def test_exact_account_id_remains_a_string_and_filters_provider_fields():
    assert ks._profile(PROFILE) == {"uid": OWNER, "name": "测试作者"}
    assert ks._profile({"data": {"userInfo": {"userId": OWNER, "name": " 作者 "}}}) == {"uid": OWNER, "name": "作者"}


@pytest.mark.parametrize("value", [None, [], {}, {"data": []}, {"data": {"userInfo": {"id": "opaque", "name": "作者"}}},
                                      {"errors": [{"message": "credential secret"}], **PROFILE},
                                      {"data": {"userInfo": {"userId": True, "name": "作者"}}},
                                      {"data": {"userInfo": {"userId": float(OWNER), "name": "作者"}}},
                                      {"data": {"userInfo": {"userId": OWNER, "name": ""}}}])
def test_cookie_or_partial_identity_is_not_login_proof(value):
    with pytest.raises(AdapterError) as failure:
        ks._profile(value)
    assert "secret" not in str(failure.value)


def test_identity_uses_one_bounded_read_query_no_page_or_cookie_inspection():
    context = SimpleNamespace(request=SimpleNamespace(post=AsyncMock(return_value=response(PROFILE))))
    assert asyncio.run(ks.KuaishouAdapter().identity(context)) == {"uid": OWNER, "name": "测试作者"}
    args, kwargs = context.request.post.call_args
    assert args == ("https://www.kuaishou.com/graphql",)
    assert kwargs["data"]["operationName"] == "userInfoQuery"
    assert "mutation" not in kwargs["data"]["query"]
    assert kwargs["timeout"] == 20000 and kwargs["max_retries"] == kwargs["max_redirects"] == 0
    assert context.request.post.call_count == 1


def test_identity_failure_does_not_leak_provider_exceptions_or_redirect():
    for mock in (AsyncMock(side_effect=RuntimeError("https://private/?token=secret")),
                 AsyncMock(return_value=response({}, status=302))):
        context = SimpleNamespace(request=SimpleNamespace(post=mock))
        with pytest.raises(AdapterError) as failure:
            asyncio.run(ks.KuaishouAdapter().identity(context))
        assert "secret" not in str(failure.value)


@pytest.mark.parametrize('data', [None, {}, {'result': True, 'data': {'userId': OWNER, 'userName': '作者'}},
    {'result': 1, 'data': {'userId': float(OWNER), 'userName': '作者'}},
    {'result': 1, 'data': {'userId': OWNER, 'userName': ''}},
    {'result': 0, 'data': {'userId': OWNER, 'userName': '作者'}}])
def test_creator_identity_refuses_partial_or_failed_responses(data):
    with pytest.raises(AdapterError):
        ks._creator_profile(data)


@pytest.mark.parametrize('existing_urls', [[], ['https://cp.kuaishou.com/article/publish/video']])
@pytest.mark.parametrize('profile_path,extra', [(ks.CREATOR_PROFILE_PATH, {}), (ks.CURRENT_ACCOUNT_PATH, {'logined': True})])
def test_creator_identity_fallback_uses_own_page_response_and_closes_it(existing_urls, profile_path, extra):
    profile = response({'result': 1, 'data': {'userId': int(OWNER), 'userName': '创作者', 'token': 'secret', **extra}},
                       url='https://cp.kuaishou.com'+profile_path+'?signature=private')
    class Capture:
        async def __aenter__(self):
            self.value = asyncio.create_task(asyncio.sleep(0, result=profile))
            return self
        async def __aexit__(self, *args):
            pass
    def expect(predicate, **kwargs):
        assert kwargs['timeout'] == 15000
        assert predicate(profile)
        assert not predicate(response({}, url='https://evil.test'+ks.CREATOR_PROFILE_PATH))
        assert not predicate(response({}, url='https://cp.kuaishou.com/rest/cp/works/v2/video/pc/submit'))
        return Capture()
    page = SimpleNamespace(expect_response=expect, goto=AsyncMock(), close=AsyncMock())
    context = SimpleNamespace(request=SimpleNamespace(post=AsyncMock(return_value=response({'errors': ['not logged into public site']}))),
                              new_page=AsyncMock(return_value=page), pages=[SimpleNamespace(url=url) for url in existing_urls])
    assert asyncio.run(ks.KuaishouAdapter().identity(context)) == {'uid': OWNER, 'name': '创作者'}
    assert page.goto.call_args.args == ('https://cp.kuaishou.com/profile',)
    page.close.assert_awaited_once()


@pytest.mark.parametrize('logined', [None, False, 1, 'true'])
def test_current_creator_account_needs_explicit_logged_in_flag(logined):
    with pytest.raises(AdapterError):
        ks._current_creator_profile({'result': 1, 'data': {'userId': int(OWNER), 'userName': '创作者', 'logined': logined}})


def test_creator_identity_fallback_closes_page_on_navigation_failure():
    class Capture:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
    page = SimpleNamespace(expect_response=lambda *a, **k: Capture(),
        goto=AsyncMock(side_effect=RuntimeError('token=secret')), close=AsyncMock())
    context = SimpleNamespace(request=SimpleNamespace(post=AsyncMock(return_value=response({}, status=403))),
                              new_page=AsyncMock(return_value=page), pages=[SimpleNamespace(url='https://cp.kuaishou.com/profile')])
    with pytest.raises(AdapterError) as error:
        asyncio.run(ks.KuaishouAdapter().identity(context))
    assert 'secret' not in str(error.value)
    page.close.assert_awaited_once()


def test_waiting_for_qr_does_not_open_another_creator_login():
    context = SimpleNamespace(request=SimpleNamespace(post=AsyncMock(return_value=response({}, status=403))),
        pages=[SimpleNamespace(url=ks.KuaishouAdapter.login_url)], new_page=AsyncMock())
    with pytest.raises(AdapterError):
        asyncio.run(ks.KuaishouAdapter().identity(context))
    context.new_page.assert_not_awaited()


@pytest.mark.parametrize("url", [
    f"https://cp.kuaishou.com{ks.SUBMIT_PATH}",
    f"https://cp.kuaishou.com:443{ks.SUBMIT_PATH}?__NS_sig3=signed",
    f"https://cp.kuaishou.com{ks.ISOLATED_SUBMIT_PATH}",
    f"https://cp.kuaishou.com:443{ks.ISOLATED_SUBMIT_PATH}?__NS_sig3=signed",
])
def test_only_final_publishing_endpoint_matches(url):
    assert ks.KuaishouAdapter().is_submit(url)


@pytest.mark.parametrize("url", [
    f"http://cp.kuaishou.com{ks.SUBMIT_PATH}",
    f"https://cp.kuaishou.com.evil.test{ks.SUBMIT_PATH}",
    f"https://evil.test/?next=https://cp.kuaishou.com{ks.SUBMIT_PATH}",
    f"https://cp.kuaishou.com:444{ks.SUBMIT_PATH}",
    f"https://user:secret@cp.kuaishou.com{ks.SUBMIT_PATH}",
    f"https://cp.kuaishou.com{ks.SUBMIT_PATH}/", "https://[invalid",
    f"https://cp.kuaishou.com.evil.test{ks.ISOLATED_SUBMIT_PATH}",
    f"https://user:secret@cp.kuaishou.com{ks.ISOLATED_SUBMIT_PATH}",
    f"http://cp.kuaishou.com{ks.ISOLATED_SUBMIT_PATH}",
    f"https://cp.kuaishou.com:444{ks.ISOLATED_SUBMIT_PATH}",
    f"https://cp.kuaishou.com{ks.ISOLATED_SUBMIT_PATH}/",
    "https://cp.kuaishou.com/rest/cp/works/v2/video/pc/upload/pre",
    "https://cp.kuaishou.com/rest/cp/works/v2/video/pc/upload/finish",
    "https://cp.kuaishou.com/article/manage/video?status=2&from=publish",
    "https://passport.kuaishou.com/pc/account/login/",
])
def test_uploads_redirects_and_foreign_hosts_are_not_submission_proof(url):
    assert not ks.KuaishouAdapter().is_submit(url)


@pytest.mark.parametrize("data", [None, {}, {"result": True}, {"result": "1"}, {"result": 1.0},
                                      {'result': 1, 'success': False}, {'result': 1, 'success': 1}])
def test_unknown_receipts_never_invent_creator_ids_from_oauth_or_upload_schema(data):
    with pytest.raises(AdapterError, match="不会自动重投"):
        ks.KuaishouAdapter().submission_receipt(data)


@pytest.mark.parametrize('data', [{'result': 1}, {'result': 1, 'data': {}},
                                      {"result": 1, "data": {"result": 1}},
                                      {"result": 1, "data": {"publishId": 123}},
                                      {"result": 1, "video_info": {"photo_id": WORK, "pending": False}},
                                      {"result": 1, "data": {"workId": WORK}}])
def test_verified_acceptance_without_public_id_is_submitted_but_never_public(data):
    receipt = ks.KuaishouAdapter().submission_receipt(data)
    assert receipt == {'accepted': True, 'confirmed_not_submitted': False, 'code': 1,
                       'awaiting_work_id': True, 'public': False}
    assert 'work_id' not in receipt and 'url' not in receipt


def test_only_explicit_provider_rejection_is_confirmed_not_submitted():
    assert ks.KuaishouAdapter().submission_receipt({"result": 100400, "message": "token=secret"}) == {
        "accepted": False, "confirmed_not_submitted": True, "code": 100400,
    }


def test_own_work_requires_exact_work_and_stable_author_not_title_or_pending_id():
    row = {"workId": WORK, "publishId": 123, "title": "相同标题", "userIdStr": OWNER,
           "photoStatus": 1, "publishStatus": 2, "judgementStatus": 0, "token": "secret"}
    data = {"result": 1, "data": {"list": [row]}}
    assert ks._owned_work(data, OWNER, WORK)
    assert not ks._owned_work(data, OWNER, "3xother00000000")
    assert not ks._owned_work({"result": True, "data": {"list": [row]}}, OWNER, WORK)
    assert not ks._owned_work({"result": 1, "data": {"list": [{**row, "workId": ""}]}}, OWNER, WORK)
    with pytest.raises(AdapterError, match="不属于"):
        ks._owned_work(data, "123", WORK)


class QrLocator:
    def __init__(self, visible=True):
        self.first = self
        self.visible = visible
        self.screenshot = AsyncMock()
        self.click = AsyncMock()
        self.wait_for = AsyncMock()

    def locator(self, selector):
        return self

    async def is_visible(self):
        return self.visible


def test_login_opens_passport_and_qr_capture_never_screenshots_account_page(tmp_path):
    qr = QrLocator()
    page = SimpleNamespace(url=ks.KuaishouAdapter.login_url, locator=lambda _: qr, goto=AsyncMock())
    context = SimpleNamespace(new_page=AsyncMock(return_value=page))
    adapter = ks.KuaishouAdapter()
    assert asyncio.run(adapter.prepare_login(context)) is page
    assert page.goto.call_args.args == (adapter.login_url,)
    assert asyncio.run(adapter.capture_qr(page, tmp_path / "qr.png"))
    qr.screenshot.assert_awaited_once_with(path=str(tmp_path / "qr.png"), timeout=5000)
    for url in (adapter.creator_url, "https://evil.test/pc/account/login/", "https://passport.kuaishou.com.evil/pc/account/login/"):
        page.url = url
        assert not asyncio.run(adapter.capture_qr(page, tmp_path / "qr.png"))
    assert qr.screenshot.await_count == 1


def test_missing_or_expired_qr_is_not_an_account_screenshot(tmp_path):
    qr = QrLocator(visible=False)
    page = SimpleNamespace(url=ks.KuaishouAdapter.login_url, locator=lambda _: qr)
    assert not asyncio.run(ks.KuaishouAdapter().capture_qr(page, tmp_path / "qr.png"))
    qr.screenshot.assert_not_awaited()


def test_login_waits_for_delayed_passport_form_before_switching_to_qr():
    events = []
    qr = QrLocator(visible=False)
    switch = QrLocator()
    form = QrLocator()

    async def mounted(**kwargs):
        events.append('form-mounted')

    async def visible():
        assert events == ['form-mounted']
        return False

    async def switched(**kwargs):
        events.append('switched')

    form.wait_for = AsyncMock(side_effect=mounted)
    qr.is_visible = visible
    switch.click = AsyncMock(side_effect=switched)
    form.locator = lambda selector: qr if 'qrcode' in selector else switch
    page = SimpleNamespace(locator=lambda _: form, goto=AsyncMock())
    context = SimpleNamespace(new_page=AsyncMock(return_value=page))
    assert asyncio.run(ks.KuaishouAdapter().prepare_login(context)) is page
    assert events == ['form-mounted', 'switched']
    switch.wait_for.assert_awaited_once_with(state='visible', timeout=15000)
    qr.wait_for.assert_awaited_once_with(state='visible', timeout=15000)


MINI_UPSTREAM = '''
KUAISHOU_PUBLISH_ATTEMPTS = 3
KUAISHOU_PUBLISH_STRATEGY_IMMEDIATE = "immediate"
async def cookie_auth(path):
    raise AssertionError("A global browser must never start")
class KSVideo:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)
    async def validate_base_args(self):
        await cookie_auth(self.account_file)
    async def upload(self, playwright):
        await self.validate_base_args()
        for _ in range(KUAISHOU_PUBLISH_ATTEMPTS):
            playwright.attempts.append({"caption": self.desc, "tags": self.tags,
                                       "storage": self.account_file, "headless": self.headless,
                                       "debug": self.debug, "executable": self.local_executable_path})
'''


def test_upload_uses_private_globals_once_without_unowned_cookie_browser(tmp_path, monkeypatch):
    source = tmp_path / "main.py"
    source.write_text(MINI_UPSTREAM)
    upstream = SimpleNamespace(__file__=str(source), KUAISHOU_PUBLISH_ATTEMPTS=3)
    monkeypatch.setattr(ks.importlib.util, "find_spec", lambda _: SimpleNamespace(origin=str(source)))
    monkeypatch.setattr(ks, "SOURCE_SHA256", hashlib.sha256(source.read_bytes()).hexdigest())
    async def checked_workflow(app, module, playwright):
        await module.KSVideo.upload(app, playwright)
    monkeypatch.setattr(ks, '_upload_video', checked_workflow)
    storage = tmp_path / "storage.json"
    storage.write_text("{}")
    guarded_pw = SimpleNamespace(attempts=[])
    asyncio.run(ks.KuaishouAdapter().upload(guarded_pw, {
        "title": "标题", "description": "简介", "tags": ["科普"], "video_path": "/tmp/video.mp4", "cover": "",
    }, storage))
    assert guarded_pw.attempts == [{"caption": "标题\n\n简介", "tags": ["科普"], "storage": str(storage),
                                    "headless": True, "debug": False, "executable": ""}]
    assert upstream.KUAISHOU_PUBLISH_ATTEMPTS == 3
    isolated = ks._isolated_upstream()
    assert isolated.KUAISHOU_PUBLISH_ATTEMPTS == 1
    assert asyncio.run(isolated._dump_page_debug(None, "sensitive"))
    assert set(tmp_path.iterdir()) == {source, storage}


def test_upload_refuses_unverified_source_before_any_browser(tmp_path, monkeypatch):
    source = tmp_path / "main.py"
    source.write_text("raise AssertionError('untrusted code ran')")
    monkeypatch.setattr(ks.importlib.util, "find_spec", lambda _: SimpleNamespace(origin=str(source)))
    with pytest.raises(AdapterError, match="已停止投稿"):
        asyncio.run(ks.KuaishouAdapter().upload(SimpleNamespace(), {
            "title": "标题", "description": "", "tags": [], "video_path": "/tmp/video.mp4",
        }, tmp_path / "storage.json"))


@pytest.mark.parametrize("change", [{"title": ""}, {"title": "长" * 81}, {"description": "长" * 501},
                                        {"tags": ["一", "二", "三", "四"]}, {"tags": [True]},
                                        {"tags": [""]}, {"video_path": None}])
def test_invalid_package_never_loads_uploader_or_browser(tmp_path, monkeypatch, change):
    monkeypatch.setattr(ks, "_isolated_upstream", lambda: pytest.fail("Uploader should not load"))
    with pytest.raises(AdapterError):
        asyncio.run(ks.KuaishouAdapter().upload(SimpleNamespace(), {
            "title": "标题", "description": "简介", "tags": [], "video_path": "/tmp/video.mp4", **change,
        }, tmp_path / "storage.json"))


def test_validate_payload_is_side_effect_free_and_works_without_local_paths(monkeypatch):
    monkeypatch.setattr(ks, "_isolated_upstream", lambda: pytest.fail("Must not load upstream"))
    monkeypatch.setattr(Path, "is_file", lambda _: pytest.fail("Must not inspect state"))
    adapter = ks.KuaishouAdapter()
    assert adapter.validate_payload({"title": "标题", "description": "简介", "tags": ["科技"]}) is None
    assert adapter.title_min == 1 and adapter.combined_limit == 500


@pytest.mark.parametrize("payload", [None, [], {"title": ""}, {"title": "长" * 81},
    {"title": "标题", "description": 0}, {"title": "标题", "tags": "话题"},
    {"title": "标题", "tags": ["长" * 31]}, {"title": "标题", "tags": [" "]},
    {"title": "标题", "tags": ["一", "二", "三", "四"]}])
def test_validate_payload_rejects_before_queue_or_browser(payload):
    with pytest.raises(AdapterError):
        ks.KuaishouAdapter().validate_payload(payload)


def test_combined_caption_boundary_includes_newlines_hashes_spaces_and_unicode():
    adapter = ks.KuaishouAdapter()
    payload = {"title": "标题", "description": "😀" * 488, "tags": ["科技", "AI"]}
    # 2 title + 2 separator + 488 description + (2+3)*2 topic overhead = 502.
    with pytest.raises(AdapterError, match="合计"):
        adapter.validate_payload(payload)
    payload["description"] = "😀" * 486
    assert adapter.validate_payload(payload) is None
    payload["description"] += "😀"
    with pytest.raises(AdapterError, match="合计"):
        adapter.validate_payload(payload)


def test_duplicate_or_blank_description_does_not_repeat_title_in_combined_length():
    adapter = ks.KuaishouAdapter()
    assert adapter.validate_payload({"title": "标题", "description": " 标题 ", "tags": []}) is None
    assert ks._caption("标题", " 标题 ") == "标题"
    assert ks._caption("标题", "  ") == "标题"
    assert ks._caption("标题", " 简介 ") == "标题\n\n 简介 "


def test_inspect_work_checks_identity_and_exact_owned_record_but_keeps_processing_nonpublic(tmp_path):
    rows = {"result": 1, "data": {"list": [{"workId": WORK, "userIdStr": OWNER,
            "photoStatus": 1, "publishStatus": 2, "judgementStatus": 0, "cookie": "secret"}]}}
    callbacks = {}

    async def goto(*args, **kwargs):
        callbacks["response"](response(rows, url=f"https://cp.kuaishou.com{ks.WORKS_PATH}"))

    page = SimpleNamespace(on=lambda event, cb: callbacks.update({event: cb}), goto=AsyncMock(side_effect=goto),
                           wait_for_timeout=AsyncMock())
    context = SimpleNamespace(request=SimpleNamespace(post=AsyncMock(return_value=response(PROFILE))),
                              new_page=AsyncMock(return_value=page), route=AsyncMock())
    browser = SimpleNamespace(new_context=AsyncMock(return_value=context), close=AsyncMock())
    pw = SimpleNamespace(chromium=SimpleNamespace(launch=AsyncMock(return_value=browser)))
    actual = asyncio.run(ks.KuaishouAdapter().inspect_work(pw, tmp_path / "storage.json", OWNER, WORK))
    assert actual['work_id'] == WORK and actual['uid'] == OWNER and actual['owned'] is True
    assert actual['public'] is False and actual['platform_state'] == 'processing'
    assert actual['url'] == f"https://www.kuaishou.com/short-video/{WORK}"
    assert ks.KuaishouAdapter.supports_public_check is True
    assert "secret" not in str(actual)
    browser.close.assert_awaited_once()


def test_inspect_work_owner_switch_fails_before_management_page(tmp_path):
    context = SimpleNamespace(request=SimpleNamespace(post=AsyncMock(return_value=response(PROFILE))), new_page=AsyncMock(), route=AsyncMock())
    browser = SimpleNamespace(new_context=AsyncMock(return_value=context), close=AsyncMock())
    pw = SimpleNamespace(chromium=SimpleNamespace(launch=AsyncMock(return_value=browser)))
    with pytest.raises(AdapterError, match="不一致"):
        asyncio.run(ks.KuaishouAdapter().inspect_work(pw, tmp_path / "storage.json", "123", WORK))
    context.new_page.assert_not_awaited()
    browser.close.assert_awaited_once()
