"""The preparation diagnostic must not send any publishing or unknown mutation."""
import asyncio
from contextlib import asynccontextmanager
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from core.social.adapters.kuaishou import KuaishouAdapter
from core.social.store import SocialStore
from scripts import diagnose_social_upload as diagnostic
from scripts.diagnose_social_upload import endpoint_label, exception_summary, pending_locations, preparation_operation, preparation_status, profile_evidence


@pytest.mark.parametrize('url', [
    'https://cp.kuaishou.com/rest/cp/works/v2/video/pc/submit',
    'https://cp.kuaishou.com/rest/cp/works/v2/video/pc/isolation/submit',
    'https://cp.kuaishou.com/rest/cp/works/v3/video/pc/submit',
    'https://cp.kuaishou.com/rest/cp/works/v2/video/pc/upload/finish/submit',
    'https://cp.kuaishou.com.evil.example/rest/cp/works/v2/video/pc/upload/pre',
    'https://secret@cp.kuaishou.com/rest/cp/works/v2/video/pc/upload/pre',
    'http://cp.kuaishou.com/rest/cp/works/v2/video/pc/upload/pre',
    'https://cp.kuaishou.com:444/rest/cp/works/v2/video/pc/upload/pre',
    'https://cp.kuaishou.com/rest/cp/works/v2/video/pc/delete',
])
def test_all_publishing_unknown_and_foreign_mutations_are_denied(url):
    assert preparation_operation('POST', url) == ''


@pytest.mark.parametrize('method', ['PUT', 'DELETE', 'PATCH'])
def test_even_known_upload_paths_do_not_accept_unreviewed_methods(method):
    assert preparation_operation(method, 'https://cp.kuaishou.com/rest/cp/works/v2/video/pc/upload/pre') == ''


def test_only_reviewed_upload_and_lookup_posts_are_allowed():
    assert preparation_operation('POST', 'https://cp.kuaishou.com/rest/cp/works/v2/video/pc/upload/pre') == 'upload_pre'
    assert preparation_operation('POST', 'https://cp.kuaishou.com/rest/cp/works/v2/video/pc/upload/isolation/finish') == 'upload_finish'
    assert preparation_operation('POST', 'https://cp.kuaishou.com/rest/cp/works/v4/video/pc/cover/edit/cover/upload') == 'cover_upload'
    assert preparation_operation('POST', 'https://upload.kuaishouzt.com/api/upload/fragment?upload_token=private') == 'file_fragment'
    assert preparation_operation('GET', 'https://cp.kuaishou.com/article/publish/video') == 'read'


def test_blocked_endpoint_diagnostics_never_keep_query_values_or_dynamic_ids():
    label = endpoint_label('https://cp.kuaishou.com/rest/cp/secret01234567890abcdef/lookup?token=private-cookie-secret')
    assert label == 'cp.kuaishou.com/rest/cp/:id/lookup'
    assert endpoint_label('https://private-token-secret.example/rest/cp/info') == 'other-host'


def test_account_evidence_keeps_public_profile_without_credentials():
    output = profile_evidence({'result': 1, 'data': {'userId': 123, 'userName': '测试作者',
        'token': 'private-token-secret', 'cookie': 'private-cookie-secret'}})
    assert output['public_fields'] == {'data.userId': '123', 'data.userName': '测试作者'}
    assert 'private-' not in str(output)


def test_preparation_status_keeps_only_bounded_numeric_codes_and_boolean_success():
    assert preparation_status({'result': 1, 'code': 0, 'success': True,
        'message': 'private-cookie', 'data': {'upload_token': 'private-token'}}) == {
        'result': 1, 'code': 0, 'success': True}
    assert preparation_status({'result': 'private-token', 'code': True,
        'error_code': 2**54, 'success': 'private-cookie'}) == {}


def test_pending_locations_include_no_local_values_or_coroutine_arguments():
    async def run():
        event = asyncio.Event()

        async def waiting(private_argument):
            secret_local = private_argument
            await event.wait()
            return secret_local

        task = asyncio.create_task(waiting('private-cookie-secret'))
        await asyncio.sleep(0)
        output = pending_locations(task)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        return output

    output = asyncio.run(run())
    assert output[0]['function'] == 'waiting'
    assert 'private-cookie-secret' not in str(output)


def test_exception_classification_never_copies_urls_or_message_details():
    try:
        raise RuntimeError('strict mode violation; https://private.example/?token=private-cookie-secret')
    except RuntimeError as error:
        output = exception_summary(error)
    assert output['kind'] == 'ambiguous_control'
    assert output['locations']
    assert 'private-cookie-secret' not in str(output) and 'https://' not in str(output)


@pytest.mark.parametrize('endpoint,intercepted', [
    ('https://cp.kuaishou.com/rest/cp/works/v2/video/pc/submit', 1),
    ('https://cp.kuaishou.com/rest/cp/works/v2/video/pc/submit?token=private', 1),
    ('https://cp.kuaishou.com/rest/cp/works/v2/video/pc/isolation/submit', 1),
    ('https://cp.kuaishou.com/rest/cp/works/v2/video/pc/isolation/submit?token=private', 1),
    ('https://cp.kuaishou.com/rest/cp/works/v3/video/pc/submit', 0),
    ('https://cp.kuaishou.com/rest/cp/works/v2/video/pc/publish', 0),
    ('https://cp.kuaishou.com/rest/cp/works/v2/video/pc/delete', 0),
    ('https://cp.kuaishou.com.evil.example/rest/cp/works/v2/video/pc/submit', 0),
])
def test_entire_diagnostic_aborts_real_route_before_network_and_preserves_original_task(tmp_path, monkeypatch, endpoint, intercepted):
    """Exercise diagnose(), not just its URL helper, under fake browser/network transports."""
    db = SocialStore(str(tmp_path), 'kuaishou')
    row = db.create('UPLOAD', '123', {'title': '诊断测试', 'tags': []}, render={'id': 'r', 'script_id': 's', 'version': 1})
    db.update(row['id'], status='UNKNOWN', result={'invocation_started': True})
    before = db.get(row['id'])
    storage = diagnostic.credential(tmp_path/'platforms/kuaishou', '123')
    storage.parent.mkdir(parents=True)
    storage.write_text('{}')
    route = SimpleNamespace(request=SimpleNamespace(method='POST', url=endpoint), abort=AsyncMock(),
                            continue_=AsyncMock(side_effect=AssertionError('Publishing must never reach the network')),
                            fetch=AsyncMock(side_effect=AssertionError('Publishing must never be replayed')))

    class Context:
        async def route(self, pattern, callback):
            assert pattern == '**/*'
            self.callback = callback

        def on(self, *args):
            pass

    context = Context()
    browser = SimpleNamespace(new_context=AsyncMock(return_value=context), close=AsyncMock())

    class Provider:
        is_submit = staticmethod(KuaishouAdapter().is_submit)
        validate_payload = staticmethod(KuaishouAdapter().validate_payload)
        identity = AsyncMock(return_value={'uid': '123', 'name': '测试作者'})

        async def upload(self, pw, payload, storage):
            guarded_browser = await pw.chromium.launch()
            guarded_context = await guarded_browser.new_context()
            await guarded_context.callback(route)

    @asynccontextmanager
    async def fake_playwright():
        yield SimpleNamespace()

    package = ModuleType('patchright'); package.__path__ = []
    api = ModuleType('patchright.async_api'); api.async_playwright = fake_playwright
    monkeypatch.setitem(sys.modules, 'patchright', package)
    monkeypatch.setitem(sys.modules, 'patchright.async_api', api)
    monkeypatch.setattr(diagnostic, 'adapter', lambda platform: Provider())
    monkeypatch.setattr(diagnostic, 'verify_upstream', lambda platform: None)
    monkeypatch.setattr(diagnostic, 'silence_upstream_logs', lambda: None)
    monkeypatch.setattr(diagnostic, 'launch', AsyncMock(return_value=browser))

    output = asyncio.run(diagnostic.diagnose(tmp_path, row['id'], timeout=1))
    assert output['publishing_protection_active'] is True
    assert output['final_publish_requests_intercepted'] == intercepted
    assert output['blocked_unknown_mutations'] == (0 if intercepted else 1)
    route.abort.assert_awaited_once()
    route.continue_.assert_not_awaited()
    route.fetch.assert_not_awaited()
    assert db.get(row['id']) == before
    browser.close.assert_awaited_once()
