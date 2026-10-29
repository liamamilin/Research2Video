(prefix) => {
  const clean = value => (value || '').replace(/\s+/g, ' ').trim();
  const visible = el => {
    const r = el.getBoundingClientRect(), s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden';
  };
  const body = clean(document.body?.innerText || '');
  const title = clean(document.title);
  let blocked = '';
  if (/just a moment|verify.*human|access denied|security verification|人机验证|安全验证|访问受限/i.test(title)
      || (body.length < 2500 && /verify you are human|checking your browser|完成验证|请输入验证码/i.test(body))) blocked = '页面要求验证或拒绝访问';
  if ([...document.querySelectorAll('input[type=password]')].some(visible) && body.length < 4000) blocked = '页面需要登录';
  if (!blocked && body.length < 80) blocked = '页面正文尚未加载或内容过少';
  const candidates = [], seen = new Set();
  const add = (el, parts = [el], visual_kind = 'text') => {
    if (!visible(el) || candidates.length >= 100) return;
    const text = clean(parts.map(node => [node.innerText, node.getAttribute('alt'), node.getAttribute('aria-label'),
      ...[...node.querySelectorAll('img')].map(img => img.alt)].filter(Boolean).join(' ')).join(' '));
    const rects = parts.map(node => node.getBoundingClientRect());
    const box = {x: Math.min(...rects.map(r => r.left)) + scrollX, y: Math.min(...rects.map(r => r.top)) + scrollY,
      width: Math.max(...rects.map(r => r.right)) - Math.min(...rects.map(r => r.left)),
      height: Math.max(...rects.map(r => r.bottom)) - Math.min(...rects.map(r => r.top))};
    const key = text || el.getAttribute('src');
    if ((text.length < 12 && visual_kind !== 'image') || box.width < 240 || box.height < (visual_kind === 'code' ? 24 : 60)
        || box.height > 1400 || seen.has(key)) return;
    seen.add(key);
    const id = prefix + '-' + candidates.length;
    el.setAttribute('data-yanying-capture', id);
    candidates.push({id, selector: `[data-yanying-capture="${id}"]`, text: (text || title).slice(0, 9000), visual_kind, described: !!text,
      context: clean(el.closest('section,figure,article')?.querySelector('h1,h2,h3,figcaption')?.innerText || title).slice(0, 400),
      anchor: el.id || el.closest('[id]')?.id || '',
      heading: clean(el.querySelector('h1,h2,h3,h4')?.innerText || (/^H[1-4]$/.test(el.tagName) ? el.innerText : '')),
      parts: parts.length, box});
  };
  // Images and short examples are useful video subjects even when they carry
  // little DOM text. Keep them ahead of large page containers in the budget.
  for (const el of document.querySelectorAll('main img,article img,.markdown-body img,figure img')) add(el, [el], 'image');
  for (const el of document.querySelectorAll('main pre,article pre,.markdown-body pre,main [class*=codeBlock],article [class*=codeBlock]')) add(el, [el], 'code');
  for (const el of document.querySelectorAll('table,[role=table]')) add(el, [el], 'table');
  for (const heading of document.querySelectorAll('main h1,main h2,main h3,article h1,article h2,article h3,h1,h2,h3')) {
    const parts = [heading];
    let next = heading.nextElementSibling;
    while (next && parts.length < 6 && !/^H[1-4]$/.test(next.tagName)) {
      if (visible(next)) parts.push(next);
      next = next.nextElementSibling;
      const a = parts[0].getBoundingClientRect(), b = parts.at(-1).getBoundingClientRect();
      if (b.bottom - a.top > 650) break;
    }
    add(heading, parts);
    // A heading can introduce a whole chapter. Offer its individual paragraphs
    // and examples as well, rather than discard that chapter as too tall.
    for (const part of parts.slice(1)) add(part, [part], part.tagName === 'PRE' ? 'code' : part.querySelector('img') ? 'image' : 'text');
  }
  for (const el of document.querySelectorAll('table,[role=table],main section,article section,figure,[id*=pricing],[class*=pricing],main,article')) add(el);
  const overlays = [...document.querySelectorAll('[role=dialog],[aria-modal=true],[id*=cookie],[class*=cookie]')]
    .filter(visible).map(el => {
      const r = el.getBoundingClientRect(), s = getComputedStyle(el);
      return {text: clean(el.innerText).slice(0, 300), fixed: s.position === 'fixed' || s.position === 'sticky',
        box: {x:r.x,y:r.y,width:r.width,height:r.height}};
    }).filter(item => item.fixed || item.box.width * item.box.height > innerWidth * innerHeight * .25);
  return {title, url:location.href, blocked, candidates, overlays};
}
