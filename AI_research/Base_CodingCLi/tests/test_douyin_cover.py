"""The portrait editor must save the requested image before an upload can continue."""
import asyncio

import pytest

from core.douyin import DouyinError
from core.social.douyin_cover import set_portrait_cover


class Node:
    def __init__(self, page, kind):
        self.page, self.kind = page, kind

    @property
    def first(self):
        return self

    def filter(self, **kwargs):
        assert kwargs['has'].kind == '竖封面3:4'
        return self

    async def count(self):
        return self.page.counts.get(self.kind, 1)

    def locator(self, selector):
        if selector == 'img':
            return Node(self.page, 'thumbnail')
        if selector.startswith('.semi-upload:has('):
            return Node(self.page, 'actual_cover_input')
        return Node(self.page, 'portrait_tile')

    def get_by_text(self, text, **kwargs):
        return Node(self.page, text)

    def get_by_role(self, role, name, **kwargs):
        assert role == 'button'
        return Node(self.page, name)

    async def get_attribute(self, name):
        assert name == 'src'
        return 'old_landscape_thumbnail'

    async def set_input_files(self, path):
        assert self.kind == 'actual_cover_input'
        self.page.calls.append(('upload', path))

    async def wait_for(self, *, state, **kwargs):
        self.page.calls.append((self.kind, state))


class Page:
    def __init__(self, counts=None, read_back=True):
        self.calls, self.counts, self.read_back = [], counts or {}, read_back

    def locator(self, selector):
        return Node(self, 'tile' if 'coverControl' in selector else 'modal')

    def get_by_text(self, text, **kwargs):
        return Node(self, text)

    async def wait_for_function(self, expression, **kwargs):
        assert ('upload', '/portrait.jpg') in self.calls
        if 'arg' in kwargs:
            assert kwargs['arg'] == 'old_landscape_thumbnail'
            assert ('modal', 'hidden') in self.calls
            if not self.read_back:
                raise TimeoutError()
        self.calls.append(('ready', None))


async def click(page, node):
    page.calls.append(('click', node.kind))


def test_selects_portrait_tile_and_actual_upload_slot_then_reads_back_saved_cover():
    page = Page()
    asyncio.run(set_portrait_cover(page, '/portrait.jpg', click))
    assert ('click', 'portrait_tile') in page.calls and ('click', '设置竖封面') in page.calls
    assert ('upload', '/portrait.jpg') in page.calls and ('click', '完成') in page.calls
    assert page.calls[-1] == ('ready', None)


def test_sets_horizontal_cover_in_its_own_tab():
    page = Page()
    asyncio.run(set_portrait_cover(page, '/portrait.jpg', click, landscape_path='/landscape.jpg'))
    assert page.calls.index(('upload', '/portrait.jpg')) < page.calls.index(('click', '设置横封面'))
    assert page.calls.index(('click', '设置横封面')) < page.calls.index(('upload', '/landscape.jpg'))
    assert page.calls.index(('upload', '/landscape.jpg')) < page.calls.index(('click', '完成'))


@pytest.mark.parametrize('counts,read_back', [({'tile': 2}, True), ({'actual_cover_input': 0}, True), ({}, False)])
def test_missing_or_unsaved_cover_does_not_silently_continue(counts, read_back):
    page = Page(counts, read_back)
    with pytest.raises(DouyinError):
        asyncio.run(set_portrait_cover(page, '/portrait.jpg', click))
