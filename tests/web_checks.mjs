// Dependency-free UI controller checks. This is a DOM test double, not a visual browser test.
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import vm from 'node:vm';
import {fileURLToPath} from 'node:url';

export async function runWebChecks() {
  const html = await readFile(new URL('../src/hamrahi_backup/web/backup.html', import.meta.url), 'utf8');
  const source = await readFile(new URL('../src/hamrahi_backup/web/backup.js', import.meta.url), 'utf8');
  const elements = new Map();
  class Element {
    constructor(id, dataset={}) {
      Object.assign(this, {id, dataset, hidden:false, disabled:false, value:'', checked:false,
        textContent:'', innerHTML:'', listeners:{}, attrs:{}});
      this.classList = {toggle(){}};
    }
    addEventListener(name, fn) { this.listeners[name] = fn; }
    setAttribute(name,value) { this.attrs[name] = value; }
    removeAttribute(name) { delete this.attrs[name]; }
    hasAttribute(name) { return Object.hasOwn(this.attrs,name); }
    replaceChildren(...children) { this.children = children; }
  }
  for (const match of html.matchAll(/\bid="([^"]+)"/g)) {
    assert(!elements.has(match[1]), 'Duplicate element ID');
    elements.set(match[1], new Element(match[1]));
  }
  const panels = [...html.matchAll(/data-panel="([^"]+)"/g)].map(m=>new Element('',{panel:m[1]}));
  const tabs = [...html.matchAll(/data-page="([^"]+)"/g)].map(m=>new Element('',{page:m[1]}));
  assert.equal(panels.length,5);
  assert(!html.includes('folder-name'));
  assert(html.includes('id="max-size" type="number" min="0"'));
  const radio = new Element('radio'); radio.value='otp';
  const handlers = {};
  const liveMode = new Element('live-mode',{folderMode:'0'}), liveTime = new Element('live-time',{folderTime:'0'});
  liveMode.matches=selector=>selector==='[data-folder-mode]';
  liveTime.matches=selector=>selector==='[data-folder-time]';
  const document = {
    getElementById: id => elements.get(id) || null,
    querySelectorAll: selector => selector==='[data-panel]' ? panels : selector==='[data-page]' ? tabs : selector==='[name="auth-mode"]' ? [radio] : selector.includes('[data-folder-mode]') ? [liveMode,liveTime] : [],
    querySelector: selector => selector==='[name="auth-mode"]:checked' ? radio : null,
    createElement: () => new Element(''),
    addEventListener: (name,fn) => handlers[name]=fn,
  };
  const run = 'a'.repeat(32), token = 'synthetic-csrf';
  const result = {id:run,at:'2026-11-05T12:00:00Z',preview:true,uploaded:1,unchanged:0,
    bytes:10,excluded:0,errors:['synthetic error'],ok:false,samples:[],files:1};
  let model = {csrfToken:token,config:{remoteRoot:'COMPUTER',intervalMinutes:60,maxFileMiB:1024,
    settleSeconds:0,automatic:false,automaticPaused:false,syncOnChange:true,verifyUploads:true,
    startupEnabled:false,exclude:['.env'],folders:[]},authenticated:true,history:[result],
    startup:{supported:true,registered:false,error:null},
    state:{busy:false,phase:'آماده',connection:null,lastTest:null,uploaded:0,skipped:0,bytes:0,
      errors:[],steps:[],lastResult:result,runId:run}};
  const calls=[], confirmations=[];
  let confirmExit=false, rejectSchedule=false;
  const path='C:\\example\\<img src=x onerror=attack>".txt';
  const fetch = async (url,options) => {
    if (!options) return {ok:true,json:async()=>structuredClone(model)};
    assert.equal(options.headers['X-Abr-Hamrahi-CSRF'],token,'POST lost its CSRF token');
    const action=url.split('/').pop(), data=JSON.parse(options.body);
    calls.push({action,data});
    if(action==='folder-schedule') {
      if(rejectSchedule) return {ok:false,json:async()=>({error:'synthetic schedule failure'})};
      Object.assign(model.config.folders.find(folder=>folder.id===data.job),{syncMode:data.syncMode,dailyTime:data.dailyTime});
      return {ok:true,json:async()=>({saved:true})};
    }
    if(action==='config') {
      model.config={...model.config,...data};
      const saved=structuredClone(model); delete saved.csrfToken;
      return {ok:true,json:async()=>saved};
    }
    if(action==='items') return {ok:true,json:async()=>({run:data.run,offset:data.offset,total:125,
      items:[{path,status:'error',bytes:10,error:'synthetic failure'}]})};
    if(action==='browse') return {ok:true,json:async()=>({path:'C:\\Selected while uploading',cancelled:false})};
    return {ok:true,json:async()=>({started:true,message:'OK'})};
  };
  const context=vm.createContext({document,window:{addEventListener(){},
    confirm: text=>{confirmations.push(text);return confirmExit;}},location:{hash:''},
    fetch,Intl,Date,structuredClone,setTimeout:()=>0,clearTimeout(){},setInterval(){}});
  new vm.Script(source,{filename:'backup.js'}).runInContext(context);
  const settle=async()=>{for(let i=0;i<5;i++)await new Promise(setImmediate);};
  await settle();
  assert.equal(panels.filter(p=>!p.hidden).length,1,'Exactly one page should be visible');
  tabs.find(tab=>tab.dataset.page==='settings').onclick();
  assert.equal(panels.find(p=>!p.hidden).dataset.panel,'settings');
  assert(elements.get('sync-on-change').disabled,'Change trigger depends on automatic mode');
  elements.get('automatic').checked=true;
  elements.get('automatic').listeners.input();
  assert(!elements.get('sync-on-change').disabled);
  elements.get('remote-root').value='NEW-COMPUTER';
  elements.get('workers').value='3';
  elements.get('workers').listeners.input();
  await elements.get('save').onclick();
  assert.equal(calls.find(c=>c.action==='config').data.remoteRoot,'NEW-COMPUTER');
  assert.equal(calls.find(c=>c.action==='config').data.uploadWorkers,3);
  assert(elements.get('save-bar').hidden,'Save should dismiss unsaved-change bar');
  await elements.get('preview').onclick();
  assert(calls.some(c=>c.action==='preview'),'Save then preview must keep CSRF capability');
  assert.equal(panels.find(p=>!p.hidden).dataset.panel,'run');
  const rendered=elements.get('run-files').innerHTML;
  assert(rendered.includes('تلاش مجدد'));
  assert(rendered.includes('synthetic failure'), 'Per-file failure reason must be visible');
  assert(rendered.includes('بایت'), 'Size units must be readable');
  assert(rendered.includes('&lt;img'));
  assert(!rendered.includes('<img src=x'),'Filenames must be escaped as text and attributes');
  assert(rendered.includes('<a href="#file"'),'Every row must provide a local file link');
  const retry = new Element('',{run,path}); retry.attrs['data-retry']=''; retry.closest=selector=>selector.includes('[data-retry]')?retry:null;
  handlers.click({target:retry,preventDefault(){}});
  await settle();
  assert(calls.some(c=>c.action==='retry'&&c.data.path===path&&c.data.run===run));
  elements.get('folder-path').value='C:\\example\\Original Name';
  elements.get('add-folder').onclick();
  assert(elements.get('folder-list').innerHTML.includes('Original Name'));
  await elements.get('save').onclick();
  assert.equal(calls.filter(c=>c.action==='config').at(-1).data.folders[0].name,'Original Name');
  assert(html.includes('href="/fonts.css"'));
  assert(elements.get('folder-list').innerHTML.includes('data-folder-mode="0"'));
  const modeInput = {value:'daily',dataset:{folderMode:'0'},matches:selector=>selector==='[data-folder-mode]'};
  elements.get('folder-list').onchange({target:modeInput});
  assert(elements.get('folder-list').innerHTML.includes('ساعت محلی'));
  const timeInput = {value:'23:45',dataset:{folderTime:'0'},matches:selector=>selector==='[data-folder-time]'};
  elements.get('folder-list').onchange({target:timeInput});
  const beforeEditingPoll = elements.get('folder-list').innerHTML;
  document.activeElement = timeInput;
  timeInput.matches = selector=>selector.includes('[data-folder-time]');
  await vm.runInContext('poll()', context);
  assert.equal(elements.get('folder-list').innerHTML,beforeEditingPoll,'Polling must preserve a focused schedule field');
  document.activeElement = null;
  await elements.get('save').onclick();
  const scheduledFolder = calls.filter(call=>call.action==='config').at(-1).data.folders[0];
  assert.equal(scheduledFolder.syncMode,'daily');
  assert.equal(scheduledFolder.dailyTime,'23:45');
  model.state.folderSchedules = {[scheduledFolder.id]:{mode:'daily',dailyTime:'23:45',nextRun:1794683700}};
  await vm.runInContext('poll()', context);
  assert(elements.get('folder-list').innerHTML.includes('نوبت بعد:'));
  model.state.busy = true;
  model.state.transfer = {sentBytes:2048,activeFiles:4,bytesPerSecond:1024};
  model.state.progress = {total:10, done:3, percent:30, failed:1, waiting:7};
  model.state.recovery = {attempt:1, retryAt:1800000000, enabled:true};
  await vm.runInContext('poll()', context);
  assert(elements.get('workers').disabled);
  model.config.folders[0].id='f'.repeat(12);
  vm.runInContext("folders[0].id='f'.repeat(12)",context);
  await vm.runInContext('poll()', context);
  assert(!liveMode.disabled && !liveTime.disabled,'Folder schedule controls must remain usable during sync');
  liveMode.value='instant';
  await elements.get('folder-list').onchange({target:liveMode});
  assert.equal(calls.filter(call=>call.action==='folder-schedule').at(-1).data.syncMode,'instant');
  assert(model.state.busy,'Saving schedule must not stop the current transfer');
  rejectSchedule=true; liveMode.value='daily';
  await elements.get('folder-list').onchange({target:liveMode});
  assert(elements.get('message').textContent.includes('synthetic schedule failure'));
  assert.equal(vm.runInContext('folders[0].syncMode',context),'instant','Failed save must restore persisted schedule');
  rejectSchedule=false;
  assert(elements.get('run-counts').innerHTML.includes('سرعت متوسط'));
  assert(elements.get('run-counts').innerHTML.includes('کیلوبایت'));
  assert(!Object.hasOwn(elements.get('overall-progress').attrs,'value'));
  assert(!Object.hasOwn(elements.get('activity-gauge').attrs,'aria-valuenow'));
  model.syncMatch={percent:99.99,complete:false,known:true,checking:true,verifiedFiles:9,totalFiles:10,totalBytes:100000,matchedBytes:99990,remainingBytes:10,assurance:'size-and-identity'};
  await vm.runInContext('poll()', context);
  assert.equal(elements.get('activity-gauge').attrs['aria-valuenow'],99.99);
  assert(!elements.get('activity-value').textContent.includes('۱۰۰'));
  assert(elements.get('activity-detail').textContent.includes('محلی:'));
  assert(elements.get('activity-detail').textContent.includes('ابر:'));
  model.syncMatch={percent:100,complete:true,known:true,checking:false,verifiedFiles:10,totalFiles:10};
  await vm.runInContext('poll()', context);
  assert.equal(elements.get('activity-gauge').attrs['aria-valuenow'],100);
  assert.equal(elements.get('activity-label').textContent,'درصد تطابق حجم ابر و محلی');
  model.state.connection={total:1024,used:256,checkedAt:'2026-11-05T12:00:00Z'};
  await vm.runInContext('poll()', context);
  assert.equal(elements.get('capacity-gauge').attrs['aria-valuenow'],75);
  assert(elements.get('capacity-value').textContent.includes('بایت'));
  assert(!elements.get('check-connection').disabled,'Quota refresh must work while uploading');
  const configurationsBeforeQuota = calls.filter(call=>call.action==='config').length;
  const callsBeforeQuota = calls.length;
  await elements.get('check-connection').onclick();
  assert(calls.slice(callsBeforeQuota).some(call=>call.action==='connection'));
  assert.equal(calls.filter(call=>call.action==='config').length,configurationsBeforeQuota);
  model.state.quotaRefreshing=true;
  await vm.runInContext('poll()', context);
  assert(elements.get('check-connection').disabled);
  assert(elements.get('capacity-detail').textContent.includes('در حال دریافت ظرفیت'));
  model.state.quotaRefreshing=false;
  model.state.quotaStale=true;
  model.state.quotaError='دریافت ظرفیت تازه ناموفق بود';
  await vm.runInContext('poll()', context);
  assert.equal(elements.get('capacity-gauge').attrs['aria-valuenow'],75);
  assert(elements.get('capacity-detail').textContent.includes(model.state.quotaError));
  model.state.quotaError=null;
  await vm.runInContext('poll()', context);
  assert(elements.get('capacity-detail').textContent.includes('عدد آخرین بررسی'));
  model.state.connection.used=2048;
  await vm.runInContext('poll()', context);
  assert.equal(elements.get('capacity-gauge').attrs['aria-valuenow'],0);
  model.state.connection={total:10*1024**3,used:7*1024**3+12345678,checkedAt:'2026-11-05T12:00:45Z'};
  model.state.quotaStale=false;
  await vm.runInContext('poll()', context);
  assert.equal(elements.get('capacity-value').textContent,'۲٫۹۹ گیگابایت');
  assert.equal(elements.get('capacity-percent').textContent,'۲۹٫۸۹٪ آزاد');
  assert(elements.get('capacity-detail').textContent.includes('مصرف‌شده: ۷٫۰۱ گیگابایت از ۱۰ گیگابایت (۷۰٫۱۱٪)'));
  assert(elements.get('quota').textContent.startsWith('مصرف‌شده: ۷٫۰۱'));
  assert.equal(elements.get('capacity-value').title,new Intl.NumberFormat('fa-IR').format(3*1024**3-12345678)+' بایت آزاد');
  assert(Math.abs(elements.get('capacity-gauge').attrs['aria-valuenow']-(3*1024**3-12345678)/(10*1024**3)*100)<1e-10);
  model.folderStats={[model.config.folders[0].id]:{localBytes:2048,cloudBytes:1024,verifiedFiles:1,localFiles:2,issues:1,excluded:3,stale:false,complete:false}};
  await vm.runInContext('poll()', context);
  assert(elements.get('folder-list').innerHTML.includes('محلی:'));
  assert(elements.get('folder-list').innerHTML.includes('ابر:'));
  assert(elements.get('folder-list').innerHTML.includes('نیازمند رسیدگی'));
  assert(!elements.get('browse').disabled,'Folder chooser must stay usable during uploads');
  assert(!elements.get('folder-path').disabled);
  assert(elements.get('save').disabled,'Configuration changes must wait for the current run');
  await elements.get('browse').onclick();
  assert.equal(elements.get('folder-path').value,'C:\\Selected while uploading');
  elements.get('add-folder').onclick();
  assert(elements.get('folder-list').innerHTML.includes('Selected while uploading'));
  assert(elements.get('save').disabled);
  assert(elements.get('progress-detail').textContent.includes('در انتظار ادامه'));
  assert(elements.get('recovery-state').textContent.includes('تلاش مجدد خودکار'));
  assert(elements.get('errors').children.some(p=>p.textContent.includes('synthetic error')), 'Previous failure must remain visible during recovery');
  model.state.busy = false;
  await vm.runInContext('poll()', context);
  elements.get('remote-root').value='UNSAVED';
  elements.get('remote-root').listeners.input();
  const savesBeforeExit=calls.filter(c=>c.action==='config').length;
  await elements.get('shutdown').onclick();
  assert(!calls.some(c=>c.action==='shutdown'),'Cancelling confirmation must keep the app running');
  assert(confirmations[0].includes('ذخیره‌نشده'),'Exit must warn about unsaved changes');
  assert(!elements.get('save-bar').hidden);
  confirmExit=true;
  await elements.get('shutdown').onclick();
  assert.equal(calls.filter(c=>c.action==='shutdown').length,1);
  assert.equal(calls.at(-1).data.confirm,true);
  assert.equal(calls.filter(c=>c.action==='config').length,savesBeforeExit,'Exit must not silently save edits');
  assert(!elements.get('exit-state').hidden,'Show a persistent exit acknowledgement');
  assert(elements.get('shutdown').disabled);
  assert(elements.get('save').disabled);
  assert(elements.get('save-bar').hidden);
  return 'UI controller checks passed: pages, settings, save/CSRF, file links, escaping, retry, folder naming and confirmed shutdown.';
}

if (process.argv?.[1] && fileURLToPath(import.meta.url) === process.argv[1]) {
  console.log(await runWebChecks());
}
