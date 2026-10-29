"""Video-input selection and one-shot publishing workflow without a live browser."""
import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from core.social.adapter import AdapterError, UploadPreparationError
from core.social.adapters.kuaishou import _publish_once, _select_video_file, _upload_video, _wait_for_video_upload


class FileInputs:
    def __init__(self, accepts):
        self.items = [SimpleNamespace(get_attribute=AsyncMock(return_value=accept),
            set_input_files=AsyncMock(), wait_for=AsyncMock()) for accept in accepts]

    @property
    def first(self):
        return self.items[0]

    def nth(self, index):
        return self.items[index]

    async def count(self):
        return len(self.items)


def selection_page(accepts, *, buttons=0):
    inputs = FileInputs(accepts)
    button = SimpleNamespace(count=AsyncMock(return_value=buttons), click=AsyncMock())
    chooser = SimpleNamespace(set_files=AsyncMock())

    @asynccontextmanager
    async def expect_chooser():
        pending = asyncio.get_running_loop().create_future()
        pending.set_result(chooser)
        yield SimpleNamespace(value=pending)

    page = SimpleNamespace(locator=lambda selector: inputs, get_by_role=lambda *args, **kwargs: button,
                           get_by_text=lambda *a, **kw: SimpleNamespace(count=AsyncMock(return_value=0)),
                           expect_file_chooser=expect_chooser)
    return page, inputs, button, chooser


def test_video_file_input_is_selected_even_when_cover_input_comes_first():
    page, inputs, button, _ = selection_page(['image/*', 'video/mp4,video/webm'])
    asyncio.run(_select_video_file(page, '/fake/video.mp4'))
    inputs.items[0].set_input_files.assert_not_awaited()
    inputs.items[1].set_input_files.assert_awaited_once_with('/fake/video.mp4')
    button.click.assert_not_awaited()


@pytest.mark.parametrize('accept', ['.MP4,.MOV', '', None])
def test_unique_compatible_file_input_needs_no_css_upload_button(accept):
    page, inputs, _, _ = selection_page([accept])
    asyncio.run(_select_video_file(page, '/fake/video.mp4'))
    inputs.items[0].wait_for.assert_awaited_once_with(state='attached', timeout=60000)
    inputs.items[0].set_input_files.assert_awaited_once()


def test_a_cover_input_is_never_used_as_the_video_input():
    page, inputs, _, _ = selection_page(['image/png,image/jpeg'])
    with pytest.raises(AdapterError, match='唯一'):
        asyncio.run(_select_video_file(page, '/fake/video.mp4'))
    inputs.items[0].set_input_files.assert_not_awaited()


def test_failed_creator_application_is_reported_as_preparation_failure():
    page, _, button, chooser = selection_page([])
    page.get_by_text = lambda text, **kw: SimpleNamespace(count=AsyncMock(return_value=1 if text == '应用加载失败' else 0))
    with pytest.raises(UploadPreparationError, match='应用加载失败'):
        asyncio.run(_select_video_file(page, '/fake/video.mp4'))
    button.click.assert_not_awaited()
    chooser.set_files.assert_not_awaited()


def test_two_video_inputs_are_not_silently_resolved_by_first_position():
    page, inputs, _, _ = selection_page(['video/*', '.mp4'])
    with pytest.raises(AdapterError, match='多个'):
        asyncio.run(_select_video_file(page, '/fake/video.mp4'))
    for item in inputs.items:
        item.set_input_files.assert_not_awaited()


def test_unique_semantic_upload_button_can_open_a_dynamic_file_input():
    page, _, button, chooser = selection_page([], buttons=1)
    asyncio.run(_select_video_file(page, '/fake/video.mp4'))
    button.click.assert_awaited_once()
    chooser.set_files.assert_awaited_once_with('/fake/video.mp4')


@pytest.mark.parametrize('buttons', [0, 2])
def test_missing_or_ambiguous_semantic_button_never_opens_a_file_chooser(buttons):
    page, _, button, chooser = selection_page([], buttons=buttons)
    with pytest.raises(AdapterError):
        asyncio.run(_select_video_file(page, '/fake/video.mp4'))
    button.click.assert_not_awaited()
    chooser.set_files.assert_not_awaited()


def workflow(*, failed=False, cover_error=None):
    page, inputs, _, _ = selection_page(['image/*', 'video/*'])
    publish = SimpleNamespace(count=AsyncMock(return_value=1), click=AsyncMock())

    def role(role_name, **kwargs):
        assert role_name == 'button' and kwargs == {'name': '发布', 'exact': True}
        return publish

    def text(value, **kwargs):
        assert value in ('上传中', '上传失败'), 'Publishing must not use an ambiguous text selector'
        return SimpleNamespace(count=AsyncMock(return_value=1 if value == '上传失败' and failed else 0),
                               nth=lambda index: SimpleNamespace(is_visible=AsyncMock(return_value=True)))

    page.get_by_role = role
    page.get_by_text = text
    page.goto = AsyncMock()
    page.wait_for_url = AsyncMock()
    page.wait_for_timeout = AsyncMock()
    page.keyboard = SimpleNamespace(press=AsyncMock(), type=AsyncMock())
    context = SimpleNamespace(new_page=AsyncMock(return_value=page), close=AsyncMock())
    browser = SimpleNamespace(new_context=AsyncMock(return_value=context), close=AsyncMock())
    pw = SimpleNamespace(chromium=SimpleNamespace(launch=AsyncMock(return_value=browser)))
    module = SimpleNamespace(set_init_script=AsyncMock(return_value=context),
        KUAISHOU_UPLOAD_URL='https://cp.kuaishou.com/article/publish/video',
        KUAISHOU_UPLOAD_URL_PATTERN='**/article/publish/video**',
        KUAISHOU_MANAGE_URL_PATTERN='**/article/manage/video**', KUAISHOU_UPLOAD_TIMEOUT_SECONDS=480,
        _focus_desc_editor=AsyncMock(), _click_visible_publish_confirm=AsyncMock(return_value=False))
    app = SimpleNamespace(validate_upload_args=AsyncMock(), headless=True, account_file='/fake/account.json',
        file_path='/fake/video.mp4', close_guide_overlay=AsyncMock(), desc='描述', title='标题', tags=['科技'],
        set_thumbnail=AsyncMock(side_effect=cover_error), apply_collection=AsyncMock())
    return app, module, pw, page, inputs, publish, context, browser


def test_new_workflow_selects_video_directly_and_clicks_one_exact_publish_button():
    app, module, pw, page, inputs, publish, context, browser = workflow()
    asyncio.run(_upload_video(app, module, pw))
    page.goto.assert_awaited_once_with(module.KUAISHOU_UPLOAD_URL, wait_until='domcontentloaded', timeout=45000)
    inputs.items[0].set_input_files.assert_not_awaited()
    inputs.items[1].set_input_files.assert_awaited_once()
    publish.click.assert_awaited_once()
    context.close.assert_awaited_once()
    browser.close.assert_awaited_once()


def test_navigation_failure_is_safe_to_retry_and_never_exposes_raw_page_error():
    app, module, pw, page, _, publish, context, browser = workflow()
    page.goto.side_effect = TimeoutError('https://private.example/?token=private-token-secret')
    with pytest.raises(UploadPreparationError) as captured:
        asyncio.run(_upload_video(app, module, pw))
    assert 'private-' not in str(captured.value)
    publish.click.assert_not_awaited()
    context.close.assert_awaited_once()
    browser.close.assert_awaited_once()


def test_upload_failure_before_publication_does_not_click_publish_and_always_closes_resources():
    app, module, pw, _, _, publish, context, browser = workflow(failed=True)
    with pytest.raises(AdapterError):
        asyncio.run(_upload_video(app, module, pw))
    publish.click.assert_not_awaited()
    context.close.assert_awaited_once()
    browser.close.assert_awaited_once()


def test_cover_dialog_failure_is_closed_and_the_upload_continues_with_the_platform_cover():
    app, module, pw, page, _, publish, context, browser = workflow(cover_error=TimeoutError('弹窗没出现'))
    asyncio.run(_upload_video(app, module, pw))
    page.keyboard.press.assert_any_await('Escape')
    app.apply_collection.assert_awaited_once()
    publish.click.assert_awaited_once()
    context.close.assert_awaited_once()
    browser.close.assert_awaited_once()


@pytest.mark.parametrize('visible_indices', [[], [0], [1], [0, 1]])
def test_custom_publish_control_requires_one_visible_exact_text_match(visible_indices):
    items = [SimpleNamespace(is_visible=AsyncMock(return_value=i in visible_indices),
                             click=AsyncMock()) for i in range(2)]
    matches = SimpleNamespace(count=AsyncMock(return_value=2), nth=lambda index: items[index])
    page = SimpleNamespace(get_by_role=lambda *a, **kw: SimpleNamespace(count=AsyncMock(return_value=0)),
        get_by_text=lambda text, **kw: matches if text == '发布' and kw == {'exact': True} else None,
        wait_for_timeout=AsyncMock())
    module = SimpleNamespace(_click_visible_publish_confirm=AsyncMock(return_value=False))
    if len(visible_indices) == 1:
        asyncio.run(_publish_once(page, module))
        items[visible_indices[0]].click.assert_awaited_once()
    else:
        with pytest.raises(AdapterError, match='唯一'):
            asyncio.run(_publish_once(page, module))
    assert sum(item.click.await_count for item in items) == (1 if len(visible_indices) == 1 else 0)


def test_publish_click_error_is_never_retried():
    app, module, _, page, _, publish, _, _ = workflow()
    publish.click.side_effect = RuntimeError('click failed')
    with pytest.raises(RuntimeError):
        asyncio.run(_publish_once(page, module))
    publish.click.assert_awaited_once()


def test_multiple_semantic_publish_buttons_stop_without_a_text_fallback():
    page = SimpleNamespace(get_by_role=lambda *a, **kw: SimpleNamespace(count=AsyncMock(return_value=2)),
                           get_by_text=lambda *a, **kw: pytest.fail('Ambiguous buttons must stop'))
    module = SimpleNamespace(_click_visible_publish_confirm=AsyncMock(return_value=False))
    with pytest.raises(AdapterError, match='唯一'):
        asyncio.run(_publish_once(page, module))


@pytest.mark.parametrize('error_visible', [False, True])
def test_upload_progress_ignores_hidden_labels_but_reports_visible_failure(error_visible):
    def text(label, **kwargs):
        return SimpleNamespace(count=AsyncMock(return_value=1), nth=lambda index:
            SimpleNamespace(is_visible=AsyncMock(return_value=label == '上传失败' and error_visible)))
    page = SimpleNamespace(get_by_text=text, wait_for_timeout=AsyncMock())
    if error_visible:
        with pytest.raises(UploadPreparationError, match='上传失败'):
            asyncio.run(_wait_for_video_upload(page, 0))
    else:
        asyncio.run(_wait_for_video_upload(page, 0))
    page.wait_for_timeout.assert_not_awaited()


def test_visible_upload_progress_waits_until_it_disappears():
    progress = SimpleNamespace(is_visible=AsyncMock(side_effect=[True, True, False]))
    def text(label, **kwargs):
        return SimpleNamespace(count=AsyncMock(return_value=int(label == '上传中')), nth=lambda index: progress)
    page = SimpleNamespace(get_by_text=text, wait_for_timeout=AsyncMock())
    asyncio.run(_wait_for_video_upload(page, 60))
    assert page.wait_for_timeout.await_count == 2


def test_real_upload_timeout_remains_a_preparation_failure_without_a_publish_click():
    def text(label, **kwargs):
        return SimpleNamespace(count=AsyncMock(return_value=int(label == '上传中')),
                               nth=lambda index: SimpleNamespace(is_visible=AsyncMock(return_value=True)))
    page = SimpleNamespace(get_by_text=text, wait_for_timeout=AsyncMock())
    with pytest.raises(UploadPreparationError, match='上传超时，未点击发布'):
        asyncio.run(_wait_for_video_upload(page, 0))
