'use strict';
/* Phase 7.14 — real-browser owner QA driver (Microsoft Edge / Google Chrome).
 *
 * Dependency-free Chrome DevTools Protocol driver built on Node's built-in global WebSocket. It
 * launches a real browser headless, navigates to the LOCAL loopback console only, and records the
 * evidence an owner-usability audit needs: the rendered DOM state, every network request the page
 * made, every browser console message / uncaught exception, and the result of scripted owner
 * interactions (navigation, keyboard, empty state, disabled control, modal).
 *
 * It never contacts the Internet: the only origin it may load is http://127.0.0.1:<port>, and the
 * run FAILS if the page issues a request to any other origin.
 *
 * This is a manually-invoked QA harness, not a unittest: it needs a real browser binary. Its output
 * is a single canonical JSON document recorded in the Phase 7.14 proof gate.
 *
 * Usage: node phase7_14_browser_qa.js --browser edge|chrome --base http://127.0.0.1:8780 --out result.json
 */
const { spawn } = require('child_process');
const fs = require('fs');
const os = require('os');
const path = require('path');
const http = require('http');

// ------------------------------------------------------------------ args
function arg(name, dflt) {
  const i = process.argv.indexOf('--' + name);
  return i >= 0 && process.argv[i + 1] ? process.argv[i + 1] : dflt;
}
const BROWSER = arg('browser', 'edge');
const BASE = arg('base', 'http://127.0.0.1:8780');
const OUT = arg('out', '');
const ORIGIN = new URL(BASE).origin;

const BROWSER_PATHS = {
  edge: ['C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe',
         'C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe'],
  chrome: ['C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe',
           'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe'],
};
function resolveBrowser() {
  for (const p of (BROWSER_PATHS[BROWSER] || [])) if (fs.existsSync(p)) return p;
  throw new Error('browser binary not found for ' + BROWSER);
}

const sleep = (ms) => new Promise(r => setTimeout(r, ms));

function getJSON(url) {
  return new Promise((resolve, reject) => {
    http.get(url, (res) => {
      let b = '';
      res.on('data', c => { b += c; });
      res.on('end', () => { try { resolve(JSON.parse(b)); } catch (e) { reject(e); } });
    }).on('error', reject);
  });
}

// ------------------------------------------------------------------ CDP client
class CDP {
  constructor(ws) {
    this.ws = ws; this.id = 0; this.pending = new Map(); this.handlers = [];
    ws.addEventListener('message', (ev) => {
      const msg = JSON.parse(typeof ev.data === 'string' ? ev.data : String(ev.data));
      if (msg.id != null && this.pending.has(msg.id)) {
        const { resolve, reject } = this.pending.get(msg.id);
        this.pending.delete(msg.id);
        if (msg.error) reject(new Error(msg.method + ': ' + JSON.stringify(msg.error)));
        else resolve(msg.result);
      } else if (msg.method) {
        this.handlers.forEach(h => h(msg));
      }
    });
  }
  on(fn) { this.handlers.push(fn); }
  send(method, params, sessionId) {
    const id = ++this.id;
    const payload = { id, method, params: params || {} };
    if (sessionId) payload.sessionId = sessionId;
    this.ws.send(JSON.stringify(payload));
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
      setTimeout(() => {
        if (this.pending.has(id)) { this.pending.delete(id); reject(new Error('CDP timeout: ' + method)); }
      }, 30000);
    });
  }
}

async function connect(wsUrl) {
  const ws = new WebSocket(wsUrl);
  await new Promise((resolve, reject) => {
    ws.addEventListener('open', resolve, { once: true });
    ws.addEventListener('error', () => reject(new Error('ws error')), { once: true });
  });
  return new CDP(ws);
}

// ------------------------------------------------------------------ page session
class Page {
  constructor(cdp, sessionId) {
    this.cdp = cdp; this.sid = sessionId;
    this.requests = []; this.console = []; this.exceptions = []; this.failures = [];
    cdp.on((msg) => {
      if (msg.sessionId !== this.sid) return;
      const p = msg.params || {};
      if (msg.method === 'Network.requestWillBeSent') this.requests.push({ url: p.request.url, method: p.request.method });
      else if (msg.method === 'Network.loadingFailed') this.failures.push({ text: p.errorText, type: p.type });
      else if (msg.method === 'Runtime.consoleAPICalled') {
        this.console.push({ level: p.type, text: (p.args || []).map(a => String(a.value != null ? a.value : a.description || a.type)).join(' ') });
      } else if (msg.method === 'Runtime.exceptionThrown') {
        this.exceptions.push({ text: (p.exceptionDetails && p.exceptionDetails.text) || 'exception',
                               detail: (p.exceptionDetails && p.exceptionDetails.exception && p.exceptionDetails.exception.description) || '' });
      } else if (msg.method === 'Log.entryAdded') {
        this.console.push({ level: p.entry.level, text: p.entry.text, source: p.entry.source });
      }
    });
  }
  s(method, params) { return this.cdp.send(method, params, this.sid); }
  async setup() {
    await this.s('Page.enable'); await this.s('Runtime.enable');
    await this.s('Log.enable'); await this.s('Network.enable');
  }
  async viewport(width, height) {
    await this.s('Emulation.setDeviceMetricsOverride',
                 { width, height, deviceScaleFactor: 1, mobile: false });
  }
  async goto(url) {
    await this.s('Page.navigate', { url });
    await sleep(1500);
  }
  async evaluate(expr) {
    const r = await this.s('Runtime.evaluate',
                           { expression: expr, returnByValue: true, awaitPromise: true });
    if (r.exceptionDetails) throw new Error('evaluate: ' + JSON.stringify(r.exceptionDetails.text));
    return r.result.value;
  }
  async key(text, code, keyCode) {
    await this.s('Input.dispatchKeyEvent', { type: 'keyDown', key: text, code, windowsVirtualKeyCode: keyCode });
    await this.s('Input.dispatchKeyEvent', { type: 'keyUp', key: text, code, windowsVirtualKeyCode: keyCode });
  }
  offOrigin() { return this.requests.filter(r => !r.url.startsWith(ORIGIN) && !r.url.startsWith('data:')); }
}

// ------------------------------------------------------------------ DOM probes (run inside the page)
const PROBE_OVERVIEW = `(function () {
  function txt(sel) { var e = document.querySelector(sel); return e ? e.textContent.trim() : null; }
  var root = document.getElementById('view-root');
  var sections = Array.prototype.map.call(root.querySelectorAll('section'), function (s) {
    var h = s.querySelector('h1,h2'); return h ? h.textContent.trim() : '(no heading)'; });
  var na = document.getElementById('next-action');
  var naOrder = -1, metricsOrder = -1, i = 0;
  Array.prototype.forEach.call(root.children, function (c) {
    if (c.id === 'next-action') naOrder = i;
    if (c.getAttribute('data-block') === 'counts') metricsOrder = i;
    i++;
  });
  var headings = Array.prototype.map.call(document.querySelectorAll('h1,h2,h3'), function (h) {
    return h.tagName + ':' + h.textContent.trim().slice(0, 60); });
  // Page controls that are unavailable must state their reason. The confirmation dialog's Confirm
  // button is excluded: it ships disabled on purpose (fail closed) and its reason is the dialog body,
  // which the prepared-modal checks below assert. confirm_starts_disabled records that separately.
  var disabled = Array.prototype.map.call(
    document.querySelectorAll('button[disabled]:not(#modal-execute)'), function (b) {
    return { label: b.textContent.trim().slice(0, 40),
             reason: b.getAttribute('title') || b.getAttribute('aria-describedby') || null,
             described: !!(b.getAttribute('aria-describedby') || b.getAttribute('title')) }; });
  var buttons = Array.prototype.map.call(document.querySelectorAll('button, a.btn'), function (b) {
    return { tag: b.tagName, label: b.textContent.trim().slice(0, 40),
             href: b.getAttribute('href'), disabled: !!b.disabled,
             wired: !!(b.getAttribute('href') || b.disabled || b.__wired || b.getAttribute('data-act')) }; });
  return {
    title: document.title,
    h1: txt('#view-root h1'),
    section_headings: sections,
    headings: headings,
    next_action_present: !!na,
    next_action_order: naOrder,
    metrics_order: metricsOrder,
    next_action_above_metrics: (naOrder >= 0 && metricsOrder >= 0 && naOrder < metricsOrder),
    next_action_title: na ? (na.querySelector('.na-title') ? na.querySelector('.na-title').textContent.trim() : null) : null,
    next_action_message: na ? (na.querySelector('.na-message') ? na.querySelector('.na-message').textContent.trim() : null) : null,
    next_action_why: na ? (na.querySelector('.na-why') ? na.querySelector('.na-why').textContent.trim() : null) : null,
    next_action_step: na ? (na.querySelector('.na-step') ? na.querySelector('.na-step').textContent.trim() : null) : null,
    next_action_expected: na ? (na.querySelector('.na-expected') ? na.querySelector('.na-expected').textContent.trim() : null) : null,
    next_action_cta_href: na && na.querySelector('.na-cta') ? na.querySelector('.na-cta').getAttribute('href') : null,
    details_elements: document.querySelectorAll('details').length,
    details_open_by_default: Array.prototype.filter.call(document.querySelectorAll('details'), function (d) { return d.open; }).length,
    disabled_controls: disabled,
    confirm_starts_disabled: (function () {
      var e = document.getElementById('modal-execute'); return e ? e.disabled : null;
    })(),
    buttons: buttons,
    doc_scroll_width: document.documentElement.scrollWidth,
    doc_client_width: document.documentElement.clientWidth,
    horizontal_overflow: document.documentElement.scrollWidth > document.documentElement.clientWidth + 1,
    skip_link: !!document.querySelector('a.skip-link'),
    landmarks: { banner: !!document.querySelector('[role=banner], header'),
                 nav: document.querySelectorAll('nav').length,
                 main: !!document.querySelector('main') },
    aria_live: document.querySelectorAll('[aria-live]').length,
    status_icons: document.querySelectorAll('.status-tag .glyph').length,
    status_tags: document.querySelectorAll('.status-tag').length,
    inline_scripts: document.querySelectorAll('script:not([src])').length,
    external_assets: Array.prototype.map.call(document.querySelectorAll('script[src], link[href], img[src]'), function (e) {
      return e.getAttribute('src') || e.getAttribute('href'); })
  };
})()`;

const PROBE_NAV = `(function () {
  var links = Array.prototype.map.call(document.querySelectorAll('#nav-list a'), function (a) {
    return { href: a.getAttribute('href'), label: a.textContent.trim(),
             current: a.getAttribute('aria-current') === 'page' }; });
  var groups = Array.prototype.map.call(document.querySelectorAll('#nav-list .nav-group-title'), function (g) {
    return g.textContent.trim(); });
  var bc = document.getElementById('breadcrumbs');
  return { links: links, groups: groups, breadcrumb: bc ? bc.textContent.trim() : null,
           hash: window.location.hash,
           active: links.filter(function (l) { return l.current; }).map(function (l) { return l.href; }) };
})()`;

const PROBE_TABLE = `(function () {
  var t = document.querySelector('#view-root table');
  var caption = t ? (t.querySelector('caption') ? t.querySelector('caption').textContent.trim() : null) : null;
  var empty = document.querySelector('#view-root .empty-state');
  return {
    has_table: !!t,
    caption: caption,
    row_count: t ? t.querySelectorAll('tbody tr').length : 0,
    has_search: !!document.querySelector('#view-root input[type=search]'),
    has_reset: !!document.querySelector('#view-root .reset-filters'),
    has_pager: !!document.querySelector('#view-root .pager'),
    sticky_head: t ? (getComputedStyle(t.querySelector('thead th')).position === 'sticky') : null,
    empty_state: empty ? {
      title: empty.querySelector('.es-title') ? empty.querySelector('.es-title').textContent.trim() : null,
      is_error: empty.querySelector('.es-error') ? empty.querySelector('.es-error').textContent.trim() : null,
      why: empty.querySelector('.es-why') ? empty.querySelector('.es-why').textContent.trim() : null,
      next: empty.querySelector('.es-next') ? empty.querySelector('.es-next').textContent.trim() : null,
      text: empty.textContent.trim().slice(0, 400)
    } : null
  };
})()`;

const PROBE_FOCUS = `(function () {
  var a = document.activeElement;
  return { tag: a ? a.tagName : null, id: a ? a.id : null,
           label: a ? (a.textContent || '').trim().slice(0, 40) : null,
           outline: a ? getComputedStyle(a).outlineStyle : null };
})()`;

/* Ask the accepted console, over loopback with a real session and CSRF token, to prepare an action
 * that is not on the allowlist. Proves the refusal returns no preparation token and no phrase.
 * Runs from Node so the page's console stays clean (see the call site for why that matters). */
function preparedRefusal() {
  const base = new URL(BASE);
  const req = (opts, body) => new Promise((resolve, reject) => {
    const r = http.request({ host: base.hostname, port: base.port, path: opts.path,
                             method: opts.method || 'GET',
                             headers: Object.assign({ Host: base.host }, opts.headers || {}) }, (res) => {
      let b = '';
      res.on('data', c => { b += c; });
      res.on('end', () => resolve({ status: res.statusCode, headers: res.headers, body: b }));
    });
    r.on('error', reject);
    if (body) r.write(body);
    r.end();
  });
  return (async () => {
    const s = await req({ path: '/api/v1/session', headers: { Accept: 'application/json' } });
    const cookie = (s.headers['set-cookie'] || []).map(c => c.split(';')[0]).join('; ');
    let csrf = null;
    try { csrf = (JSON.parse(s.body).data || {}).csrf_token || null; } catch (e) { /* reported below */ }
    const payload = JSON.stringify({ action: 'not-a-real-action', params: {} });
    const p = await req({ path: '/api/v1/actions/prepare', method: 'POST',
      headers: { 'Content-Type': 'application/json', Accept: 'application/json',
                 'Content-Length': Buffer.byteLength(payload),
                 'X-CSRF-Token': csrf || '', Cookie: cookie } }, payload);
    let parsed = {};
    try { parsed = JSON.parse(p.body); } catch (e) { /* non-JSON body is itself a finding */ }
    const data = parsed.data || {};
    return { session_status: s.status, had_csrf: !!csrf, status: p.status,
             error: parsed.error || null, readiness: parsed.readiness || null,
             has_token: !!data.action_token, has_phrase: !!data.confirmation_phrase };
  })();
}

// ------------------------------------------------------------------ main
async function main() {
  const bin = resolveBrowser();
  const port = 9222 + Math.floor(process.pid % 500);
  const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'p714qa-'));
  const proc = spawn(bin, [
    '--headless=new', '--disable-gpu', '--no-first-run', '--no-default-browser-check',
    '--disable-extensions', '--disable-background-networking', '--disable-sync',
    '--disable-component-update', '--disable-features=Translate,OptimizationHints',
    '--remote-debugging-port=' + port, '--user-data-dir=' + profile, 'about:blank'
  ], { stdio: 'ignore' });

  const result = { browser: BROWSER, browser_binary: path.basename(bin), base: BASE, checks: [], ok: true };
  function check(name, ok, detail) {
    result.checks.push({ check: name, ok: !!ok, detail: detail === undefined ? null : detail });
    if (!ok) result.ok = false;
  }

  let cdp, page;
  try {
    let version = null;
    for (let i = 0; i < 60 && !version; i++) {
      try { version = await getJSON('http://127.0.0.1:' + port + '/json/version'); } catch (e) { await sleep(250); }
    }
    if (!version) throw new Error('browser did not expose a debugging endpoint');
    result.browser_version = version['Browser'];
    result.user_agent = version['User-Agent'];

    cdp = await connect(version.webSocketDebuggerUrl);
    const { targetId } = await cdp.send('Target.createTarget', { url: 'about:blank' });
    const { sessionId } = await cdp.send('Target.attachToTarget', { targetId, flatten: true });
    page = new Page(cdp, sessionId);
    await page.setup();

    // ---------------- 1920x1080 overview -------------------------------------------------------
    await page.viewport(1920, 1080);
    await page.goto(BASE + '/#overview');
    await sleep(1200);
    const ov = await page.evaluate(PROBE_OVERVIEW);
    result.overview_1920 = ov;
    check('overview_renders', ov.h1 !== null, ov.h1);
    check('next_action_panel_present', ov.next_action_present);
    check('next_action_above_metrics', ov.next_action_above_metrics,
          'na=' + ov.next_action_order + ' counts=' + ov.metrics_order);
    check('next_action_answers_what', !!ov.next_action_title, ov.next_action_title);
    check('next_action_answers_why', !!ov.next_action_why, ov.next_action_why);
    check('next_action_answers_step', !!ov.next_action_step, ov.next_action_step);
    check('next_action_answers_expected', !!ov.next_action_expected, ov.next_action_expected);
    check('progressive_disclosure_present', ov.details_elements > 0, ov.details_elements);
    check('progressive_disclosure_closed_by_default', ov.details_open_by_default === 0, ov.details_open_by_default);
    check('no_horizontal_overflow_1920', !ov.horizontal_overflow,
          ov.doc_scroll_width + '/' + ov.doc_client_width);
    check('skip_link_present', ov.skip_link);
    check('landmarks_present', ov.landmarks.banner && ov.landmarks.main && ov.landmarks.nav > 0,
          JSON.stringify(ov.landmarks));
    check('aria_live_present', ov.aria_live > 0, ov.aria_live);
    check('status_not_colour_only', ov.status_tags > 0 && ov.status_icons >= ov.status_tags,
          'tags=' + ov.status_tags + ' glyphs=' + ov.status_icons);
    check('no_inline_script', ov.inline_scripts === 0, ov.inline_scripts);
    check('assets_all_local', ov.external_assets.every(a => a && a.startsWith('/')),
          JSON.stringify(ov.external_assets));
    check('heading_hierarchy_starts_h1', ov.headings.length > 0 && ov.headings[0].startsWith('H1'),
          ov.headings[0]);
    check('no_dead_button', ov.buttons.every(b => b.wired || b.disabled || b.href),
          JSON.stringify(ov.buttons.filter(b => !b.wired && !b.disabled && !b.href)));
    check('disabled_controls_have_reason', ov.disabled_controls.every(d => d.described),
          JSON.stringify(ov.disabled_controls));
    check('confirm_starts_disabled', ov.confirm_starts_disabled === true,
          String(ov.confirm_starts_disabled));

    // ---------------- 1366x768 ------------------------------------------------------------------
    await page.viewport(1366, 768);
    await page.goto(BASE + '/#overview');
    await sleep(1000);
    const ov13 = await page.evaluate(PROBE_OVERVIEW);
    result.overview_1366 = { horizontal_overflow: ov13.horizontal_overflow,
                             doc_scroll_width: ov13.doc_scroll_width,
                             doc_client_width: ov13.doc_client_width,
                             next_action_present: ov13.next_action_present };
    check('no_horizontal_overflow_1366', !ov13.horizontal_overflow,
          ov13.doc_scroll_width + '/' + ov13.doc_client_width);
    check('next_action_visible_1366', ov13.next_action_present);

    // ---------------- navigation ---------------------------------------------------------------
    await page.viewport(1920, 1080);
    const nav = await page.evaluate(PROBE_NAV);
    result.navigation = nav;
    check('sidebar_groups_present', nav.groups.length >= 3, JSON.stringify(nav.groups));
    check('active_nav_marked', nav.active.length === 1, JSON.stringify(nav.active));
    check('breadcrumb_present', !!nav.breadcrumb, nav.breadcrumb);
    const navHrefs = nav.links.map(l => l.href);

    // every nav destination resolves to a real rendered page
    const navResults = [];
    for (const href of navHrefs) {
      await page.evaluate(`window.location.hash = ${JSON.stringify(href.replace('#', ''))};`);
      await sleep(900);
      const st = await page.evaluate(PROBE_NAV);
      const h1 = await page.evaluate(`(function(){var h=document.querySelector('#view-root h1');return h?h.textContent.trim():null;})()`);
      navResults.push({ href, rendered_h1: h1, active: st.active, breadcrumb: st.breadcrumb });
    }
    result.nav_destinations = navResults;
    check('every_nav_destination_renders', navResults.every(n => !!n.rendered_h1),
          JSON.stringify(navResults.filter(n => !n.rendered_h1)));
    check('every_nav_destination_marks_active', navResults.every(n => n.active.length === 1),
          JSON.stringify(navResults.filter(n => n.active.length !== 1)));

    // ---------------- next-action destination is a real page -----------------------------------
    await page.evaluate(`window.location.hash = 'overview';`);
    await sleep(900);
    const naHref = (await page.evaluate(PROBE_OVERVIEW)).next_action_cta_href;
    result.next_action_cta_href = naHref;
    if (naHref && naHref.indexOf('#') === 0) {
      await page.evaluate(`window.location.hash = ${JSON.stringify(naHref.replace('#', ''))};`);
      await sleep(900);
      const h1 = await page.evaluate(`(function(){var h=document.querySelector('#view-root h1');return h?h.textContent.trim():null;})()`);
      result.next_action_destination_h1 = h1;
      check('next_action_destination_renders', !!h1, h1);
    } else {
      check('next_action_destination_renders', naHref === null || naHref === undefined,
            'no navigational CTA (instruction-only destination): ' + naHref);
    }

    // ---------------- hash retained on refresh --------------------------------------------------
    await page.goto(BASE + '/#system');
    await sleep(1100);
    const afterReload = await page.evaluate(PROBE_NAV);
    result.hash_after_reload = afterReload.hash;
    check('hash_retained_on_refresh', afterReload.hash === '#system', afterReload.hash);
    check('breadcrumb_after_reload', !!afterReload.breadcrumb, afterReload.breadcrumb);

    // ---------------- table + empty state -------------------------------------------------------
    await page.evaluate(`window.location.hash = 'research';`);
    await sleep(1100);
    const tbl = await page.evaluate(PROBE_TABLE);
    result.table_research = tbl;
    check('table_has_search', tbl.has_search);
    check('table_has_reset_filters', tbl.has_reset);
    check('table_has_pager', tbl.has_pager);
    check('table_sticky_header', tbl.sticky_head === true, String(tbl.sticky_head));
    check('table_shows_total_count', !!tbl.caption && /\d/.test(tbl.caption), tbl.caption);
    if (tbl.row_count === 0) {
      check('empty_state_explains_missing', !!(tbl.empty_state && tbl.empty_state.title),
            tbl.empty_state && tbl.empty_state.title);
      check('empty_state_says_whether_error', !!(tbl.empty_state && tbl.empty_state.is_error),
            tbl.empty_state && tbl.empty_state.is_error);
      check('empty_state_explains_why', !!(tbl.empty_state && tbl.empty_state.why),
            tbl.empty_state && tbl.empty_state.why);
      check('empty_state_explains_next_step', !!(tbl.empty_state && tbl.empty_state.next),
            tbl.empty_state && tbl.empty_state.next);
      check('empty_state_not_bare_no_data',
            !!(tbl.empty_state && tbl.empty_state.text && tbl.empty_state.text.trim() !== 'No data'),
            tbl.empty_state && tbl.empty_state.text);
    } else {
      check('empty_state_not_applicable', true, 'rows=' + tbl.row_count);
    }

    // ---------------- keyboard navigation -------------------------------------------------------
    await page.evaluate(`window.location.hash = 'overview';`);
    await sleep(900);
    await page.evaluate(`document.body.focus(); document.activeElement.blur();`);
    const tabbed = [];
    for (let i = 0; i < 8; i++) {
      await page.key('Tab', 'Tab', 9);
      await sleep(90);
      tabbed.push(await page.evaluate(PROBE_FOCUS));
    }
    result.keyboard_tab_order = tabbed;
    check('keyboard_reaches_controls', tabbed.some(t => t.tag === 'A' || t.tag === 'BUTTON'
                                                        || t.tag === 'INPUT' || t.tag === 'SELECT' || t.tag === 'SUMMARY'),
          JSON.stringify(tabbed.map(t => t.tag)));
    check('focus_is_visible', tabbed.some(t => t.outline && t.outline !== 'none'),
          JSON.stringify(tabbed.map(t => t.outline)));

    /* ---------------- next-action navigation contract (Day-0 pilot hotfix) ----------------------
     * A pilot found "Go to Analysis & Decisions" failing to navigate while an empty confirmation
     * dialog covered the page. Root cause: the author rule `#modal-backdrop { display: flex }`
     * outranked the user-agent `[hidden] { display: none }`, so the hidden dialog stayed painted at
     * z-index 80 and swallowed every click. These checks are the ones that could have caught it:
     * they read the COMPUTED style (not the `hidden` property, which was always correctly true),
     * hit-test every control against what the browser would actually deliver the click to, and then
     * click the CTA with a real mouse event at its real coordinates. */
    await page.evaluate(`window.location.hash = 'overview';`);
    await sleep(1200);

    const hiddenCss = await page.evaluate(`(function () {
      function st(id) {
        var e = document.getElementById(id);
        if (!e) return null;
        var cs = getComputedStyle(e);
        var r = e.getBoundingClientRect();
        return { hidden_attr: e.hasAttribute('hidden'), hidden_prop: e.hidden,
                 display: cs.display, visibility: cs.visibility,
                 rect: { w: Math.round(r.width), h: Math.round(r.height) } };
      }
      return { backdrop: st('modal-backdrop'), confirm_wrap: st('modal-confirm-wrap'),
               toast: st('toast'),
               center_element: (function () {
                 var e = document.elementFromPoint(Math.floor(window.innerWidth / 2),
                                                   Math.floor(window.innerHeight / 2));
                 var out = [];
                 while (e) { out.push(e.tagName + (e.id ? '#' + e.id : '')); e = e.parentElement; }
                 return out.join(' < ');
               })() };
    })()`);
    result.hidden_css = hiddenCss;
    check('hidden_modal_computes_to_display_none',
          hiddenCss.backdrop && hiddenCss.backdrop.hidden_attr === true
          && hiddenCss.backdrop.display === 'none', JSON.stringify(hiddenCss.backdrop));
    check('hidden_modal_occupies_no_space',
          hiddenCss.backdrop && hiddenCss.backdrop.rect.w === 0 && hiddenCss.backdrop.rect.h === 0,
          JSON.stringify(hiddenCss.backdrop && hiddenCss.backdrop.rect));
    check('page_centre_is_not_the_modal',
          hiddenCss.center_element.indexOf('modal') < 0, hiddenCss.center_element);

    // every visible control must be the element the browser would actually deliver a click to
    const hitTest = await page.evaluate(`(function () {
      var out = [];
      Array.prototype.forEach.call(
        document.querySelectorAll('#nav-list a, #view-root a, #view-root button, #statusbar button'),
        function (b) {
          var r = b.getBoundingClientRect();
          if (r.width <= 0 || r.height <= 0) return;
          var cx = r.x + r.width / 2, cy = r.y + r.height / 2;
          if (cx < 0 || cy < 0 || cx > window.innerWidth || cy > window.innerHeight) return;
          var hit = document.elementFromPoint(cx, cy), e = hit, reachable = false;
          while (e) { if (e === b) { reachable = true; break; } e = e.parentElement; }
          out.push({ label: (b.textContent || '').trim().slice(0, 40),
                     act: b.getAttribute('data-act'),
                     hit: hit ? (hit.tagName + (hit.id ? '#' + hit.id : '')) : null,
                     reachable: reachable });
        });
      return { total: out.length, blocked: out.filter(function (o) { return !o.reachable; }) };
    })()`);
    result.hit_test = hitTest;
    check('every_control_is_clickable', hitTest.total > 0 && hitTest.blocked.length === 0,
          hitTest.total + ' controls, blocked=' + JSON.stringify(hitTest.blocked.slice(0, 6)));

    // Click the CTA with a real mouse event, at its real coordinates. The element is scrolled into
    // view first and the coordinates are hit-tested, so a click that would have landed on something
    // else is reported as such instead of silently looking like "navigation did not happen".
    const ctaBefore = await page.evaluate(`(function () {
      var na = document.getElementById('next-action');
      var cta = na ? na.querySelector('.na-cta') : null;
      if (!cta) return { found: false };
      cta.scrollIntoView({ block: 'center' });
      var r = cta.getBoundingClientRect();
      var cx = r.x + r.width / 2, cy = r.y + r.height / 2;
      var hit = document.elementFromPoint(cx, cy), e = hit, reachable = false;
      while (e) { if (e === cta) { reachable = true; break; } e = e.parentElement; }
      return { found: true, tag: cta.tagName, text: cta.textContent.trim(),
               act: cta.getAttribute('data-act'), href: cta.getAttribute('href'),
               data_action: cta.getAttribute('data-action'),
               cx: cx, cy: cy,
               in_viewport: cx >= 0 && cy >= 0 && cx <= window.innerWidth && cy <= window.innerHeight,
               hit: hit ? (hit.tagName + (hit.id ? '#' + hit.id : '')) : null,
               click_reaches_cta: reachable,
               hash_before: window.location.hash };
    })()`);
    result.next_action_cta = ctaBefore;
    check('cta_is_a_navigation_anchor',
          ctaBefore.found === true && ctaBefore.tag === 'A'
          && String(ctaBefore.act || '').indexOf('nav:') === 0
          && String(ctaBefore.href || '').charAt(0) === '#' && !ctaBefore.data_action,
          JSON.stringify(ctaBefore));
    check('cta_receives_the_owner_click',
          ctaBefore.in_viewport === true && ctaBefore.click_reaches_cta === true,
          'hit=' + ctaBefore.hit + ' in_viewport=' + ctaBefore.in_viewport);

    const prepareBefore = page.requests.filter(r => r.url.indexOf('/actions/prepare') >= 0).length;
    if (ctaBefore.found) {
      await page.s('Input.dispatchMouseEvent', { type: 'mousePressed', x: ctaBefore.cx,
                                                 y: ctaBefore.cy, button: 'left', clickCount: 1 });
      await page.s('Input.dispatchMouseEvent', { type: 'mouseReleased', x: ctaBefore.cx,
                                                 y: ctaBefore.cy, button: 'left', clickCount: 1 });
      await sleep(1500);
    }
    const afterCta = await page.evaluate(`(function () {
      var links = Array.prototype.map.call(document.querySelectorAll('#nav-list a'), function (a) {
        return { href: a.getAttribute('href'), current: a.getAttribute('aria-current') === 'page' }; });
      var bd = document.getElementById('modal-backdrop');
      var h1 = document.querySelector('#view-root h1');
      var bc = document.getElementById('breadcrumbs');
      return { hash: window.location.hash, h1: h1 ? h1.textContent.trim() : null,
               breadcrumb: bc ? bc.textContent.trim() : null,
               active: links.filter(function (l) { return l.current; }).map(function (l) { return l.href; }),
               modal_hidden: bd ? bd.hidden : null,
               modal_display: bd ? getComputedStyle(bd).display : null };
    })()`);
    const prepareAfter = page.requests.filter(r => r.url.indexOf('/actions/prepare') >= 0).length;
    result.next_action_after_click = afterCta;
    result.next_action_prepare_calls = prepareAfter - prepareBefore;
    check('cta_navigates_to_analysis', afterCta.hash === '#analysis', JSON.stringify(afterCta));
    check('cta_destination_content_visible', afterCta.h1 === 'Analysis & Decisions', afterCta.h1);
    check('cta_updates_active_nav', afterCta.active.length === 1 && afterCta.active[0] === '#analysis',
          JSON.stringify(afterCta.active));
    check('cta_updates_breadcrumb', String(afterCta.breadcrumb || '').indexOf('Analysis & Decisions') >= 0,
          afterCta.breadcrumb);
    check('cta_opens_no_modal', afterCta.modal_hidden === true && afterCta.modal_display === 'none',
          JSON.stringify(afterCta));
    check('cta_issues_zero_prepare_requests', (prepareAfter - prepareBefore) === 0,
          'prepare calls = ' + (prepareAfter - prepareBefore));

    // the destination survives a normal reload and a cache-bypassing reload
    for (const [name, ignoreCache] of [['normal_refresh', false], ['hard_refresh', true]]) {
      await page.s('Page.reload', { ignoreCache });
      await sleep(1800);
      const kept = await page.evaluate(`(function () {
        var h1 = document.querySelector('#view-root h1');
        var bd = document.getElementById('modal-backdrop');
        return { hash: window.location.hash, h1: h1 ? h1.textContent.trim() : null,
                 modal_display: bd ? getComputedStyle(bd).display : null }; })()`);
      result['destination_after_' + name] = kept;
      check('destination_survives_' + name,
            kept.hash === '#analysis' && kept.h1 === 'Analysis & Decisions'
            && kept.modal_display === 'none', JSON.stringify(kept));
    }

    /* A bounded failed preparation. This is issued from Node with a real session + CSRF token rather
     * than from inside the page: an in-page raw fetch would (correctly) be rejected by CSRF and the
     * resulting 403 would appear as a browser console error, masking real console failures. The
     * front end's own handling of a failed / incomplete / malformed preparation is proved by the
     * committed DOM harness, which drives the real app.js code path against those responses. */
    const failedPrep = await preparedRefusal();
    result.failed_prepare = failedPrep;
    check('unknown_action_refused_by_server',
          failedPrep.status >= 400 && failedPrep.has_token === false, JSON.stringify(failedPrep));
    check('refused_action_left_no_token_and_no_phrase',
          !failedPrep.has_token && !failedPrep.has_phrase, JSON.stringify(failedPrep));

    // ---------------- modal regression ----------------------------------------------------------
    await page.evaluate(`window.location.hash = 'backups';`);
    await sleep(1200);
    // Target the control by its declared action, not by its owner-facing wording, so a label
    // change can never silently turn this regression check into a no-op.
    const modalOpen = await page.evaluate(`(function () {
      var b = document.querySelector('[data-act="action:create-backup-snapshot"]');
      if (!b) return { clicked: false, reason: 'create-backup-snapshot control not found' };
      b.click();
      return { clicked: true };
    })()`);
    await sleep(1600);
    const modal = await page.evaluate(`(function () {
      var bd = document.getElementById('modal-backdrop');
      var title = document.getElementById('modal-title');
      var desc = document.getElementById('modal-desc');
      var exec = document.getElementById('modal-execute');
      var wrap = document.getElementById('modal-confirm-wrap');
      var phrase = document.getElementById('modal-phrase-required');
      return {
        open: bd ? !bd.hidden : null,
        title: title ? title.textContent.trim() : null,
        desc_len: desc ? desc.textContent.trim().length : 0,
        exec_hidden: exec ? exec.hidden : null,
        exec_disabled: exec ? exec.disabled : null,
        confirm_visible: wrap ? !wrap.hidden : null,
        phrase_text: phrase ? phrase.textContent.trim() : null,
        focus_id: document.activeElement ? document.activeElement.id : null
      };
    })()`);
    result.modal = { trigger: modalOpen, state: modal };
    check('modal_opens_not_blank', modal.open === true && modal.desc_len > 0,
          JSON.stringify(modal));
    check('modal_gates_confirm', modal.confirm_visible === false || modal.exec_disabled === true
          || modal.exec_hidden === true, JSON.stringify(modal));

    /* A real state-changing action must show the complete owner-facing dialog and keep Confirm
     * disabled until the exact phrase is typed. Typed with real key events through the real input
     * listener, so a gating regression cannot hide behind a programmatic value assignment. */
    const prepared = await page.evaluate(`(function () {
      var desc = document.getElementById('modal-desc');
      var req = document.getElementById('modal-phrase-required');
      var m = /Required confirmation phrase:\\s*(.+)$/.exec(req ? req.textContent.trim() : '');
      var keys = Array.prototype.map.call(desc.querySelectorAll('dt'), function (d) { return d.textContent.trim(); });
      return { keys: keys, text: desc.textContent,
               canonical: document.getElementById('modal-canonical').textContent.trim(),
               readiness: document.getElementById('modal-readiness').textContent.trim(),
               phrase: m ? m[1] : null,
               exec_disabled: document.getElementById('modal-execute').disabled };
    })()`);
    result.prepared_modal = prepared;
    check('prepared_modal_states_authority_target_effect',
          ['Accepted authority', 'Target(s)', 'Expected effect', 'Confirmation window']
            .every(k => prepared.keys.indexOf(k) >= 0), JSON.stringify(prepared.keys));
    check('prepared_modal_states_canonical_action_and_readiness',
          prepared.canonical.indexOf('create-backup-snapshot') >= 0 && prepared.readiness.length > 0,
          prepared.canonical + ' / ' + prepared.readiness);
    check('prepared_modal_confirm_starts_disabled', prepared.exec_disabled === true,
          String(prepared.exec_disabled));
    check('prepared_modal_shows_a_phrase', !!prepared.phrase, String(prepared.phrase));

    if (prepared.phrase) {
      const typeInto = async (text) => {
        await page.evaluate(`(function () { var i = document.getElementById('modal-phrase');
          i.value = ''; i.focus(); return true; })()`);
        await page.s('Input.insertText', { text });
        await sleep(250);
        return page.evaluate(`document.getElementById('modal-execute').disabled`);
      };
      const gate = {
        wrong: await typeInto('WRONG:phrase'),
        case_folded: await typeInto(prepared.phrase.toLowerCase()),
        trailing_space: await typeInto(prepared.phrase + ' '),
        leading_space: await typeInto(' ' + prepared.phrase),
        exact: await typeInto(prepared.phrase),
      };
      result.phrase_gate = gate;
      check('wrong_phrase_keeps_confirm_disabled', gate.wrong === true, String(gate.wrong));
      check('case_folded_phrase_keeps_confirm_disabled', gate.case_folded === true, String(gate.case_folded));
      check('trailing_space_phrase_keeps_confirm_disabled', gate.trailing_space === true,
            String(gate.trailing_space));
      check('leading_space_phrase_keeps_confirm_disabled', gate.leading_space === true,
            String(gate.leading_space));
      check('exact_phrase_enables_confirm', gate.exact === false, String(gate.exact));
    }
    await page.key('Escape', 'Escape', 27);
    await sleep(500);
    const closed = await page.evaluate(`(function(){var b=document.getElementById('modal-backdrop');return b?b.hidden:null;})()`);
    result.modal.closed_by_escape = closed;
    check('modal_closes_with_escape', closed === true, String(closed));

    // ---------------- export ---------------------------------------------------------------------
    const exp = await page.evaluate(`fetch('/api/v1/exports/overview?format=json', { credentials: 'same-origin' })
      .then(function (r) { return r.text().then(function (t) { return { status: r.status, len: t.length,
        ctype: r.headers.get('content-type') }; }); })`);
    result.export = exp;
    check('export_download_ok', exp.status === 200 && exp.len > 0, JSON.stringify(exp));

    // ---------------- network + console -----------------------------------------------------------
    const off = page.offOrigin();
    result.network = { total_requests: page.requests.length, off_origin: off,
                       failures: page.failures,
                       origins: Array.from(new Set(page.requests.map(r => { try { return new URL(r.url).origin; } catch (e) { return r.url.slice(0, 12); } }))) };
    check('all_requests_same_origin_loopback', off.length === 0, JSON.stringify(off));
    const errs = page.console.filter(c => c.level === 'error' || c.level === 'severe');
    result.console_messages = page.console;
    result.console_errors = errs;
    result.exceptions = page.exceptions;
    check('no_browser_console_error', errs.length === 0, JSON.stringify(errs));
    check('no_uncaught_exception', page.exceptions.length === 0, JSON.stringify(page.exceptions));
  } catch (e) {
    result.ok = false;
    result.fatal = String(e && e.message ? e.message : e);
  } finally {
    try { if (cdp) cdp.ws.close(); } catch (e) { /* ignore */ }
    try { proc.kill(); } catch (e) { /* ignore */ }
    await sleep(400);
    try { fs.rmSync(profile, { recursive: true, force: true }); } catch (e) { /* ignore */ }
  }

  result.checks_total = result.checks.length;
  result.checks_passed = result.checks.filter(c => c.ok).length;
  result.checks_failed = result.checks.filter(c => !c.ok).length;
  const text = JSON.stringify(result, null, 2);
  if (OUT) fs.writeFileSync(OUT, text, 'utf8');
  process.stdout.write(text + '\n');
  process.exit(result.ok ? 0 : 1);
}

main();
