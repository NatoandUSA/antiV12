'use strict';
/* Phase 7.13 action-confirmation modal — dependency-free DOM contract harness.
 *
 * Loads the REAL production app.js into a minimal DOM shim and drives the exact owner flow (prepare ->
 * modal -> confirm -> execute). No framework, no network: fetch is shimmed with fixtures supplied by the
 * Python test (real prepare/execute/error envelopes produced by the accepted backend). Prints one line
 * per check ("PASS <name>" / "FAIL <name>: <detail>") and exits non-zero if any check fails.
 *
 * Usage: node phase7_13_modal_dom_harness.js <fixtures.json>
 */
const fs = require('fs');
const vm = require('vm');

const fixturesPath = process.argv[2];
const FX = JSON.parse(fs.readFileSync(fixturesPath, 'utf8'));
const APP_SRC = fs.readFileSync(FX.app_js, 'utf8');

// ------------------------------------------------------------------ minimal DOM
function makeDom() {
  let ACTIVE = null;
  class Node {
    constructor(type) {
      this.nodeType = type; this.childNodes = []; this.parentNode = null;
      this.attributes = {}; this._listeners = {}; this._hidden = false; this.style = {};
      this._data = ''; this._value = ''; this.disabled = false; this.selected = false;
      this.tagName = null; this.id = '';
    }
    get children() { return this.childNodes.filter(n => n.nodeType === 1); }
    appendChild(c) { if (c == null) return c; if (c.parentNode) c.parentNode.removeChild(c);
      c.parentNode = this; this.childNodes.push(c); return c; }
    removeChild(c) { const i = this.childNodes.indexOf(c); if (i >= 0) { this.childNodes.splice(i, 1); c.parentNode = null; } return c; }
    insertBefore(n, ref) { if (n.parentNode) n.parentNode.removeChild(n); n.parentNode = this;
      const i = ref ? this.childNodes.indexOf(ref) : -1; if (i < 0) this.childNodes.push(n); else this.childNodes.splice(i, 0, n); return n; }
    setAttribute(k, v) { if (k === 'hidden') { this._hidden = true; return; }
      if (k === 'id') { this.id = String(v); return; } this.attributes[k] = String(v); }
    getAttribute(k) { if (k === 'hidden') return this._hidden ? '' : null;
      return (k in this.attributes) ? this.attributes[k] : null; }
    removeAttribute(k) { if (k === 'hidden') { this._hidden = false; return; } delete this.attributes[k]; }
    get className() { return this.attributes['class'] || ''; }
    set className(v) { this.attributes['class'] = v; }
    get hidden() { return this._hidden; }
    set hidden(v) { this._hidden = !!v; }
    get value() { return this._value; }
    set value(v) { this._value = v == null ? '' : String(v); }
    get textContent() { return this.childNodes.map(n => n.nodeType === 3 ? n._data : n.textContent).join(''); }
    set textContent(v) { this.childNodes.forEach(c => { c.parentNode = null; }); this.childNodes = [];
      if (v !== '' && v != null) { const t = new Node(3); t._data = String(v); t.parentNode = this; this.childNodes.push(t); } }
    addEventListener(t, fn) { (this._listeners[t] = this._listeners[t] || []).push(fn); }
    focus() { ACTIVE = this; }
    select() {}
  }
  const doc = {
    readyState: 'loading',                        // suppress app.js auto-boot; we wire the modal manually
    _all: {},
    createElement(t) { const e = new Node(1); e.tagName = String(t).toUpperCase(); return e; },
    createTextNode(s) { const n = new Node(3); n._data = String(s); return n; },
    getElementById(x) { return doc._all[x] || null; },
    addEventListener() {},
    execCommand() { return true; },
    get activeElement() { return ACTIVE; },
  };
  function mk(tag, id) { const e = doc.createElement(tag); if (id) { e.id = id; doc._all[id] = e; } return e; }
  const html = mk('html'); doc.documentElement = html;
  const body = mk('body'); html.appendChild(body); doc.body = body;
  // shell nodes referenced by route()/refreshStatusBar()/toast()
  body.appendChild(mk('div', 'status-metrics'));
  const sidebar = mk('nav'); const navList = mk('ul', 'nav-list'); sidebar.appendChild(navList); body.appendChild(sidebar);
  const content = mk('main', 'content'); body.appendChild(content);
  content.appendChild(mk('nav', 'breadcrumbs'));
  content.appendChild(mk('section', 'view-root'));
  body.appendChild(mk('div', 'toast'));
  // modal subtree — mirrors production/index.html (validated separately by a Python id/attr assertion)
  const backdrop = mk('div', 'modal-backdrop'); backdrop.hidden = true;
  const modal = mk('div', 'modal');
  modal.appendChild(mk('h2', 'modal-title'));
  modal.appendChild(mk('p', 'modal-canonical'));
  modal.appendChild(mk('div', 'modal-readiness'));
  modal.appendChild(mk('div', 'modal-desc'));
  const wrap = mk('div', 'modal-confirm-wrap'); wrap.hidden = true;
  wrap.appendChild(mk('p', 'modal-phrase-required'));
  wrap.appendChild(mk('input', 'modal-phrase'));
  wrap.appendChild(mk('p', 'modal-phrase-hint'));
  modal.appendChild(wrap);
  modal.appendChild(mk('div', 'modal-result'));
  modal.appendChild(mk('button', 'modal-cancel'));
  modal.appendChild(mk('button', 'modal-execute'));
  backdrop.appendChild(modal); body.appendChild(backdrop);
  // ancestry path for bubbling
  function path(node) { const p = []; let n = node; while (n) { p.push(n); n = n.parentNode; } return p; }
  function fire(target, type, init) {
    const ev = Object.assign({ type, target, _prevented: false,
      preventDefault() { this._prevented = true; }, stopPropagation() {} }, init || {});
    for (const n of path(target)) { (n._listeners[type] || []).forEach(fn => fn(ev)); }
    return ev;
  }
  return { doc, fire, get active() { return ACTIVE; }, setActive(n) { ACTIVE = n; } };
}

// ------------------------------------------------------------------ per-check environment
function newEnv() {
  const dom = makeDom();
  const calls = [];                               // recorded fetch URLs (method + path)
  const env = {
    onPrepare: (action) => FX.prepare[action] || FX.prepare_default,
    onExecute: () => FX.execute_ok,
  };
  function respond(fx) { return Promise.resolve({ status: fx.status, json: () => Promise.resolve(fx.body) }); }
  function fetchShim(path, opts) {
    const method = (opts && opts.method) || 'GET';
    calls.push(method + ' ' + path);
    // A real fetch never throws synchronously; a network / handler failure surfaces as a REJECTED
    // promise. Modelling that here is what exercises app.js's error callbacks (check 25).
    try {
      if (method === 'POST' && path.indexOf('/actions/prepare') >= 0) {
        const b = JSON.parse(opts.body || '{}'); return respond(env.onPrepare(b.action, b));
      }
      if (method === 'POST' && path.indexOf('/actions/execute') >= 0) {
        const b = JSON.parse(opts.body || '{}'); return respond(env.onExecute(b));
      }
      if (path.indexOf('/session') >= 0) return respond(FX.session);
      if (path.indexOf('/overview') >= 0) return respond(FX.overview);
      return respond({ status: 200, body: { data: {}, readiness: 'SESSION7_13_CONSOLE_READY' } });
    } catch (e) { return Promise.reject(e); }
  }
  const sandbox = { document: dom.doc, navigator: { clipboard: null }, fetch: fetchShim, console,
    setTimeout: (fn) => 0, clearTimeout: () => {}, setInterval: () => 0,
    window: { location: { hash: '#overview' }, addEventListener() {}, setTimeout: () => 0,
      clearTimeout: () => {}, setInterval: () => 0 } };
  sandbox.globalThis = sandbox;
  vm.createContext(sandbox);
  // expose the closure internals through a test hook injected before the IIFE close
  const hooked = APP_SRC.replace(/\}\)\(\);\s*$/,
    '; if (typeof window !== "undefined") { window.__api = { startAction: startAction, openModal: openModal,'
    + ' openModalBlocked: openModalBlocked, executeModal: executeModal, closeModal: closeModal,'
    + ' wireModal: wireModal, refreshExecuteEnabled: refreshExecuteEnabled, modalState: modalState }; } })();');
  vm.runInContext(hooked, sandbox, { filename: 'app.js' });
  const api = sandbox.window.__api;
  api.wireModal();
  return { dom, env, calls, api };
}
const flush = async () => { for (let i = 0; i < 60; i++) await new Promise(r => setImmediate(r)); };

// subtree text + all attribute values (for secret-leak assertions)
function collectAll(node) {
  let s = node.nodeType === 3 ? node._data : '';
  if (node.attributes) for (const k in node.attributes) s += ' ' + node.attributes[k];
  s += ' ' + (node.value || '');
  (node.childNodes || []).forEach(c => { s += ' ' + collectAll(c); });
  return s;
}

// ------------------------------------------------------------------ checks
const results = [];
function record(name, ok, detail) { results.push({ name, ok: !!ok, detail: detail || '' }); }
function assert(name, cond, detail) { record(name, cond, cond ? '' : (detail || 'assertion failed')); }

const CONFIRM_ACTION = FX.confirm_action;         // an action whose prepare requires confirmation
const CONFIRM_PHRASE = FX.confirm_phrase;         // its exact confirmation phrase

async function run() {
  // ---- successful prepare renders a complete, non-empty modal (checks 1-9)
  {
    const { dom, api } = newEnv();
    api.startAction('refresh-overview', {}, 'Refresh overview');
    await flush();
    const desc = dom.doc.getElementById('modal-desc').textContent;
    const prep = FX.prepare['refresh-overview'].body.data;
    assert('01_prepare_renders_nonempty', !dom.doc.getElementById('modal-backdrop').hidden && desc.length > 0, 'desc="' + desc + '"');
    assert('02_action_title', dom.doc.getElementById('modal-title').textContent === 'Refresh overview');
    assert('03_canonical_action', dom.doc.getElementById('modal-canonical').textContent.indexOf('refresh-overview') >= 0);
    assert('04_authority', desc.indexOf(prep.expected_authority) >= 0);
    assert('05_expected_effect', desc.indexOf(prep.expected_effect) >= 0);
    assert('06_network_use', desc.indexOf(prep.network_use) >= 0);
    assert('07_local_state_change', desc.indexOf(prep.local_state_changes) >= 0);
    assert('08_upstream_state_change', desc.indexOf(prep.upstream_state_changes) >= 0);
    assert('09_expiration', desc.indexOf(String(prep.expires_in_seconds)) >= 0);
  }

  // ---- confirmation phrase, input, gating (checks 10-16)
  {
    const { dom, api } = newEnv();
    api.startAction(CONFIRM_ACTION, FX.confirm_params, 'Run watchlist');
    await flush();
    const phrase = CONFIRM_PHRASE;
    assert('10_exact_phrase_rendered', dom.doc.getElementById('modal-phrase-required').textContent.indexOf(phrase) >= 0);
    assert('11_confirmation_input_present', !dom.doc.getElementById('modal-confirm-wrap').hidden);
    assert('12_confirm_initially_disabled', dom.doc.getElementById('modal-execute').disabled === true);
    const input = dom.doc.getElementById('modal-phrase');
    input.value = phrase; dom.fire(input, 'input');
    assert('13_exact_phrase_enables', dom.doc.getElementById('modal-execute').disabled === false);
    input.value = phrase + 'x'; dom.fire(input, 'input');
    assert('14_wrong_phrase_keeps_disabled', dom.doc.getElementById('modal-execute').disabled === true);
    input.value = phrase.toLowerCase(); dom.fire(input, 'input');
    assert('15_case_variation_rejected', phrase.toLowerCase() === phrase || dom.doc.getElementById('modal-execute').disabled === true);
    input.value = phrase + ' '; dom.fire(input, 'input');
    assert('16_extra_whitespace_rejected', dom.doc.getElementById('modal-execute').disabled === true);
  }

  // ---- no secret in the DOM (checks 17-20)
  {
    const { dom, api } = newEnv();
    api.startAction(CONFIRM_ACTION, FX.confirm_params, 'Run watchlist');
    await flush();
    const blob = collectAll(dom.doc.getElementById('modal-backdrop'));
    const token = FX.prepare[CONFIRM_ACTION].body.data.action_token;
    const csrf = FX.session.body.data.csrf_token;
    assert('17_prep_token_not_rendered', token && blob.indexOf(token) < 0, 'token leaked');
    assert('18_csrf_not_rendered', csrf && blob.indexOf(csrf) < 0, 'csrf leaked');
    assert('19_session_data_not_rendered', blob.indexOf('Set-Cookie') < 0 && blob.indexOf('fingerprint') < 0);
    assert('20_endpoint_secret_not_rendered', blob.indexOf(FX.secret_probe) < 0, 'secret probe leaked');
  }

  // ---- failed / blocked / session / csrf prepare (checks 21-25)
  {
    const { dom, env, api } = newEnv();
    env.onPrepare = () => FX.prepare_blocked.BLOCKED;
    api.startAction('run-watchlist', { watchlist_id: 'nope' }, 'Run watchlist');
    await flush();
    const backdrop = dom.doc.getElementById('modal-backdrop');
    const desc = dom.doc.getElementById('modal-desc').textContent;
    assert('21_failed_prepare_not_blank', !backdrop.hidden && desc.length > 0 && dom.doc.getElementById('modal-execute').hidden === true);
    assert('22_blocked_reason_shown', desc.length > 0 && dom.doc.getElementById('modal-execute').hidden);
  }
  {
    const { dom, env, api } = newEnv();
    env.onPrepare = () => FX.prepare_blocked.SESSION;
    api.startAction('run-watchlist', {}, 'Run watchlist'); await flush();
    assert('23_session_expired_reason', dom.doc.getElementById('modal-desc').textContent.indexOf('Session expired') >= 0);
  }
  {
    const { dom, env, api } = newEnv();
    env.onPrepare = () => FX.prepare_blocked.CSRF;
    api.startAction('run-watchlist', {}, 'Run watchlist'); await flush();
    assert('24_csrf_failure_reason', dom.doc.getElementById('modal-desc').textContent.indexOf('Security token') >= 0);
  }
  {
    const { dom, env, api } = newEnv();
    env.onPrepare = () => { throw new Error('boom'); };   // fetch handler throws -> fetch rejects
    let threw = false;
    try { api.startAction('run-watchlist', {}, 'Run watchlist'); await flush(); } catch (e) { threw = true; }
    const desc = dom.doc.getElementById('modal-desc').textContent;
    assert('25_js_exception_bounded', !threw && !dom.doc.getElementById('modal-backdrop').hidden && desc.length > 0, 'threw=' + threw + ' desc=' + desc);
  }

  // ---- cancel / escape / focus-return (checks 26-28)
  {
    const { dom, api } = newEnv();
    const trigger = dom.doc.createElement('button'); dom.setActive(trigger);
    api.startAction('refresh-overview', {}, 'Refresh overview'); await flush();
    dom.fire(dom.doc.getElementById('modal-cancel'), 'click');
    assert('26_cancel_closes', dom.doc.getElementById('modal-backdrop').hidden === true);
  }
  {
    const { dom, api } = newEnv();
    api.startAction('refresh-overview', {}, 'Refresh overview'); await flush();
    dom.fire(dom.doc.getElementById('modal-execute'), 'keydown', { key: 'Escape' });
    assert('27_escape_closes_before_execution', dom.doc.getElementById('modal-backdrop').hidden === true);
  }
  {
    const { dom, api } = newEnv();
    const trigger = dom.doc.createElement('button'); dom.setActive(trigger);
    api.startAction('refresh-overview', {}, 'Refresh overview'); await flush();
    dom.fire(dom.doc.getElementById('modal-execute'), 'keydown', { key: 'Escape' });
    assert('28_focus_returns_to_trigger', dom.active === trigger);
  }

  // ---- enter submits only after exact phrase (check 29)
  {
    const { dom, env, api } = newEnv();
    let executes = 0; env.onExecute = () => { executes++; return FX.execute_ok; };
    api.startAction(CONFIRM_ACTION, FX.confirm_params, 'Run watchlist'); await flush();
    const input = dom.doc.getElementById('modal-phrase');
    input.value = 'wrong'; dom.fire(input, 'input');
    dom.fire(input, 'keydown', { key: 'Enter' }); await flush();
    const afterWrong = executes;
    input.value = CONFIRM_PHRASE; dom.fire(input, 'input');
    dom.fire(input, 'keydown', { key: 'Enter' }); await flush();
    assert('29_enter_submits_only_after_exact', afterWrong === 0 && executes === 1, 'afterWrong=' + afterWrong + ' executes=' + executes);
  }

  // ---- double-click blocked + token consumed once (checks 30-31)
  {
    const { dom, env, api } = newEnv();
    let executes = 0; env.onExecute = () => { executes++; return FX.execute_ok; };
    api.startAction('refresh-overview', {}, 'Refresh overview'); await flush();
    const exec = dom.doc.getElementById('modal-execute');
    // during-execution lock: right after the first click, both controls are disabled
    dom.fire(exec, 'click');
    const lockedCancel = dom.doc.getElementById('modal-cancel').disabled;
    const lockedExec = exec.disabled;
    dom.fire(exec, 'click');                       // synchronous second click while executing
    await flush();
    dom.fire(exec, 'click');                       // post-resolve click (token consumed, exec hidden)
    await flush();
    assert('30_double_click_blocked', executes === 1 && lockedCancel && lockedExec, 'executes=' + executes);
    assert('31_token_consumed_once', executes === 1 && api.modalState.token === null && exec.hidden === true);
  }

  // ---- success / failure / export / refresh / verify result rendering (checks 32-36)
  {
    const { dom, env, api } = newEnv();
    let overviewAfter = 0;
    env.onExecute = () => FX.execute_ok;
    api.startAction('refresh-overview', {}, 'Refresh overview'); await flush();
    const before = api; // count overview gets from here
    const envCallsIdx = 0;
    dom.fire(dom.doc.getElementById('modal-execute'), 'click'); await flush();
    const resEl = dom.doc.getElementById('modal-result');
    assert('32_success_result_rendered', resEl.className === 'ok' && resEl.textContent.indexOf('Completed') >= 0
      && dom.doc.getElementById('modal-backdrop').hidden === false, 'res="' + resEl.textContent + '"');
  }
  {
    const { dom, env, api } = newEnv();
    env.onExecute = () => FX.execute_fail;
    api.startAction('run-watchlist', FX.confirm_params, 'Run watchlist'); await flush();
    const input = dom.doc.getElementById('modal-phrase'); input.value = CONFIRM_PHRASE; dom.fire(input, 'input');
    dom.fire(dom.doc.getElementById('modal-execute'), 'click'); await flush();
    const resEl = dom.doc.getElementById('modal-result');
    assert('33_failure_result_rendered', resEl.className === 'bad' && resEl.textContent.indexOf('Not completed') >= 0
      && resEl.textContent.indexOf('Completed —') < 0, 'res="' + resEl.textContent + '"');
  }
  {
    const { dom, env, api } = newEnv();
    env.onExecute = () => FX.execute_export;
    api.startAction('export-overview', {}, 'Export snapshot'); await flush();
    dom.fire(dom.doc.getElementById('modal-execute'), 'click'); await flush();
    const resEl = dom.doc.getElementById('modal-result');
    // a download anchor to the exports endpoint + at least one safe relative filename
    let anchor = null, listed = false;
    (function walk(n){ if (n.tagName === 'A' && (n.getAttribute('href')||'').indexOf('/exports/overview') >= 0 && n.getAttribute('download')) anchor = n;
      (n.childNodes||[]).forEach(walk); })(resEl);
    const exports = FX.execute_export.body.data.upstream_summary.exports || [];
    listed = exports.length > 0 && resEl.textContent.indexOf(exports[0]) >= 0;
    const noAbs = resEl.textContent.indexOf(':\\') < 0 && resEl.textContent.indexOf(FX.tmp_marker) < 0;
    assert('34_export_usable_result', !!anchor && listed && noAbs, 'anchor=' + !!anchor + ' listed=' + listed + ' noAbs=' + noAbs);
  }
  {
    const { dom, env, calls, api } = newEnv();
    env.onExecute = () => FX.execute_ok;
    api.startAction('refresh-overview', {}, 'Refresh overview'); await flush();
    const base = calls.filter(c => c.indexOf('GET /api/v1/overview') >= 0).length;
    dom.fire(dom.doc.getElementById('modal-execute'), 'click'); await flush();
    const after = calls.filter(c => c.indexOf('GET /api/v1/overview') >= 0).length;
    assert('35_refresh_updates_overview', after > base, 'overview gets before=' + base + ' after=' + after);
  }
  {
    const { dom, env, api } = newEnv();
    env.onExecute = () => FX.execute_ok;
    api.startAction('verify-system-state', {}, 'Verify system state'); await flush();
    dom.fire(dom.doc.getElementById('modal-execute'), 'click'); await flush();
    const resEl = dom.doc.getElementById('modal-result');
    assert('36_verify_state_visible_result', resEl.textContent.indexOf('Completed') >= 0);
  }

  // ---- all 15 actions avoid a blank modal (check 37)
  {
    let blank = [];
    for (const action of FX.all_actions) {
      const { dom, api } = newEnv();
      api.startAction(action, FX.params_for[action] || {}, action); await flush();
      const backdrop = dom.doc.getElementById('modal-backdrop');
      const desc = dom.doc.getElementById('modal-desc').textContent;
      if (backdrop.hidden || desc.trim().length === 0) blank.push(action);
    }
    assert('37_all_15_actions_no_blank', blank.length === 0, 'blank for: ' + blank.join(','));
  }

  // ---- no external request + no unsafe innerHTML (checks 38-39)
  {
    const { dom, env, calls, api } = newEnv();
    env.onExecute = () => FX.execute_ok;
    api.startAction('refresh-overview', {}, 'Refresh overview'); await flush();
    dom.fire(dom.doc.getElementById('modal-execute'), 'click'); await flush();
    const external = calls.filter(c => !/ \/(api\/|styles|app\.js|icons|$)/.test(c) && c.split(' ')[1].indexOf('/') !== 0);
    assert('38_no_external_frontend_request', external.length === 0, 'external: ' + external.join(','));
  }
  assert('39_no_unsafe_innerHTML_with_authority', APP_SRC.indexOf('.innerHTML') < 0, 'app.js uses innerHTML');

  // ---- backdrop click is a deliberate no-dismiss
  {
    const { dom, api } = newEnv();
    api.startAction('refresh-overview', {}, 'Refresh overview'); await flush();
    const backdrop = dom.doc.getElementById('modal-backdrop');
    dom.fire(backdrop, 'click', { target: backdrop });
    assert('40_backdrop_click_no_dismiss', backdrop.hidden === false);
  }
}

run().then(() => {
  let failed = 0;
  for (const r of results) {
    if (r.ok) console.log('PASS ' + r.name);
    else { failed++; console.log('FAIL ' + r.name + ': ' + r.detail); }
  }
  console.log('TOTAL ' + results.length + ' FAILED ' + failed);
  process.exit(failed ? 1 : 0);
}).catch(e => { console.log('HARNESS-ERROR ' + (e && e.stack || e)); process.exit(2); });
