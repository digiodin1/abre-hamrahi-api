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
  const radio = new Element('radio'); radio.value='otp';
  const handlers = {};
  const document = {
    getElementById: id => elements.get(id) || null,
    querySelectorAll: selector => selector==='[data-panel]' ? panels : selector==='[data-page]' ? tabs : selector==='[name="auth-mode"]' ? [radio] : [],
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
  const calls=[];
  const path='C:\\example\\<img src=x onerror=attack>".txt';
  const fetch = async (url,options) => {
    if (!options) return {ok:true,json:async()=>structuredClone(model)};
    assert.equal(options.headers['X-Abr-Hamrahi-CSRF'],token,'POST lost its CSRF token');
    const action=url.split('/').pop(), data=JSON.parse(options.body);
    calls.push({action,data});
    if(action==='config') {
      model.config={...model.config,...data};
      const saved=structuredClone(model); delete saved.csrfToken;
      return {ok:true,json:async()=>saved};
    }
    if(action==='items') return {ok:true,json:async()=>({run:data.run,offset:data.offset,total:125,
      items:[{path,status:'error',bytes:10,error:'synthetic failure'}]})};
    return {ok:true,json:async()=>({started:true,message:'OK'})};
  };
  const context=vm.createContext({document,window:{addEventListener(){}},location:{hash:''},
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
  await elements.get('save').onclick();
  assert.equal(calls.find(c=>c.action==='config').data.remoteRoot,'NEW-COMPUTER');
  assert(elements.get('save-bar').hidden,'Save should dismiss unsaved-change bar');
  await elements.get('preview').onclick();
  assert(calls.some(c=>c.action==='preview'),'Save then preview must keep CSRF capability');
  assert.equal(panels.find(p=>!p.hidden).dataset.panel,'run');
  const rendered=elements.get('run-files').innerHTML;
  assert(rendered.includes('تلاش مجدد'));
  assert(rendered.includes('&lt;img'));
  assert(!rendered.includes('<img src=x'),'Filenames must be escaped as text and attributes');
  assert(rendered.includes('<a href="#file"'),'Every row must provide a local file link');
  const retry = new Element('',{run,path}); retry.attrs['data-retry']=''; retry.closest=()=>retry;
  handlers.click({target:retry,preventDefault(){}});
  await settle();
  assert(calls.some(c=>c.action==='retry'&&c.data.path===path&&c.data.run===run));
  elements.get('folder-path').value='C:\\example\\Original Name';
  elements.get('add-folder').onclick();
  assert(elements.get('folder-list').innerHTML.includes('Original Name'));
  await elements.get('save').onclick();
  assert.equal(calls.filter(c=>c.action==='config').at(-1).data.folders[0].name,'Original Name');
  return 'UI controller checks passed: pages, settings, save/CSRF, file links, escaping, retry and folder naming.';
}

if (process.argv?.[1] && fileURLToPath(import.meta.url) === process.argv[1]) {
  console.log(await runWebChecks());
}
