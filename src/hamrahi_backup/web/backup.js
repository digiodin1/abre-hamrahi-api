'use strict';
const $ = id => document.getElementById(id);
const nf = new Intl.NumberFormat('fa-IR');
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const size = n => {
  if (!n || n < 0) return '۰ بایت';
  const units = ['بایت','کیلوبایت','مگابایت','گیگابایت','ترابایت'];
  const i = Math.min(4, Math.floor(Math.log(n) / Math.log(1024)));
  return nf.format(Math.round(n / 1024 ** i * 10) / 10) + ' ' + units[i];
};
const date = v => v ? new Date(v).toLocaleString('fa-IR', {dateStyle:'short', timeStyle:'short'}) : '—';
let model = null, folders = [], dirty = false, initialized = false, captcha = {};
let activeRequests = 0, lastBusy = false, messageTimer, polling = false, closing = false;
const views = {run: {run:null, offset:0, sequence:0}, report: {run:null, offset:0, sequence:0}};
let folderView = {job:null, offset:0, sequence:0};

function showPage(page) {
  if (!['account','folders','settings','run','reports'].includes(page)) page = 'account';
  document.querySelectorAll('[data-panel]').forEach(el => el.hidden = el.dataset.panel !== page);
  document.querySelectorAll('[data-page]').forEach(el => {
    const active = el.dataset.page === page;
    el.classList.toggle('active', active);
    if (active) el.setAttribute('aria-current','page'); else el.removeAttribute('aria-current');
  });
  if (location.hash !== '#' + page) location.hash = page;
  if (page === 'reports' && views.report.run) loadItems('report').catch(error => message(error.message,true));
}
function message(text, error=false) {
  if (closing) return;
  clearTimeout(messageTimer);
  $('message').textContent = text;
  $('message').className = error ? 'error' : '';
  $('message').hidden = false;
  messageTimer = setTimeout(() => $('message').hidden = true, error ? 20000 : 8000);
}
function changed() {
  dirty = true;
  $('save-bar').hidden = false;
  $('save-state').textContent = model?.state.busy ? 'پس از پایان کار جاری تنظیمات را ذخیره کنید.' : 'تغییرات را ذخیره کنید';
  renderControls();
}
async function api(action, data={}) {
  const response = await fetch('/api/backup/' + action, {method:'POST', headers:{
    'Content-Type':'application/json', 'X-Abr-Hamrahi-CSRF':model?.csrfToken || ''
  }, body:JSON.stringify(data)});
  let result;
  try { result = await response.json(); }
  catch { throw Error(`پاسخ پنل قابل خواندن نیست (HTTP ${response.status})؛ دوباره تلاش کنید.`); }
  if (!response.ok) {
    if (result.captcha) $('captcha-details').open = true;
    throw Error(result.error || `درخواست ناموفق بود (HTTP ${response.status}).`);
  }
  return result;
}
async function perform(button, work) {
  if (button.disabled || activeRequests) return;
  activeRequests++;
  renderControls();
  try { await work(); } catch (error) { message(error.message, true); }
  finally { activeRequests--; if (!closing) await poll(); renderControls(); }
}
function renderControls() {
  const busy = !!model?.state.busy, pending = activeRequests > 0 || closing, auth = !!model?.authenticated;
  $('shutdown').disabled = !initialized || pending;
  for (const id of ['preview','save','resume-sync','login','send-code','captcha-get']) $(id).disabled = !initialized || busy || pending;
  for (const id of ['browse','add-folder','folder-path']) $(id).disabled = !initialized || pending;
  for (const id of ['sync','audit','repair-audit','test','logout']) $(id).disabled = busy || pending || !auth;
  $('check-connection').disabled = pending || !auth || !!model?.state.quotaRefreshing;
  for (const id of ['remote-root','interval','max-size','settle','workers','automatic','verify','excludes']) $(id).disabled = !initialized || busy || pending;
  $('sync-on-change').disabled = busy || pending || !$('automatic').checked;
  $('startup').disabled = busy || pending || !model?.startup?.supported;
  $('pause-sync').hidden = !model?.config.automatic || !!model?.config.automaticPaused;
  $('resume-sync').hidden = !model?.config.automatic || !model?.config.automaticPaused;
  $('pause-sync').disabled = pending;
  $('cancel').hidden = !busy;
  $('cancel').disabled = pending;
  $('busy-tag').textContent = closing ? 'درخواست خروج ثبت شد' : busy ? 'در حال اجرا' : 'آماده';
  $('status-dot').classList.toggle('working', busy && !closing);
  $('nav-spinner').hidden = !busy || closing;
  document.querySelectorAll('[data-remove],[data-folder-enable],[data-folder-mode],[data-folder-time],[data-retry]').forEach(el => el.disabled = busy || pending);
  document.querySelectorAll('[data-folder-mode],[data-folder-time]').forEach(el => el.disabled = !initialized || pending);
}
function renderFolders(force=false) {
  if (!force && document.activeElement?.matches('[data-folder-mode],[data-folder-time]')) return;
  $('folder-list').innerHTML = folders.length ? folders.map((f,i) => `
    <div class="folder-row"><input type="checkbox" data-folder-enable="${i}" ${f.enabled?'checked':''} aria-label="فعال‌بودن ${esc(f.name)}"><span class="folder-icon">▱</span><div class="folder-info"><div class="folder-heading"><strong>${esc(f.name)}</strong>${folderSizeLabel(f)}</div><p dir="ltr">${esc(f.path)}</p>${folderAuditLabel(f)}${folderScheduleLabel(f,i)}</div>${f.id?`<button data-folder-files="${esc(f.id)}">فایل‌ها و حجم</button>`:''}<button data-remove="${i}">حذف</button></div>`).join('') : '<div class="empty">هنوز پوشه‌ای انتخاب نشده است</div>';
}
function folderScheduleLabel(folder, index) {
  const daily = folder.syncMode === 'daily', schedule = model?.state.folderSchedules?.[folder.id];
  const next = daily && schedule?.nextRun && schedule.dailyTime === folder.dailyTime ? `نوبت بعد: ${date(schedule.nextRun*1000)}` : '';
  return `<div class="folder-schedule"><label>زمان‌بندی<select data-folder-mode="${index}" aria-label="زمان‌بندی ${esc(folder.name)}"><option value="instant" ${daily?'':'selected'}>سینک آنی</option><option value="daily" ${daily?'selected':''}>سینک روزانه</option></select></label><label ${daily?'':'hidden'}>ساعت محلی<input type="time" data-folder-time="${index}" value="${esc(folder.dailyTime || '02:00')}" required aria-label="ساعت سینک ${esc(folder.name)}"></label><span class="next-run">${esc(next)}</span></div>`;
}
function folderSizeLabel(folder) {
  const stats = model?.folderStats?.[folder.id];
  return stats ? `<span class="folder-size">محلی: <b>${stats.sizeUnreadable?'حداقل ':''}${size(stats.localTotalBytes ?? stats.localBytes)}</b> · ابر: <b>${size(stats.cloudBytes)}</b></span>` : '<span class="muted">حجم هنوز بررسی نشده</span>';
}
function folderAuditLabel(folder) {
  const stats = model?.folderStats?.[folder.id];
  if (!stats) return '';
  const label = stats.stale ? 'نیاز به بررسی دوباره' : stats.complete ? '✓ تطابق فایل‌های مجاز تأیید شد' : `✕ ${nf.format(stats.issues)} مورد نیازمند رسیدگی`;
  return `<p class="${stats.stale?'muted':stats.complete?'success':'bad'}">${label} · ${nf.format(stats.verifiedFiles)} از ${nf.format(stats.localFiles)} فایل · ${date(stats.checkedAt)}</p><p class="muted">حجم فایل‌های مجاز: ${size(stats.localBytes)} · ${nf.format(stats.excluded)} مسیر مستثنا${stats.cloudExtras ? ` · ${nf.format(stats.cloudExtras)} فایل اضافه در ابر، حفظ‌شده` : ''}${stats.sizeUnreadable ? ` · حجم ${nf.format(stats.sizeUnreadable)} مسیر قابل خواندن نبود` : ''}${stats.contentVerifiedAt ? ` · قدیمی‌ترین تأیید محتوای نسخه‌های فعلی: ${date(stats.contentVerifiedAt*1000)}` : ''}</p>`;
}
function setGauge(name, percent) {
  const known = Number.isFinite(percent), value = known ? Math.max(0,Math.min(100,percent)) : 0;
  $(name+'-arc').setAttribute('stroke-dasharray', `${value * .75} 100`);
  $(name+'-needle').setAttribute('transform', `rotate(${-135 + value * 2.7} 100 100)`);
  if (known) $(name+'-gauge').setAttribute('aria-valuenow',value); else $(name+'-gauge').removeAttribute('aria-valuenow');
}
function renderGauges(state) {
  const connection = state.connection, known = connection && connection.total > 0;
  const remaining = known ? Math.max(0,connection.total-connection.used) : null;
  const freePercent = known ? remaining/connection.total*100 : null;
  setGauge('capacity',freePercent);
  $('capacity-value').textContent = known ? size(remaining) : '—';
  $('capacity-percent').textContent = known ? nf.format(Math.round(freePercent*10)/10)+'٪ آزاد' : 'ظرفیت بررسی نشده';
  const freshness = state.quotaError || (state.quotaRefreshing ? 'در حال دریافت ظرفیت' : state.quotaStale ? 'عدد آخرین بررسی؛ نیاز به به‌روزرسانی' : '');
  $('capacity-detail').textContent = (known ? `مصرف‌شده: ${size(connection.used)} از ${size(connection.total)} · بررسی: ${date(connection.checkedAt)}` : 'ظرفیت هنوز دریافت نشده.') + (freshness ? ` · ${freshness}` : '');
  const match = model.syncMatch || {}, percent = Number.isFinite(match.percent) ? match.percent : null;
  setGauge('activity',percent);
  $('activity-value').textContent = Number.isFinite(percent) ? nf.format(match.complete ? 100 : Math.min(99.99, Math.round(percent*100)/100))+'٪' : '—';
  $('activity-label').textContent = 'درصد تطابق حجم ابر و محلی';
  $('activity-detail').textContent = `کل محلی: ${size(match.totalBytes||0)} · ابر: ${size(match.cloudBytes||0)} · اختلاف: ${size(Math.abs((match.totalBytes||0)-(match.cloudBytes||0)))} · خارج از انتخاب سینک: ${size(match.excludedBytes||0)}${match.stale ? ' · آخرین اندازه‌گیری' : ''}${match.checkedAt ? ` · ${date(match.checkedAt)}` : ''}`;
  $('activity-gauge').classList.toggle('gauge-error',!match.complete && !!state.errors?.length);
}
function populate(config) {
  folders = structuredClone(config.folders);
  for (const [id,key] of [['remote-root','remoteRoot'],['interval','intervalMinutes'],['max-size','maxFileMiB'],['settle','settleSeconds'],['workers','uploadWorkers']]) $(id).value = config[key] ?? (key==='uploadWorkers' ? 8 : '');
  for (const [id,key] of [['automatic','automatic'],['sync-on-change','syncOnChange'],['verify','verifyUploads'],['startup','startupEnabled']]) $(id).checked = !!config[key];
  $('excludes').value = config.exclude.join('\n');
  renderFolders();
}
function settings() {
  return {remoteRoot:$('remote-root').value, syncBehavior:'mirror', intervalMinutes:Number($('interval').value),
    maxFileMiB:Number($('max-size').value), settleSeconds:Number($('settle').value),
    uploadWorkers:Number($('workers').value),
    automatic:$('automatic').checked, automaticPaused:!!model?.config.automaticPaused,
    debounceSeconds:model?.config.debounceSeconds || 5, syncOnChange:$('sync-on-change').checked,
    verifyUploads:$('verify').checked, startupEnabled:$('startup').checked,
    exclude:$('excludes').value.split('\n').map(s=>s.trim()).filter(Boolean), folders};
}
async function save() {
  const result = await api('config', settings());
  model = {...result, csrfToken:model.csrfToken};
  populate(result.config);
  dirty = false;
  $('save-bar').hidden = true;
  message('تنظیمات ذخیره شد.');
}
function errorParagraphs(container, errors) {
  container.replaceChildren(...errors.map(text => {
    const p = document.createElement('p'); p.textContent = text; return p;
  }));
}
function renderProgress(s) {
  $('phase').textContent = s.phase;
  $('current').textContent = s.current || '';
  const icon = status => status === 'ok' ? '✓' : status === 'error' ? '✕' : status === 'running' ? '<span class="spinner"></span>' : '○';
  $('progress-steps').innerHTML = (s.steps || []).map(step => `<li class="stage ${esc(step.status)}"><span>${icon(step.status)}</span>${esc(step.label)}</li>`).join('');
  const stages = [['snapshot','نسخهٔ موقت'], ...(s.action === 'audit' ? [['verify','بررسی صحت']] : s.action !== 'preview' ? [['upload','ارسال'], ...(model.config.verifyUploads ? [['verify','بررسی صحت']] : [])] : []), ['record','ثبت']];
  const current = stages.findIndex(step => step[0] === s.fileStage);
  $('file-stages').innerHTML = s.busy && current >= 0 ? stages.map(([key,label], i) => `<span class="${i<current?'success':''}">${i<current?'✓ ':i===current?'◌ ':''}${label}</span>`).join(' ← ') : '';
  const p = s.progress || {}, match = model.syncMatch || {}, known = Number.isFinite(match.percent), percent = match.percent;
  const bar = $('overall-progress');
  bar.max = 100;
  if (known) bar.value = percent; else bar.removeAttribute('value');
  bar.hidden = !s.busy && !known;
  bar.classList.toggle('has-errors', !!p.failed || !!p.waiting);
  $('progress-percent').textContent = known ? nf.format(percent) + '٪' : s.busy ? 'در حال آماده‌سازی…' : '';
  $('progress-detail').textContent = (known ? `حجم ابر ${size(match.cloudBytes ?? match.matchedBytes ?? 0)} · محلی ${size(match.totalBytes||0)} · باقی‌مانده ${size(match.remainingBytes||0)}` : '') + (p.failed ? ` · ${nf.format(p.failed)} خطا` : '') + (p.waiting ? ` · ${nf.format(p.waiting)} در انتظار ادامه` : '');
  const transfer = s.transfer || {};
  $('run-counts').innerHTML = `<span>ارسال‌شده: <b>${nf.format(s.uploaded)}</b></span><span>بدون تغییر: <b>${nf.format(s.skipped)}</b></span><span>حجم ثبت‌شده: <b>${size(s.bytes)}</b></span>${s.busy ? `<span>در حال ارسال: <b>${nf.format(transfer.activeFiles||0)} فایل</b></span><span>سرعت متوسط: <b>${size(transfer.bytesPerSecond||0)} / ثانیه</b></span><span>دادهٔ ارسال‌شدهٔ این اجرا: <b>${size(transfer.sentBytes||0)}</b></span>` : ''}`;
  const recovery = s.recovery || {};
  $('recovery-state').textContent = recovery.attempt ? (recovery.enabled ? (s.busy ? 'تلاش مجدد خودکار در حال اجراست؛ فایل‌های موفق قبلی دوباره بارگذاری نمی‌شوند.' : `ادامهٔ خودکار پس از خطا: ${date(recovery.retryAt * 1000)} · تلاش ${nf.format(recovery.attempt)}`) : 'ادامهٔ خودکار متوقف است؛ برای ادامه «شروع / ادامهٔ پشتیبان‌گیری» را بزنید.') : '';
}
function render() {
  const s = model.state, c = model.config, connection = s.connection;
  $('auth-form').hidden = model.authenticated;
  $('connected-actions').hidden = !model.authenticated;
  $('auth-tag').textContent = model.authenticated ? 'متصل' : 'وارد نشده‌اید';
  $('connection').textContent = connection ? 'متصل' : model.authenticated ? 'حساب آماده' : 'نیاز به ورود';
  $('quota').textContent = connection ? size(connection.used) + ' از ' + size(connection.total) : '—';
  $('folder-count').textContent = nf.format(c.folders.filter(f=>f.enabled).length);
  $('test-state').textContent = s.lastTest ? (s.lastTest.ok?'موفق':'ناموفق') : 'انجام نشده';
  $('test-state').className = s.lastTest ? (s.lastTest.ok?'success':'bad') : '';
  $('test-time').textContent = s.lastTest ? date(s.lastTest.at) : '—';
  const daily = c.folders.some(folder=>folder.enabled && folder.syncMode==='daily');
  const instant = c.folders.some(folder=>folder.enabled && folder.syncMode!=='daily');
  $('schedule-state').textContent = c.automatic ? (c.automaticPaused?'متوقف':[instant?(c.syncOnChange?'آنی':'دوره‌ای'):null,daily?'روزانه':null].filter(Boolean).join(' و ') || 'بدون پوشهٔ فعال') : 'خاموش';
  $('next-run').textContent = c.automatic ? (c.automaticPaused?'توقف موقت':!model.authenticated?'نیاز به ورود':s.busy?'در حال پردازش':s.nextSync?'نوبت بعد: '+date(s.nextSync*1000):'—') : 'اجرای دستی';
  $('startup-state').textContent = model.startup.error || (model.startup.registered ? 'اجرای خودکار ویندوز فعال است.' : c.startupEnabled ? 'ثبت ویندوز در دسترس نیست؛ گزینه را خاموش و دوباره روشن کنید.' : '');
  renderProgress(s);
  renderGauges(s);
  renderFolders();
  const previousErrors = s.busy && s.lastResult && !s.lastResult.ok ? s.lastResult.errors.map(text => 'خطای نوبت قبل: ' + text) : [];
  errorParagraphs($('errors'), [...new Set([...(model.error?[model.error]:[]), ...(s.watchError?[s.watchError]:[]), ...s.errors, ...previousErrors])]);
  const result = s.lastResult;
  $('result').textContent = result ? `${result.audit?'آخرین بررسی صحت':result.preview?'پیش‌نمایش':'آخرین ارسال'} · ${date(result.at)} · ${nf.format(result.excluded)} استثنا` : '';
  const run = s.busy ? s.runId : result?.id;
  if (views.run.run !== run) { views.run.run = run; views.run.offset = 0; }
  $('history').innerHTML = model.history.length ? `<table><thead><tr><th>زمان</th><th>نوع</th><th>فایل تازه</th><th>بدون تغییر</th><th>حجم</th><th>نتیجه</th></tr></thead><tbody>${model.history.map((h,i)=>`<tr><td><button class="link" data-report="${i}">${date(h.at)}</button></td><td>${h.audit?'بررسی صحت':h.retry?'تلاش مجدد':h.preview?'پیش‌نمایش':'ارسال'}</td><td>${nf.format(h.uploaded)}</td><td>${nf.format(h.unchanged)}</td><td dir="ltr">${size(h.bytes)}</td><td class="${h.ok?'success':'bad'}">${h.ok?'✓ موفق':'✕ '+nf.format(h.errors.length)+' خطا'}</td></tr>`).join('')}</tbody></table>` : '<div class="empty">گزارشی ثبت نشده است.</div>';
  renderControls();
}
async function loadItems(viewName) {
  const view = views[viewName], container = $(viewName + '-files');
  if (!view.run) { container.replaceChildren(); return; }
  const sequence = ++view.sequence;
  const data = await api('items', {run:view.run, offset:view.offset, failedOnly:viewName==='report' && $('report-failed').checked});
  if (sequence !== view.sequence || data.run !== view.run) return;
  const statuses = {queued:'در صف', deferred:'در انتظار ادامه', processing:'در حال پردازش', ok:'✓ ارسال شد', unchanged:'✓ بدون تغییر', trashed:'✓ سطل زباله', preview:'✓ آمادهٔ ارسال', error:'✕ خطا'};
  // Historic rows from older releases may have no stored explanation.
  data.items = data.items.map(file => ({...file, error:file.error || (['error','deferred'].includes(file.status) ? 'جزئیات در نسخهٔ قبلی ثبت نشده است؛ خطای کلی همین نوبت را در گزارشات ببینید و دوباره تلاش کنید.' : '')}));
  container.innerHTML = `<div class="file-summary">${nf.format(data.total)} فایل <span class="muted">لینک هر فایل، محل آن را در ویندوز باز می‌کند.</span></div>` + (data.items.length ? `<div class="table-scroll"><table><thead><tr><th>فایل</th><th>حجم</th><th>وضعیت</th><th></th></tr></thead><tbody>${data.items.map(file=>`<tr><td class="path"><a href="#file" class="link" data-reveal data-run="${esc(data.run)}" data-path="${esc(file.path)}" title="نمایش فایل در پوشه">${esc(file.path)}</a>${file.error?`<p class="bad">${esc(file.error)}</p>`:''}</td><td dir="ltr">${size(file.bytes)}</td><td class="${file.status==='error'?'bad':['ok','unchanged'].includes(file.status)?'success':''}">${file.status==='processing'?'<span class="spinner"></span> ':''}${statuses[file.status]||esc(file.status)}</td><td>${['error','deferred'].includes(file.status)?`<button data-retry data-run="${esc(data.run)}" data-path="${esc(file.path)}">تلاش مجدد</button>`:''}</td></tr>`).join('')}</tbody></table></div>` : '<div class="empty">فایلی در این بخش نیست.</div>') + `<div class="pagination"><button data-prev="${viewName}" ${view.offset===0?'disabled':''}>قبلی</button><span>${nf.format(Math.floor(view.offset/100)+1)} / ${nf.format(Math.max(1,Math.ceil(data.total/100)))}</span><button data-next="${viewName}" ${view.offset+100>=data.total?'disabled':''}>بعدی</button></div>`;
  view.items = data.items;
  renderControls();
}
async function loadFolderFiles() {
  const sequence = ++folderView.sequence;
  const data = await api('folder-files',{job:folderView.job,offset:folderView.offset});
  if (sequence !== folderView.sequence || data.job !== folderView.job) return;
  const container = $('folder-files');
  container.hidden=false;
  container.innerHTML = `<h2>فایل‌های بررسی‌شده · ${nf.format(data.total)} فایل</h2><div class="table-scroll"><table><thead><tr><th>فایل</th><th>حجم محلی</th><th>حجم ابری</th><th>وضعیت بررسی</th></tr></thead><tbody>${data.items.map(file=>`<tr><td class="path"><a href="#file" class="link" data-reveal data-run="${esc(data.run)}" data-path="${esc(file.path)}">${esc(file.relative)}</a></td><td>${size(file.localBytes)}</td><td>${file.cloudBytes===null?'موجود نیست':size(file.cloudBytes)}</td><td class="${file.status==='error'?'bad':'success'}">${file.status==='error'?esc(file.error):data.assurance==='size-and-identity'?'✓ اندازه و شناسه تأیید شد':'✓ SHA-256 برابر'}${file.status==='error'?` <button data-retry data-run="${esc(data.run)}" data-path="${esc(file.path)}">تلاش مجدد</button>`:''}</td></tr>`).join('')}</tbody></table></div><div class="pagination"><button data-folder-prev ${data.offset===0?'disabled':''}>قبلی</button><span>${nf.format(Math.floor(data.offset/100)+1)}</span><button data-folder-next ${data.offset+100>=data.total?'disabled':''}>بعدی</button></div>`;
  renderControls();
}
async function poll() {
  if (polling || closing) return;
  polling = true;
  try {
    const response = await fetch('/api/backup');
    if (!response.ok) throw Error('دریافت وضعیت ممکن نشد.');
    model = await response.json();
    if (closing) return;
    if (!initialized) { populate(model.config); initialized = true; }
    if (lastBusy && !model.state.busy) message(model.state.errors[0] || model.state.phase, !!model.state.errors.length);
    lastBusy = model.state.busy;
    render();
    await loadItems('run');
    if (!$('report-detail').hidden) await loadItems('report');
  } catch (error) { if (!closing) message('دریافت وضعیت ناموفق بود: ' + error.message, true); }
  finally { polling = false; }
}
document.querySelectorAll('[data-page]').forEach(button => button.onclick = () => showPage(button.dataset.page));
window.addEventListener('hashchange', () => showPage(location.hash.slice(1)));
$('auth-form').onsubmit = event => {
  event.preventDefault();
  perform($('login'), async () => {
    const data = {phone:$('phone').value, ...captcha, captcha_value:$('captcha-value').value};
    if (document.querySelector('[name="auth-mode"]:checked').value==='otp') data.code=$('otp').value; else data.password=$('password').value;
    try { message((await api('login',data)).message); }
    finally { $('password').value=''; $('otp').value=''; }
  });
};
document.querySelectorAll('[name="auth-mode"]').forEach(input => input.onchange = () => {
  const otp = input.value==='otp'; $('otp-fields').hidden=!otp; $('password-fields').hidden=otp;
});
$('send-code').onclick = () => perform($('send-code'), async () => message((await api('code',{phone:$('phone').value,...captcha,captcha_value:$('captcha-value').value})).message));
$('captcha-get').onclick = () => perform($('captcha-get'), async () => {
  const c = await api('captcha'); captcha={captcha_id:c.id,captcha_provider:c.provider};
  let image=c.image||'';
  if (!image.startsWith('data:image/')) {
    if (/^[A-Za-z0-9+/=\s]+$/.test(image)) image='data:image/png;base64,'+image;
    else throw Error('نوع تصویر کپچا معتبر نیست.');
  }
  if (!/^data:image\/(png|jpeg|jpg|gif|webp);base64,/i.test(image)) throw Error('نوع تصویر کپچا پشتیبانی نمی‌شود.');
  $('captcha-image').src=image; $('captcha-image').hidden=false; $('captcha-details').open=true;
});
for (const action of ['logout','pause','resume','cancel']) {
  const id={pause:'pause-sync',resume:'resume-sync'}[action]||action;
  $(id).onclick=()=>perform($(id),async()=>message((await api(action)).message));
}
$('save').onclick=()=>perform($('save'),save);
$('shutdown').onclick=()=>perform($('shutdown'),async()=>{
  const warning = dirty ? 'تغییرات ذخیره‌نشده از دست می‌رود. ' : '';
  if (!window.confirm(warning + 'برنامه بسته شود؟ کار جاری متوقف می‌شود و تا اجرای دوباره سینک انجام نمی‌شود.')) return;
  const result=await api('shutdown',{confirm:true});
  closing=true; dirty=false;
  clearTimeout(messageTimer);
  $('save-bar').hidden=true; $('message').hidden=true;
  $('exit-state').textContent=result.message + ' برای اتصال دوباره، برنامه را اجرا کنید.';
  $('exit-state').hidden=false;
});
$('browse').onclick=()=>perform($('browse'),async()=>{
  message('پنجرهٔ انتخاب پوشه باز است.'); const r=await api('browse');
  if (r.path) { $('folder-path').value=r.path; message('پوشه انتخاب شد؛ «افزودن» را بزنید.'); }
  else if(r.cancelled) message('انتخاب پوشه لغو شد.');
});
$('add-folder').onclick=()=>{
  const path=$('folder-path').value.trim().replace(/[\\/]+$/,'');
  if(!/^(?:[a-z]:[\\/]|\/)/i.test(path)) { message('مسیر کامل پوشه را وارد کنید.',true); return; }
  const name=path.split(/[\\/]/).pop();
  if(folders.some(f=>f.path.toLowerCase().replaceAll('/','\\')===path.toLowerCase().replaceAll('/','\\'))) { message('این پوشه قبلاً اضافه شده است.',true); return; }
  folders.push({path,name,enabled:true,syncMode:'instant',dailyTime:'02:00'}); $('folder-path').value=''; renderFolders(); changed();
};
$('folder-list').onclick=event=>{
  const button=event.target.closest('[data-remove]');
  if(button) { folders.splice(Number(button.dataset.remove),1); renderFolders(); changed(); }
};
$('folder-list').onchange=event=>{
  if(event.target.matches('[data-folder-enable]')) { folders[Number(event.target.dataset.folderEnable)].enabled=event.target.checked; changed(); }
  const mode = event.target.matches('[data-folder-mode]'), time = event.target.matches('[data-folder-time]');
  if (!mode && !time) return;
  const index = Number(mode ? event.target.dataset.folderMode : event.target.dataset.folderTime), folder = folders[index];
  const previous = {...folder};
  if (mode) folder.syncMode = event.target.value; else folder.dailyTime = event.target.value;
  if (!folder.id) { renderFolders(true); changed(); return; }
  return perform(event.target,async()=>{
    try {
      await api('folder-schedule',{job:folder.id,syncMode:folder.syncMode || 'instant',dailyTime:folder.dailyTime});
    } catch (error) { Object.assign(folder,previous); renderFolders(true); throw error; }
    model.config.folders = model.config.folders.map(item => item.id===folder.id ? {...folder} : item);
    renderFolders(true);
    message('زمان‌بندی ذخیره شد؛ از نوبت بعد اعمال می‌شود.');
  });
};
for(const id of ['remote-root','interval','max-size','settle','workers','automatic','sync-on-change','startup','verify','excludes']) $(id).addEventListener('input',changed);
for(const [id,action] of [['preview','preview'],['sync','sync'],['audit','audit'],['repair-audit','repair-audit'],['test','test']]) $(id).onclick=()=>perform($(id),async()=>{
  if(dirty) await save(); await api(action); showPage('run'); message('عملیات شروع شد.');
});
$('check-connection').onclick=()=>perform($('check-connection'),async()=>{
  await api('connection'); message('ظرفیت حساب در حال به‌روزرسانی است.');
});
document.addEventListener('click',event=>{
  const folderButton=event.target.closest('[data-folder-files],[data-folder-next],[data-folder-prev]');
  if (folderButton) {
    if (folderButton.disabled) return;
    event.preventDefault();
    if (folderButton.hasAttribute('data-folder-files')) folderView={job:folderButton.dataset.folderFiles,offset:0,sequence:folderView.sequence+1};
    else folderView.offset+=folderButton.hasAttribute('data-folder-next')?100:-100;
    loadFolderFiles().catch(error=>message(error.message,true));
    return;
  }
  const button=event.target.closest('[data-retry],[data-reveal],[data-next],[data-prev],[data-report]');
  if(!button || button.disabled) return;
  event.preventDefault();
  if(button.hasAttribute('data-report')) {
    const report=model.history[Number(button.dataset.report)];
    views.report.run=report.id; views.report.offset=0;
    $('report-detail').hidden=false; $('report-title').textContent=date(report.at);
    errorParagraphs($('report-errors'),report.errors);
    loadItems('report').catch(error=>message(error.message,true)); return;
  }
  if(button.hasAttribute('data-next') || button.hasAttribute('data-prev')) {
    const name=button.dataset.next||button.dataset.prev;
    views[name].offset+=button.hasAttribute('data-next')?100:-100;
    loadItems(name).catch(error=>message(error.message,true)); return;
  }
  const retry=button.hasAttribute('data-retry');
  const run=button.dataset.run, path=button.dataset.path;
  perform(button,async()=>{
    if(retry && dirty) await save();
    const result=await api(retry?'retry':'reveal',{run,path});
    if(retry) { showPage('run'); message('تلاش مجدد برای همین فایل شروع شد.'); }
    else message(result.message);
  });
});
$('report-failed').onchange=()=>{views.report.offset=0;loadItems('report').catch(error=>message(error.message,true));};
window.addEventListener('beforeunload',event=>{if(dirty){event.preventDefault();event.returnValue='';}});
showPage(location.hash.slice(1)); renderControls();
poll(); setInterval(poll,1500);
