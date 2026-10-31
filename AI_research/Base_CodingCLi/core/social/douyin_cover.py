"""Set the profile cover in Douyin's current, separate portrait editor."""
from __future__ import annotations

from core.douyin import DouyinError


async def set_portrait_cover(page, path: str, native_click, *, landscape_path: str = '') -> None:
    """Use the labelled 3:4 tile, then read back its saved portrait thumbnail."""
    try:
        tile = page.locator('[class*="coverControl-"]').filter(has=page.get_by_text('竖封面3:4', exact=True))
        if await tile.count() != 1:
            raise DouyinError('未找到唯一的抖音竖封面入口，请检查上传页面。')
        previous = await tile.locator('img').first.get_attribute('src')
        await native_click(page, tile.locator('[class^="cover-"]').first)
        modal = page.locator('div.dy-creator-content-modal').first
        await modal.wait_for(state='visible', timeout=15000)
        portrait_tab = modal.get_by_text('设置竖封面', exact=True)
        if await portrait_tab.count():
            await native_click(page, portrait_tab.first)
        # The other file input is an AI reference slot, not the actual cover upload.
        upload = modal.locator('.semi-upload:has(.semi-upload-drag-area-main-text) input.semi-upload-hidden-input').first
        if not await upload.count():
            raise DouyinError('抖音封面上传位置已变化，请检查竖封面编辑窗口。')
        await upload.set_input_files(path)
        await page.wait_for_function('''() => Array.from(document.querySelectorAll(
            'div.dy-creator-content-modal img')).some(im => im.naturalWidth > 300 && im.naturalHeight > im.naturalWidth)''', timeout=45000)
        if landscape_path:
            await native_click(page, modal.get_by_text('设置横封面', exact=True).first)
            await modal.locator('.semi-upload:has(.semi-upload-drag-area-main-text) input.semi-upload-hidden-input').first.set_input_files(landscape_path)
            await page.wait_for_function('''() => Array.from(document.querySelectorAll(
                'div.dy-creator-content-modal img')).some(im => im.naturalWidth > 300 &&
                Math.abs(im.naturalWidth / im.naturalHeight - 4 / 3) < 0.02)''', timeout=45000)
        done = modal.get_by_role('button', name='完成', exact=True).first
        await done.wait_for(state='visible', timeout=10000)
        await page.wait_for_function('''() => Array.from(document.querySelectorAll(
            'div.dy-creator-content-modal button')).some(b => b.textContent.trim() === '完成' &&
            !b.disabled && !b.className.includes('semi-button-disabled'))''', timeout=45000)
        await native_click(page, done)
        # Douyin recommends adding a horizontal cover when only the portrait was supplied.
        try:
            await modal.wait_for(state='hidden', timeout=3000)
        except Exception:
            skip = page.get_by_role('button', name='暂不设置', exact=True)
            if await skip.count() == 1:
                await native_click(page, skip)
        await modal.wait_for(state='hidden', timeout=15000)
        await page.wait_for_function('''previous => {
            const tile = Array.from(document.querySelectorAll('[class*="coverControl-"]'))
                .find(el => el.textContent.includes('竖封面3:4'));
            const image = tile?.querySelector('img');
            return image && image.getAttribute('src') !== previous && image.naturalWidth > 0 &&
                image.naturalHeight > image.naturalWidth;
        }''', arg=previous, timeout=30000)
    except DouyinError:
        raise
    except Exception:
        raise DouyinError('抖音竖封面未能完成设置或保存，已停止在发布前；请检查封面编辑窗口。') from None
