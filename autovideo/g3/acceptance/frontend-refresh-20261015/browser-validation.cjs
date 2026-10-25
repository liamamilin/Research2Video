const {spawn}=require('child_process');const fs=require('fs');
const base='http://127.0.0.1:8011',port=9358,out='/private/tmp/autovideo-ui-refresh/browser';fs.mkdirSync(out,{recursive:true});
const chrome=spawn('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',['--headless','--disable-gpu','--no-first-run','--remote-debugging-port='+port,'--remote-debugging-address=127.0.0.1','--user-data-dir='+out+'/chrome-'+Date.now(),'about:blank'],{stdio:'ignore'});
const pause=ms=>new Promise(r=>setTimeout(r,ms));let ws;
(async()=>{
 const health=await fetch(base+'/api/health');if(!health.ok)throw Error('App unavailable');
 let targets;for(let n=0;n<60;n++){try{targets=await(await fetch('http://127.0.0.1:'+port+'/json')).json();if(targets.some(t=>t.type==='page'))break;}catch{}await pause(250);}
 ws=new WebSocket(targets.find(t=>t.type==='page').webSocketDebuggerUrl);await new Promise((r,j)=>{ws.onopen=r;ws.onerror=j;});let seq=0;const pending=new Map();
 ws.onmessage=e=>{const d=JSON.parse(e.data);if(d.id&&pending.has(d.id)){const p=pending.get(d.id);pending.delete(d.id);d.error?p.reject(new Error(JSON.stringify(d.error))):p.resolve(d.result);}};
 const send=(method,params={})=>new Promise((resolve,reject)=>{const id=++seq;pending.set(id,{resolve,reject});ws.send(JSON.stringify({id,method,params}));});
 const ev=async expression=>{const r=await send('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true});if(r.exceptionDetails)throw Error(JSON.stringify(r.exceptionDetails));return r.result.value;};
 const wait=async exp=>{for(let n=0;n<120;n++){if(await ev(exp))return;await pause(250);}throw Error('Timeout '+exp);};
 await send('Page.enable');await send('Runtime.enable');
 const assert=require('assert').strict;const report={layouts:[],checks:[],requests:[],errors:[]};
 ws.addEventListener('message',e=>{const d=JSON.parse(e.data);if(d.method==='Runtime.exceptionThrown')report.errors.push(d.params.exceptionDetails);});
 const save=()=>fs.writeFileSync(out+'/report.json',JSON.stringify(report,null,2));
 const size=async(width,height)=>{await send('Emulation.setDeviceMetricsOverride',{width,height,deviceScaleFactor:1,mobile:false});await pause(150);};
 const snap=async name=>{const s=await send('Page.captureScreenshot',{format:'png',captureBeyondViewport:false});fs.writeFileSync(out+'/'+name+'.png',Buffer.from(s.data,'base64'));};
 const input=async(selector,value)=>ev(`(()=>{const e=document.querySelector(${JSON.stringify(selector)});if(!e)throw Error('Missing input');const p=e.tagName==='TEXTAREA'?HTMLTextAreaElement.prototype:HTMLInputElement.prototype;Object.getOwnPropertyDescriptor(p,'value').set.call(e,${JSON.stringify(value)});e.dispatchEvent(new Event('input',{bubbles:true}));return true;})()`);
 const select=async(selector,value)=>ev(`(()=>{const e=document.querySelector(${JSON.stringify(selector)});e.value=${JSON.stringify(value)};e.dispatchEvent(new Event('change',{bubbles:true}));return true;})()`);
 const navigate=async hash=>{await send('Page.navigate',{url:base+'/'+hash});await pause(200);};
 const layouts=async(name)=>{
  const value=await ev(`(()=>({name:${JSON.stringify(name)},width:innerWidth,height:innerHeight,scrollWidth:document.documentElement.scrollWidth,pageHeight:document.documentElement.scrollHeight,buttonTop:document.querySelector('.generate-button')?Math.round(document.querySelector('.generate-button').getBoundingClientRect().top):null,sourceFont:document.querySelector('#source')?getComputedStyle(document.querySelector('#source')).fontSize:null}))()`);
  report.layouts.push(value);assert(value.scrollWidth<=value.width+1,'Horizontal overflow '+JSON.stringify(value));save();
 };
 for(const [w,h] of [[1440,900],[1280,800],[1024,768],[390,844]]){
  await size(w,h);await navigate('#new');await wait('document.querySelectorAll(".voice-selector select option").length===13');await wait('document.querySelector(".local-status span")?.textContent.includes("已就绪")');await layouts('new-'+w);await snap('new-'+w);
 }
 await size(1440,900);await navigate('#new');await wait('!!document.querySelector("#source")');
 await ev('document.querySelector(".create-card .text-link").click();true');await wait('document.querySelector("#source").value.length>50');
 const expected=await ev('document.querySelector("#source").value');await input('#title','前端验收 · 已保存草稿');await send('Page.reload',{ignoreCache:true});await wait('document.querySelector("#title")?.value==="前端验收 · 已保存草稿"');assert.equal(await ev('document.querySelector("#source").value'),expected);report.checks.push('draft_reload_source_title');
 await ev('document.querySelector(".output-settings").open=true;true');await select('.voice-selector select','qwen_eric');await select('.output-settings .setting:last-child select','1.5');
 await ev('document.querySelector(".voice-selector button").click();true');await wait('document.querySelector(".voice-selector audio").readyState>=2');const audio=await ev('(()=>{const a=document.querySelector(".voice-selector audio");a.pause();return {duration:a.duration,error:a.error?.code||null};})()');assert(audio.duration>1&&!audio.error);report.sample=audio;
 await send('Page.reload',{ignoreCache:true});await wait('document.querySelector(".voice-selector select")?.value==="qwen_eric"');assert.equal(await ev('document.querySelector(".output-settings .setting:last-child select").value'),'1.5');report.checks.push('draft_voice_speed_reload','real_cached_accent_preview');
 await size(1280,800);await snap('new-filled-1280');
 // Upload a real local document, then restore its association across a reload.
 const doc=await send('DOM.getDocument');const file=await send('DOM.querySelector',{nodeId:doc.root.nodeId,selector:'#input-document'});await send('DOM.setFileInputFiles',{nodeId:file.nodeId,files:['/Users/milin/2026/软件开发/autovideo/g3/acceptance/input-documents-20261015/fixtures/会议记录.docx']});await wait('document.querySelector(".document-summary")!==null');
 const uploaded=await ev('JSON.parse(localStorage.getItem("autovideo.creation-draft.v1"))');assert(uploaded.inputDocument.document_id);await send('Page.reload',{ignoreCache:true});await wait('document.querySelector(".document-upload-heading strong")?.textContent==="会议记录.docx"');assert.equal(await ev('document.querySelector("#source").value'),uploaded.source);report.checks.push('real_docx_upload_draft_association_reload');save();
 await size(1440,900);await navigate('#jobs');await wait('document.querySelectorAll(".jobs-table .job-row").length===12&&document.querySelector(".jobs-table").getAttribute("aria-busy")=="false"');
 const before=await ev('fetch("/api/jobs?limit=50").then(r=>r.json())');report.actualCounts=before.summary;assert.equal(before.total,before.summary.total);
 await ev('Array.from(document.querySelectorAll(".pagination button")).find(e=>e.textContent==="下一页").click();true');await wait('document.querySelector(".pagination")?.textContent.includes("13–")');
 await ev('Array.from(document.querySelectorAll(".pagination button")).find(e=>e.textContent==="下一页").click();true');await wait('document.querySelector(".pagination")?.textContent.includes("25–")');
 const oldest=before.items[before.items.length-1];assert(await ev('Array.from(document.querySelectorAll(".jobs-table .job-row")).some(e=>e.href.includes('+JSON.stringify(oldest.job_id)+'))'));
 report.checks.push('library_all_records_pagination');await select('.library-filter select','NEEDS_INPUT');await wait('document.querySelectorAll(".jobs-table .job-row").length===1');assert.equal(await ev('document.querySelector(".jobs-table .status-badge").textContent'),'待补充');
 await select('.library-filter select','');await input('.search-field input',oldest.job_id.slice(0,8));await wait('document.querySelectorAll(".jobs-table .job-row").length===1&&document.querySelector(".jobs-table .job-row").href.includes('+JSON.stringify(oldest.job_id)+')');report.checks.push('library_status_and_identifier_search');await input('.search-field input','前端验收不存在的标题');await wait('document.querySelector(".empty-state h2")?.textContent==="没有找到对应作品"');report.checks.push('search_empty_state');await input('.search-field input','');await wait('document.querySelectorAll(".jobs-table .job-row").length===12&&document.querySelector(".jobs-table").getAttribute("aria-busy")=="false"');await snap('library-1440');await layouts('library-1440');save();
 const jid='1332041b-b1e1-4c4d-a1ae-3ae12702572b';
 await navigate('#job/'+jid);await wait('document.querySelectorAll(".scene-thumb-grid button").length===3');await wait('document.querySelector("video").readyState>=2');
 await ev('(()=>{const v=document.querySelector("video");v.muted=true;v.currentTime=2;return v.play();})()');await wait('document.querySelector("video").currentTime>2.3');await ev('document.querySelector("video").pause();true');
 report.playback=await ev('(()=>{const v=document.querySelector("video");return {width:v.videoWidth,duration:v.duration,error:v.error?.code||null,time:v.currentTime};})()');assert.equal(report.playback.width,1920);assert(!report.playback.error);report.checks.push('real_existing_mp4_playback');
 await ev('document.querySelectorAll(".scene-thumb-grid button")[1].click();true');await wait('document.querySelector(".scene-fields textarea")!==null');await wait('!document.querySelector("video").seeking');await pause(500);await snap('editor-1440');
 await input('.scene-fields textarea','界面验收 · 临时场景标题');await ev('document.querySelector("#tab-audio").click();true');await wait('document.querySelector("#tab-audio").getAttribute("aria-selected")==="true"');await ev('document.querySelector("#tab-visual").click();true');assert.equal(await ev('document.querySelector(".scene-fields textarea").value'),'界面验收 · 临时场景标题');report.checks.push('tab_switch_preserves_scene_draft');
 await ev('document.querySelector("#tab-visual").focus();true');await send('Input.dispatchKeyEvent',{type:'keyDown',key:'ArrowRight',code:'ArrowRight',windowsVirtualKeyCode:39});await wait('document.querySelector("#tab-script").getAttribute("aria-selected")==="true"');await wait('document.querySelector(".revision-text").value.length>0');report.checks.push('keyboard_tabs_and_saved_narration');
 await ev('document.querySelector("#tab-versions").click();true');await wait('document.querySelector(".version-history .current-version")!==null');assert(await ev('!!document.querySelector(".parent-version")'));report.checks.push('version_parent_navigation');
 await ev('document.querySelector(".technical-details").open=true;true');assert(!await ev('Array.from(document.querySelectorAll(".technical-details .stage-item small")).some(e=>["进行中","待处理"].includes(e.textContent))'));await ev('document.querySelector(".technical-details").open=false;document.querySelector("#tab-audio").click();true');await snap('audio-1440');report.checks.push('completed_revision_status_consistency');
 // Capture submit requests and return a deliberate validation error. No video jobs are created.
 await send('Fetch.enable',{patterns:[{urlPattern:'*/api/jobs*',requestStage:'Request'}]});
 ws.addEventListener('message',e=>{const d=JSON.parse(e.data);if(d.method!=='Fetch.requestPaused')return;const p=d.params;if(p.request.method==='POST'){
  report.requests.push({url:p.request.url,body:JSON.parse(p.request.postData||'{}')});
  void send('Fetch.fulfillRequest',{requestId:p.requestId,responseCode:422,responseHeaders:[{name:'Content-Type',value:'application/json'}],body:Buffer.from(JSON.stringify({detail:{message:'界面验收：请求已核对，未启动制作。'}})).toString('base64')});
 }else void send('Fetch.continueRequest',{requestId:p.requestId});});
 await select('.media-editor .setting:last-child select','2');await ev('document.querySelector(".media-editor form").requestSubmit();true');await wait('document.querySelector(".media-editor .revision-error")?.textContent.includes("界面验收")');assert.equal(report.requests.at(-1).body.video_speed,2);
 await ev('document.querySelector("#tab-visual").click();document.querySelector(".scene-revision-editor form").requestSubmit();true');await wait('document.querySelector(".scene-revision-editor .revision-error")?.textContent.includes("界面验收")');assert.equal(report.requests.at(-1).body.scene_id,'c1_s2');assert.equal(report.requests.at(-1).body.changes.title,'界面验收 · 临时场景标题');
 await ev('document.querySelector("#tab-script").click();true');await input('.revision-text','这是前端请求验收用的讲稿。保留原版，按当前讲稿重新组织画面。');await ev('document.querySelector(".revision-editor form").requestSubmit();true');await wait('document.querySelector(".revision-editor .revision-error")?.textContent.includes("界面验收")');assert.equal(report.requests.at(-1).body.mode,'narration');
 await navigate('#new');await wait('document.querySelector(".generate-button")!==null');await ev('document.querySelector("#create-video").requestSubmit();true');await wait('document.querySelector(".alert.error")?.textContent.includes("界面验收")');const created=report.requests.at(-1).body;assert.equal(created.input_document_id,uploaded.inputDocument.document_id);assert.equal(created.source_text,uploaded.source);assert.equal(created.video_speed,1.5);assert.equal(created.voice_id,'qwen_eric');report.checks.push('create_visual_narration_media_submit_payloads_and_error_feedback');await send('Fetch.disable');save();
 for(const [name,id] of [['long','a4ae8c5f-81de-42ab-8144-8bb7e9b3edf7'],['blocked','6b2fc6b3-0ac1-436c-a8d6-2ed5f381df36'],['legacy','877e83a6-b9a7-41b2-a433-22a847a4e97c']]){
  await navigate('#job/'+id);await wait('document.querySelector(".detail-heading")!==null');await pause(400);
  if(name==='long'){await wait('document.querySelectorAll(".scene-thumb-grid button").length===35');await layouts(name+'-1440');assert(await ev('document.documentElement.scrollHeight<2300'));}
  if(name==='blocked'){assert.equal(await ev('document.querySelector(".detail-meta .status-badge").textContent'),'待补充');assert(await ev('!!document.querySelector(".revision-text")'));}
  if(name==='legacy'){await wait('document.querySelectorAll(".scene-preview-item").length===3');report.checks.push('legacy_draw_things_background_editor_preserved');}
  await snap(name+'-1440');
 }
 for(const [w,h] of [[1280,800],[1024,768],[390,844]]){
  await size(w,h);await navigate('#job/'+jid);await wait('document.querySelectorAll(".scene-thumb-grid button").length===3');await wait('document.querySelector("video").poster.length>0');await pause(300);await layouts('detail-'+w);await snap('detail-'+w);
 }
 const after=await ev('fetch("/api/jobs?limit=1").then(r=>r.json())');assert.equal(after.summary.total,before.summary.total);report.checks.push('no_video_jobs_created','desktop_and_narrow_layouts_no_overflow','long_scene_list_bounded','material_clarification_state');assert.equal(report.errors.length,0);report.status='PASS';save();console.log(JSON.stringify({status:report.status,checks:report.checks,layouts:report.layouts,requests:report.requests.length,errors:report.errors},null,2));
})().catch(e=>{console.error(e);process.exitCode=1;}).finally(()=>{ws?.close();chrome.kill();});
