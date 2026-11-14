const $ = (selector) => document.querySelector(selector);
const escapeHtml = (value = '') => String(value).replace(/[&<>"']/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const stages = {idea:'创意萌芽',song:'歌曲创作',video:'MV 制作'};
let records = [], current = null, tab = 'idea', dirty = false, saving = false, noticeTimer;

async function api(path, options = {}) {
  const response = await fetch(path, { ...options, headers: {'Content-Type':'application/json', ...options.headers} });
  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new Error(typeof error.detail === 'string' ? error.detail : '输入内容不完整或格式不正确，请检查后保存。');
  }
  return response.json();
}
function notify(message, error = false) {
  clearTimeout(noticeTimer); $('#notice').textContent = message; $('#notice').hidden = false;
  $('#notice').classList.toggle('error', error); noticeTimer = setTimeout(() => $('#notice').hidden = true, 6500);
}
function action(fn) { return async (...args) => { try { await fn(...args); } catch (error) { notify(error.message, true); } }; }
function markDirty() { dirty = true; const status = $('#save-state'); if (status) status.textContent = '有未保存的修改'; }
function collect() {
  if (!current) return;
  document.querySelectorAll('[data-field]').forEach((el) => current.project[el.dataset.field] = el.value);
}
function canLeave() { return !dirty || window.confirm('这张创意卡有未保存的修改。放弃修改并切换吗？'); }
async function refresh() { records = await api('/api/projects'); renderLibrary(); }
function renderLibrary() {
  $('#total').textContent = records.length; $('#idea-count').textContent = records.length;
  $('#song-count').textContent = records.reduce((n,r) => n+r.project.songs.length,0);
  $('#video-count').textContent = records.reduce((n,r) => n+r.project.videos.length,0);
  const search = $('#search').value.trim().toLowerCase();
  const filtered = records.filter(({project:p}) => `${p.title} ${p.premise} ${p.hook}`.toLowerCase().includes(search));
  $('#list-count').textContent = `${filtered.length} 个作品`;
  $('#project-list').innerHTML = filtered.length ? filtered.map((r,i) => `<button class="project-card ${current?.id===r.id?'selected':''}" data-project="${r.id}"><div class="card-top"><span class="tag">${stages[r.project.stage]}</span><span class="card-number">${String(i+1).padStart(2,'0')}</span></div><h3>${escapeHtml(r.project.title)}</h3><p>${escapeHtml(r.project.premise || '先记住这个念头，再慢慢发展。')}</p><div class="card-foot"><span>${escapeHtml(r.project.emotion || '情绪待探索')}</span><span>${r.project.songs.length} 个歌曲版本</span></div></button>`).join('') : `<div class="empty"><strong>${search?'没有找到作品':'第一句灵感，是什么？'}</strong>${search?'试试其他关键词。':'新建一张创意卡，或载入蓝按钮案例。'}</div>`;
  document.querySelectorAll('[data-project]').forEach(button => button.onclick = action(async () => {
    if (!canLeave()) return; current = await api(`/api/projects/${button.dataset.project}`); dirty=false; tab='idea'; render();
  }));
}
function field(key, label, hint='', multiline=false, placeholder='') {
  const value=escapeHtml(current.project[key]);
  return `<label class="field"><span>${label}${hint?`<small>${hint}</small>`:''}</span>${multiline?`<textarea data-field="${key}" placeholder="${placeholder}">${value}</textarea>`:`<input data-field="${key}" value="${value}" placeholder="${placeholder}" ${key==='title'?'maxlength="120" required':''}>`}</label>`;
}
function ideaPanel() {
  return `${field('title','作品名字','可以先用一个工作标题')}${field('inspiration','灵感从哪里来','经历、对话、评论，或一个念头',true,'把最原始的素材记在这里。')}${field('premise','一句话故事','具体发生了什么？',true)}<div class="two-col">${field('audience','谁会有感觉')}${field('perspective','从谁的视角讲')}</div>${field('emotion','想让观众产生什么情绪','可以有递进和转折')}${field('hook','一句能记住的副歌', '',true)}${field('visuals','三个能演出来的画面','每行一个画面',true)}${field('ending','最后落在哪里','笑点、反转、释放，或一句余味',true)}`;
}
function songPanel() {
  const p = current.project;
  return `<p class="note">把完整歌词和曲风带到歌曲工具创作，再把每个结果记录在这里。当前支持版本管理，尚未接入自动生成与音频上传。</p>${field('lyrics','完整歌词','保留段落结构',true)}${field('style','音乐方向','曲风、情绪、人声与编曲',true)}<h3 class="subheading">歌曲版本 · ${p.songs.length}</h3>${p.songs.map(s => `<article class="version ${s.id===p.selected_song_id?'chosen':''}"><div class="section-top"><h3>${escapeHtml(s.label)}</h3><span class="tag">${s.id===p.selected_song_id?'已采用':'候选'}</span></div><p>${escapeHtml(s.provider || '来源待记录')} · ${s.duration_seconds?`${s.duration_seconds} 秒`:'时长待记录'}<br>${escapeHtml(s.feedback || '试听感受待记录')}</p><div class="version-actions">${s.source_url?`<a href="${escapeHtml(s.source_url)}" target="_blank" rel="noopener noreferrer">打开歌曲 ↗</a>`:''}<button data-choose="${s.id}">${s.id===p.selected_song_id?'当前采用版本':'采用这一版'}</button></div></article>`).join('') || '<p class="note">还没有歌曲版本。先保留每次生成结果，再挑选最能表达创意的一版。</p>'}<details><summary>＋ 记录一个歌曲版本</summary><form id="song-form"><label class="field"><span>版本名称</span><input name="label" required maxlength="120" placeholder="例如：电子说唱 · 第二版"></label><div class="two-col"><label class="field"><span>生成工具</span><input name="provider" placeholder="Mureka / 其他"></label><label class="field"><span>实际时长（秒，可选）</span><input name="duration" type="number" min="0.1" max="7200" step="0.1"></label></div><label class="field"><span>歌曲链接（可选）</span><input name="url" type="url" placeholder="https://"></label><label class="field"><span>试听感受与文件状态</span><textarea name="feedback" placeholder="哪句好听、哪些地方要调整、是否已下载。"></textarea></label><button class="quiet" type="submit">保存这个版本</button></form></details>`;
}
function videoPanel() {
  const p=current.project, song=p.songs.find(s => s.id===p.selected_song_id);
  return `<p class="note">MV 草案绑定具体歌曲版本。先安排歌词对应的画面；音频导入、准确时间轴和视频渲染将在后续接入。项目导出保存创作资料，当前不是剪映草稿文件。</p>${song?`<article class="version chosen"><span class="tag">当前歌曲</span><h3 style="margin-top:12px">${escapeHtml(song.label)}</h3><p>${escapeHtml(song.provider)} · ${song.duration_seconds || '待确认'} 秒</p></article>`:'<p class="note">先在「歌曲创作」中采用一个歌曲版本，再新建 MV 草案。</p>'}${p.videos.map(v => `<article class="version"><div class="section-top"><h3>${escapeHtml(v.title)}</h3><span class="tag">${v.format} · 草案</span></div><p>歌曲：${escapeHtml(p.songs.find(s=>s.id===v.song_id)?.label || '')}</p><label class="field"><span>画面安排</span><textarea data-video="${v.id}" rows="7">${escapeHtml(v.treatment)}</textarea></label></article>`).join('')}${song?'<button id="add-video" class="quiet">＋ 为当前歌曲新建 MV 草案</button>':''}`;
}
function render() {
  renderLibrary();
  if (!current) { $('#editor').innerHTML='<div class="empty"><span class="big-note">♫</span><strong>给灵感一个生长的地方</strong>记下一个故事，找到一句副歌，<br>再让歌曲与画面替你表达。<br><br>左侧选择作品，或点击「新建创意」。</div>'; return; }
  const p=current.project;
  $('#editor').innerHTML=`<div class="editor-head"><div class="eyebrow">WORK IN PROGRESS</div><div class="editor-title"><h2>${escapeHtml(p.title)}</h2><span class="tag">${stages[p.stage]}</span></div><span id="save-state" class="save-state">${dirty?'有未保存的修改':`已保存 · 版本 ${current.version}`}</span><nav class="tabs" aria-label="创作环节">${[['idea','01 创意挖掘'],['song','02 歌曲创作'],['video','03 MV 制作']].map(([key,label])=>`<button data-tab="${key}" class="${tab===key?'active':''}" aria-current="${tab===key?'page':'false'}">${label}</button>`).join('')}</nav></div><div class="editor-body">${tab==='idea'?ideaPanel():tab==='song'?songPanel():videoPanel()}</div><footer class="editor-actions"><button id="save-project" class="primary">保存作品</button>${tab==='idea'?'<button id="develop-idea" class="quiet">生成创作简报</button>':''}<button id="export-project" class="export">导出项目 ↗</button></footer>`;
  document.querySelectorAll('[data-field]').forEach(el=>el.addEventListener('input',markDirty));
  document.querySelectorAll('[data-video]').forEach(el=>el.addEventListener('input',()=>{p.videos.find(v=>v.id===el.dataset.video).treatment=el.value;markDirty();}));
  document.querySelectorAll('[data-tab]').forEach(el=>el.onclick=()=>{collect();tab=el.dataset.tab;render();});
  $('#save-project').onclick=action(()=>save());
  $('#export-project').onclick=action(async()=>{await save(false); window.location.assign(`/api/projects/${current.id}/export`);});
  if ($('#develop-idea')) $('#develop-idea').onclick=action(async()=>{await save(false);$('#prompt-text').value=(await api(`/api/projects/${current.id}/prompt`)).text;$('#prompt-dialog').showModal();});
  document.querySelectorAll('[data-choose]').forEach(el=>el.onclick=action(async()=>{collect();p.selected_song_id=el.dataset.choose;if(p.stage==='idea')p.stage='song';markDirty();await save();}));
  if ($('#song-form')) $('#song-form').onsubmit=action(async event=>{
    event.preventDefault();collect();const data=new FormData(event.target);
    p.songs.push({id:crypto.randomUUID(),label:data.get('label').trim(),provider:data.get('provider'),duration_seconds:data.get('duration')?Number(data.get('duration')):null,source_url:data.get('url'),feedback:data.get('feedback'),lyrics:p.lyrics,style:p.style});
    if(p.stage==='idea')p.stage='song';markDirty();await save();
  });
  if ($('#add-video')) $('#add-video').onclick=action(async()=>{collect();p.videos.push({id:crypto.randomUUID(),song_id:p.selected_song_id,title:`${p.title} · MV ${p.videos.length+1}`,format:'16:9',treatment:p.visuals,status:'draft'});p.stage='video';markDirty();await save();});
}
async function save(showNotice=true) {
  if(saving) throw new Error('正在保存，请稍候。');
  collect(); if (!current.project.title.trim()) throw new Error('先给这个作品起个名字。');
  saving=true;
  try { current=await api(`/api/projects/${current.id}`,{method:'PUT',body:JSON.stringify({version:current.version,project:current.project})});dirty=false;await refresh();render();if(showNotice)notify('作品已保存'); }
  finally {saving=false;}
}
$('#new-project').onclick=action(async()=>{if(!canLeave())return;current=await api('/api/projects',{method:'POST',body:JSON.stringify({title:'未命名的灵感'})});dirty=false;tab='idea';await refresh();render();$('[data-field="title"]').focus();$('[data-field="title"]').select();});
$('#example-button').onclick=action(async()=>{if(!canLeave())return;const existing=records.find(r=>r.project.title==='只想要个蓝按钮');current=existing || await api('/api/import',{method:'POST',body:JSON.stringify(await api('/api/example'))});dirty=false;tab='idea';await refresh();render();notify(existing?'已打开蓝按钮案例':'已载入蓝按钮案例，可继续编辑');});
$('#search').oninput=renderLibrary;
$('#show-all').onclick=()=>{$('#search').value='';renderLibrary();};
$('#import-button').onclick=()=>{if(canLeave())$('#import-file').click();};
$('#import-file').onchange=action(async event=>{const file=event.target.files[0];if(!file)return;try{if(file.size>2000000)throw new Error('请选择小于 2 MB 的项目 JSON。');const bundle=JSON.parse(await file.text());current=await api('/api/import',{method:'POST',body:JSON.stringify(bundle)});dirty=false;tab='idea';await refresh();render();notify('已导入为独立作品');}finally{event.target.value='';}});
$('#close-dialog').onclick=()=>$('#prompt-dialog').close();
$('#copy-prompt').onclick=action(async()=>{await navigator.clipboard.writeText($('#prompt-text').value);notify('创作简报已复制');});
window.addEventListener('beforeunload',event=>{if(dirty){event.preventDefault();event.returnValue='';}});
action(async()=>{await refresh();current=records[0] || null;render();})();
