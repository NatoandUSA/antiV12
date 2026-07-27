'use strict';
/* Phase 7.14 owner-console render contract — dependency-free DOM harness.
 *
 * Loads the REAL production app.js into a minimal DOM shim and renders the owner-facing surface from
 * fixtures supplied by the Python test (real envelopes produced by the accepted backend). It proves
 * offline, in a committed test, what the real-browser QA proves interactively: the next-action panel
 * is present and sits above the metrics, every empty state answers what/whether-error/why/next, a
 * disabled control always states its reason, status is never colour-only, navigation is grouped and
 * marks the active page, and no control is dead.
 *
 * No framework, no network: fetch is shimmed. Prints one line per check ("PASS <name>" /
 * "FAIL <name>: <detail>") and exits non-zero if any check fails.
 *
 * Usage: node phase7_14_console_dom_harness.js <fixtures.json>
 */
const fs = require('fs');
const vm = require('vm');

const FX = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const APP_SRC = fs.readFileSync(FX.app_js, 'utf8');

// ------------------------------------------------------------------ minimal DOM
function makeDom() {
  let ACTIVE = null;
  const REGISTRY = {};          // id -> node, mirroring how a real document resolves getElementById
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
      if (k === 'id') { this.id = String(v); REGISTRY[this.id] = this; return; }
      this.attributes[k] = String(v); }
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
    readyState: 'loading',                        // suppress app.js auto-boot; we drive it manually
    _all: REGISTRY,
    createElement(t) { const e = new Node(1); e.tagName = String(t).toUpperCase(); return e; },
    createTextNode(s) { const n = new Node(3); n._data = String(s); return n; },
    getElementById(x) { return REGISTRY[x] || null; },
    addEventListener() {},
    execCommand() { return true; },
    get activeElement() { return ACTIVE; },
  };
  function mk(tag, id) { const e = doc.createElement(tag); if (id) { e.id = id; doc._all[id] = e; } return e; }
  const html = mk('html'); doc.documentElement = html;
  const body = mk('body'); html.appendChild(body); doc.body = body;
  body.appendChild(mk('div', 'status-metrics'));
  const shell = mk('div', 'shell');
  const sidebar = mk('nav', 'sidebar'); const navList = mk('ul', 'nav-list');
  sidebar.appendChild(navList); shell.appendChild(sidebar);
  const toggle = mk('button', 'sidebar-toggle');
  toggle.setAttribute('aria-label', 'Hide or show the menu');
  body.appendChild(toggle);
  const content = mk('main', 'content'); shell.appendChild(content); body.appendChild(shell);
  content.appendChild(mk('nav', 'breadcrumbs'));
  content.appendChild(mk('div', 'global-feedback'));
  content.appendChild(mk('section', 'view-root'));
  body.appendChild(mk('div', 'toast'));
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
  // these mirror production/index.html, including the declared action each control performs
  const mc = mk('button', 'modal-cancel'); mc.setAttribute('data-act', 'modal:cancel');
  const me = mk('button', 'modal-execute'); me.setAttribute('data-act', 'modal:execute');
  modal.appendChild(mc);
  modal.appendChild(me);
  backdrop.appendChild(modal); body.appendChild(backdrop);
  function path(node) { const p = []; let n = node; while (n) { p.push(n); n = n.parentNode; } return p; }
  function fire(target, type, init) {
    const ev = Object.assign({ type, target, _prevented: false,
      preventDefault() { this._prevented = true; }, stopPropagation() {} }, init || {});
    for (const n of path(target)) { (n._listeners[type] || []).forEach(fn => fn(ev)); }
    return ev;
  }
  return { doc, fire, get active() { return ACTIVE; }, setActive(n) { ACTIVE = n; } };
}

// ------------------------------------------------------------------ tree helpers
function walk(node, fn) {
  if (!node) return;
  fn(node);
  (node.childNodes || []).forEach(c => walk(c, fn));
}
function findAll(root, pred) { const out = []; walk(root, n => { if (n.nodeType === 1 && pred(n)) out.push(n); }); return out; }
function find(root, pred) { return findAll(root, pred)[0] || null; }
function hasClass(n, cls) { return (' ' + (n.className || '') + ' ').indexOf(' ' + cls + ' ') >= 0; }
function byClass(root, cls) { return findAll(root, n => hasClass(n, cls)); }
function byTag(root, tag) { return findAll(root, n => n.tagName === tag.toUpperCase()); }

// ------------------------------------------------------------------ environment
function newEnv(overrides) {
  const dom = makeDom();
  const calls = [];
  const responses = Object.assign({
    session: FX.session, overview: FX.overview, activity: FX.activity,
    research: FX.research, backups: FX.backups, system: FX.system, alerts: FX.alerts,
    notifications: FX.notifications, analysis: FX.analysis, watchlists: FX.watchlists
  }, overrides || {});
  function respond(fx) {
    return Promise.resolve({ status: fx.status, json: () => Promise.resolve(fx.body) });
  }
  function fetchShim(path, opts) {
    const method = (opts && opts.method) || 'GET';
    calls.push(method + ' ' + path);
    try {
      if (method === 'POST' && path.indexOf('/actions/prepare') >= 0) return respond(FX.prepare_default);
      if (method === 'POST' && path.indexOf('/actions/execute') >= 0) return respond(FX.execute_ok);
      const keys = ['session', 'overview', 'activity', 'next-action', 'research', 'backups',
                    'system', 'alerts', 'notifications', 'analysis', 'watchlists'];
      for (const k of keys) {
        if (path.indexOf('/' + k) >= 0) {
          const fx = responses[k === 'next-action' ? 'overview' : k];
          if (fx) return respond(fx);
        }
      }
      return respond({ status: 200, body: { data: {}, readiness: 'SESSION7_13_CONSOLE_READY' } });
    } catch (e) { return Promise.reject(e); }
  }
  const sandbox = { document: dom.doc, navigator: { clipboard: null }, fetch: fetchShim, console,
    setTimeout: (fn) => 0, clearTimeout: () => {}, setInterval: () => 0,
    window: { location: { hash: '#overview' }, addEventListener() {}, setTimeout: () => 0,
      clearTimeout: () => {}, setInterval: () => 0 } };
  sandbox.globalThis = sandbox;
  vm.createContext(sandbox);
  const hooked = APP_SRC.replace(/\}\)\(\);\s*$/,
    '; if (typeof window !== "undefined") { window.__api = { route: route, buildNav: buildNav,'
    + ' setActive: setActive, renderOverview: renderOverview, renderResearch: renderResearch,'
    + ' renderManualActions: renderManualActions, renderFollowups: renderFollowups,'
    + ' renderAlerts: renderAlerts, renderBackups: renderBackups, renderSystem: renderSystem,'
    + ' renderActivity: renderActivity, renderAnalysis: renderAnalysis,'
    + ' nextActionPanel: nextActionPanel, emptyState: emptyState, disabledBtn: disabledBtn,'
    + ' tag: tag, ownerTag: ownerTag, setFeedback: setFeedback, wireModal: wireModal,'
    + ' startAction: startAction, modalState: modalState, VIEWS: VIEWS, NAV_GROUPS: NAV_GROUPS,'
    + ' STATUS: STATUS, ownerState: ownerState, wireSidebarToggle: wireSidebarToggle,'
    + ' refreshStatusBar: refreshStatusBar }; } })();');
  vm.runInContext(hooked, sandbox, { filename: 'app.js' });
  return { dom, calls, api: sandbox.window.__api, sandbox };
}
const flush = async () => { for (let i = 0; i < 80; i++) await new Promise(r => setImmediate(r)); };

// ------------------------------------------------------------------ checks
const results = [];
function assert(name, cond, detail) { results.push({ name, ok: !!cond, detail: cond ? '' : (detail || 'assertion failed') }); }

async function run() {
  // ---- Overview hierarchy + next-action panel -----------------------------------------------
  {
    const { dom, api } = newEnv();
    api.buildNav(); api.renderOverview(); await flush();
    const root = dom.doc.getElementById('view-root');
    const blocks = root.children.map(c => c.getAttribute('data-block'));
    assert('01_overview_renders', byTag(root, 'H1').length === 1, 'h1 count=' + byTag(root, 'H1').length);
    assert('02_next_action_panel_present', blocks.indexOf('next-action') >= 0, blocks.join(','));
    assert('03_next_action_above_metrics',
      blocks.indexOf('next-action') >= 0 && blocks.indexOf('counts') >= 0
      && blocks.indexOf('next-action') < blocks.indexOf('counts'), blocks.join(','));
    assert('04_next_action_above_modules',
      blocks.indexOf('next-action') < blocks.indexOf('modules'), blocks.join(','));
    assert('05_heading_first', blocks[0] === 'heading', blocks.join(','));
    assert('06_technical_last', blocks[blocks.length - 1] === 'technical', blocks.join(','));

    const na = dom.doc.getElementById('next-action');
    const t = byClass(na, 'na-title')[0], m = byClass(na, 'na-message')[0];
    const why = byClass(na, 'na-why')[0], step = byClass(na, 'na-step')[0];
    const exp = byClass(na, 'na-expected')[0];
    assert('07_na_title', t && t.textContent === FX.overview.body.data.next_action.owner_title, t && t.textContent);
    assert('08_na_message', m && m.textContent === FX.overview.body.data.next_action.owner_message, m && m.textContent);
    assert('09_na_why', why && why.textContent.indexOf(FX.overview.body.data.next_action.why_it_matters) >= 0);
    assert('10_na_step', step && step.textContent.indexOf(FX.overview.body.data.next_action.recommended_step) >= 0);
    assert('11_na_expected', exp && exp.textContent.indexOf(FX.overview.body.data.next_action.expected_result) >= 0);
    const cta = byClass(na, 'na-cta')[0];
    assert('12_na_cta_navigates', cta && cta.getAttribute('href') === '#analysis',
      cta ? cta.getAttribute('href') : 'no cta');
    assert('13_na_priority_shown', byClass(na, 'na-rank')[0].textContent.indexOf('Priority') >= 0);
    // the whole panel is free of internal ids / hashes above the owner's actions
    assert('14_no_schema_token_in_na', na.textContent.indexOf('SESSION7_14') < 0
      && na.textContent.indexOf('phase7.14') < 0, na.textContent.slice(0, 120));
  }

  // ---- next-action: every destination kind ---------------------------------------------------
  {
    const { dom, api } = newEnv();
    const inst = api.nextActionPanel({
      priority: 3, priority_of: 14, readiness: 'SESSION7_14_NEXT_ACTION_READY',
      canonical_status: 'SESSION7_14_USABILITY_REQUIRED', owner_title: 'T', owner_message: 'M',
      why_it_matters: 'W', recommended_step: 'S', expected_result: 'E',
      destination: { type: 'instructions', steps: ['one', 'two'], command: 'python -m x' }
    });
    assert('15_instructions_rendered', byTag(inst, 'OL').length === 1 && byTag(inst, 'LI').length === 2);
    const cmdBtn = findAll(inst, n => n.getAttribute('data-act') === 'copy:command')[0];
    assert('16_command_copyable', !!cmdBtn && byTag(inst, 'CODE')[0].textContent === 'python -m x');
    assert('17_command_has_sr_label', cmdBtn.getAttribute('aria-label').indexOf('python -m x') >= 0);

    const un = api.nextActionPanel({
      priority: 12, priority_of: 14, readiness: 'SESSION7_14_NEXT_ACTION_READY',
      canonical_status: 'SESSION7_14_OWNER_ACTION_REQUIRED', owner_title: 'T', owner_message: 'M',
      why_it_matters: 'W', recommended_step: 'S', expected_result: 'E',
      destination: { type: 'unavailable', reason: 'The backup workspace does not exist yet.',
                     page: 'backups', page_label: 'Backup & Recovery' }
    });
    const dis = findAll(un, n => n.tagName === 'BUTTON' && n.disabled)[0];
    assert('18_unavailable_is_disabled', !!dis, 'no disabled control');
    assert('19_unavailable_states_reason',
      byClass(un, 'disabled-why')[0].textContent.indexOf('backup workspace') >= 0);
    assert('20_unavailable_reason_is_described',
      dis.getAttribute('aria-describedby') === byClass(un, 'disabled-why')[0].id);

    const none = api.nextActionPanel(null);
    assert('21_missing_guidance_degrades', byClass(none, 'na-title').length === 1
      && byClass(none, 'na-step').length === 1);

    const bad = api.nextActionPanel({
      priority: 1, priority_of: 14, readiness: 'SESSION7_14_NEXT_ACTION_BLOCKED',
      canonical_status: 'SESSION7_14_USABILITY_BLOCKED', owner_title: 'T', owner_message: 'M',
      why_it_matters: 'W', recommended_step: 'S', expected_result: 'E',
      destination: { type: 'existing_console_page', page: 'system', page_label: 'System Health' }
    });
    assert('22_blocked_state_visible', hasClass(bad, 'na-bad'), bad.className);
    assert('23_blocked_cta_real', byClass(bad, 'na-cta')[0].getAttribute('href') === '#system');
  }

  // ---- empty states ---------------------------------------------------------------------------
  {
    const { api } = newEnv();
    const es = api.emptyState({ title: 'No current report analysis found', isError: false,
      why: 'because', next: 'do this', route: 'analysis', routeLabel: 'Open Analysis' });
    assert('24_empty_has_title', byClass(es, 'es-title')[0].textContent === 'No current report analysis found');
    assert('25_empty_says_not_error', byClass(es, 'es-error')[0].textContent.indexOf('not an error') >= 0);
    assert('26_empty_has_why', byClass(es, 'es-why')[0].textContent === 'because');
    assert('27_empty_has_next', byClass(es, 'es-next')[0].textContent.indexOf('do this') >= 0);
    assert('28_empty_cta_real', byClass(es, 'es-cta')[0].getAttribute('href') === '#analysis');
    assert('29_empty_never_bare_no_data', es.textContent.trim() !== 'No data');
    const err = api.emptyState({ title: 'x', isError: true, why: 'w', next: 'n' });
    assert('30_empty_can_flag_error', byClass(err, 'es-error')[0].textContent.indexOf('needs your attention') >= 0);
  }

  // ---- a table with zero rows still explains itself ---------------------------------------------
  {
    const { dom, api } = newEnv({ research: { status: 200, body: { readiness: 'SESSION7_13_CONSOLE_READY',
      data: { status: 'READY_EMPTY', counts: { runs: 0 }, runs: { data: [], page: 1, page_size: 50, total: 0, total_pages: 0 } } } } });
    api.renderResearch(); await flush();
    const root = dom.doc.getElementById('view-root');
    const es = byClass(root, 'empty-state')[0];
    assert('31_empty_table_has_empty_state', !!es, 'no empty state rendered');
    assert('32_empty_table_title', byClass(es, 'es-title')[0].textContent.indexOf('No research run') >= 0);
    assert('33_empty_table_next_step', byClass(es, 'es-next')[0].textContent.indexOf('watchlist') >= 0);
    assert('34_empty_table_no_bare_row', byTag(root, 'TABLE').length === 0);
  }

  // ---- a table with rows: search, reset, sort, pagination, sticky, count, copy id ---------------
  {
    const { dom, api } = newEnv();
    api.renderResearch(); await flush();
    const root = dom.doc.getElementById('view-root');
    assert('35_table_rendered', byTag(root, 'TABLE').length === 1);
    assert('36_table_total_count', byTag(root, 'CAPTION')[0].textContent.indexOf('record(s)') >= 0);
    assert('37_table_search', findAll(root, n => n.getAttribute('data-act') === 'table:search').length === 1);
    assert('38_table_reset_filters', byClass(root, 'reset-filters').length === 1);
    assert('39_table_page_size', findAll(root, n => n.getAttribute('data-act') === 'table:page-size').length === 1);
    assert('40_table_sortable', findAll(root, n => n.getAttribute('data-act') === 'table:sort').length >= 1);
    assert('41_table_pager', byClass(root, 'pager').length === 1);
    assert('42_table_copy_id', findAll(root, n => n.getAttribute('data-act') === 'copy:id').length >= 1);
    assert('43_sort_control_labelled',
      findAll(root, n => n.getAttribute('data-act') === 'table:sort')[0].getAttribute('aria-label').indexOf('Sort by') === 0);
    assert('44_search_labelled',
      findAll(root, n => n.getAttribute('data-act') === 'table:search')[0].getAttribute('aria-label').indexOf('Search') === 0);
    // pagination is bounded: prev is disabled on page 1 and explains why
    const prev = findAll(root, n => n.getAttribute('data-act') === 'table:prev')[0];
    assert('45_pager_bounded', prev.disabled === true && !!prev.getAttribute('title'), prev.getAttribute('title'));
  }

  // ---- status is never colour-only --------------------------------------------------------------
  {
    const { dom, api } = newEnv();
    api.renderOverview(); await flush();
    const root = dom.doc.getElementById('view-root');
    const tags = byClass(root, 'status-tag');
    assert('46_status_tags_present', tags.length > 0, 'none');
    assert('47_every_status_has_glyph', tags.every(t => byClass(t, 'glyph').length === 1));
    assert('48_every_status_has_shape', tags.every(t => byClass(t, 'dot').length === 1));
    assert('49_every_status_has_word', tags.every(t => t.textContent.trim().length > 0));
    assert('50_every_status_has_state_attr', tags.every(t => !!t.getAttribute('data-state')));
    const states = Object.keys(api.STATUS);
    assert('51_status_vocabulary_complete',
      ['READY', 'READY_EMPTY', 'ACTION_REQUIRED', 'BLOCKED', 'STALE', 'UNKNOWN', 'NEEDS_SETUP']
        .every(s => states.indexOf(s) >= 0), states.join(','));
    assert('52_glyphs_distinct',
      new Set(states.map(s => api.STATUS[s].glyph)).size === states.length,
      states.map(s => api.STATUS[s].glyph).join(''));
    assert('53_unknown_is_honest', api.ownerState('SOMETHING_NEW') === 'UNKNOWN');
  }

  // ---- progressive disclosure -------------------------------------------------------------------
  {
    const { dom, api } = newEnv();
    api.renderOverview(); await flush();
    const root = dom.doc.getElementById('view-root');
    const dets = byTag(root, 'DETAILS');
    assert('54_disclosure_present', dets.length >= 1, 'count=' + dets.length);
    assert('55_disclosure_closed_by_default', dets.every(d => d.getAttribute('open') === null));
    assert('56_disclosure_has_summary', dets.every(d => byTag(d, 'SUMMARY').length === 1));
    const techBlock = root.children.filter(c => c.getAttribute('data-block') === 'technical')[0];
    assert('57_canonical_readiness_behind_disclosure',
      techBlock.textContent.indexOf('Canonical readiness') >= 0
      && byTag(techBlock, 'DETAILS').length >= 1);
    const headBlock = root.children.filter(c => c.getAttribute('data-block') === 'heading')[0];
    assert('58_no_canonical_token_in_heading',
      headBlock.textContent.indexOf('SESSION7_13') < 0, headBlock.textContent);
  }

  // ---- button contract: nothing dead ------------------------------------------------------------
  {
    const { dom, api } = newEnv();
    api.buildNav(); api.wireSidebarToggle(); api.renderOverview(); await flush();
    const undeclared = [];
    walk(dom.doc.body, n => {
      if (n.nodeType !== 1) return;
      if (n.tagName !== 'BUTTON' && !(n.tagName === 'A' && hasClass(n, 'btn'))) return;
      const wired = n.getAttribute('data-act') || n.getAttribute('href') || n.disabled;
      if (!wired) undeclared.push(n.textContent.trim().slice(0, 30));
    });
    assert('59_no_dead_control', undeclared.length === 0, undeclared.join(' | '));
    const disabled = findAll(dom.doc.body, n => n.tagName === 'BUTTON' && n.disabled);
    assert('60_disabled_controls_explain',
      disabled.every(b => b.getAttribute('title') || b.getAttribute('aria-describedby')),
      disabled.map(b => b.textContent).join(','));
  }

  // ---- disabled row action states its reason -----------------------------------------------------
  {
    const { api } = newEnv();
    const d = api.disabledBtn('Run now', 'This watchlist is switched off.');
    const btn = findAll(d, n => n.tagName === 'BUTTON')[0];
    assert('61_disabled_is_disabled', btn.disabled === true);
    assert('62_disabled_has_title', btn.getAttribute('title') === 'This watchlist is switched off.');
    assert('63_disabled_reason_visible',
      byClass(d, 'disabled-why')[0].textContent === 'This watchlist is switched off.');
    assert('64_disabled_reason_linked',
      btn.getAttribute('aria-describedby') === byClass(d, 'disabled-why')[0].id);
  }

  // ---- navigation: groups, active state, breadcrumb ------------------------------------------------
  {
    const { dom, api } = newEnv();
    api.buildNav();
    const nav = dom.doc.getElementById('nav-list');
    const groups = byClass(nav, 'nav-group-title').map(g => g.textContent);
    assert('65_sidebar_groups', groups.length === 3 && groups.indexOf('OPERATIONS') >= 0
      && groups.indexOf('INTELLIGENCE') >= 0 && groups.indexOf('SYSTEM') >= 0, groups.join(','));
    const links = byTag(nav, 'A');
    assert('66_every_view_navigable', links.length === api.VIEWS.length,
      links.length + ' vs ' + api.VIEWS.length);
    assert('67_nav_links_declare_action', links.every(a => (a.getAttribute('data-act') || '').indexOf('nav:') === 0));
    assert('68_nav_href_is_hash', links.every(a => (a.getAttribute('href') || '').indexOf('#') === 0));
    api.setActive('alerts');
    const current = links.filter(a => a.getAttribute('aria-current') === 'page');
    assert('69_single_active_page', current.length === 1 && current[0].getAttribute('href') === '#alerts',
      current.map(a => a.getAttribute('href')).join(','));
    const bc = dom.doc.getElementById('breadcrumbs');
    assert('70_breadcrumb_rendered', bc.textContent.indexOf('Console') >= 0
      && bc.textContent.indexOf('Alerts') >= 0, bc.textContent);
    assert('71_breadcrumb_shows_group', bc.textContent.indexOf('Intelligence') >= 0, bc.textContent);
    assert('72_breadcrumb_marks_current',
      byClass(bc, 'current')[0].getAttribute('aria-current') === 'page');
    api.setActive('overview');
    assert('73_active_moves', links.filter(a => a.getAttribute('aria-current') === 'page')[0].getAttribute('href') === '#overview');
  }

  // ---- every next-action page id maps to a real rendered view ---------------------------------------
  {
    const { api } = newEnv();
    const pages = ['overview', 'analysis', 'research', 'watchlists', 'alerts', 'notifications',
                   'backups', 'system', 'activity'];
    const ids = api.VIEWS.map(v => v.id);
    const missing = pages.filter(p => ids.indexOf(p) < 0);
    assert('74_all_destination_pages_exist', missing.length === 0, 'missing: ' + missing.join(','));
    assert('75_section_routes_exist',
      ['analysis', 'manual-actions', 'followups'].every(r => ids.indexOf(r) >= 0), ids.join(','));
  }

  // ---- routing keeps the hash and renders the right page -----------------------------------------
  {
    const { dom, api, sandbox } = newEnv();
    api.buildNav();
    sandbox.window.location.hash = '#system';
    api.route(); await flush();
    const root = dom.doc.getElementById('view-root');
    assert('76_hash_routes_to_view', byTag(root, 'H1')[0].textContent === 'System Health',
      byTag(root, 'H1')[0].textContent);
    assert('77_hash_marks_active',
      dom.doc.getElementById('nav-system').getAttribute('aria-current') === 'page');
    sandbox.window.location.hash = '#alerts-view';        // accepted legacy route
    api.route(); await flush();
    assert('78_legacy_route_still_works',
      dom.doc.getElementById('nav-alerts').getAttribute('aria-current') === 'page');
    sandbox.window.location.hash = '#not-a-real-page';
    api.route(); await flush();
    assert('79_unknown_route_falls_back',
      dom.doc.getElementById('nav-overview').getAttribute('aria-current') === 'page');
  }

  // ---- feedback states ------------------------------------------------------------------------------
  {
    const { dom, api } = newEnv();
    const box = dom.doc.getElementById('global-feedback');
    const kinds = ['loading', 'ready', 'no-change', 'completed', 'blocked', 'failed',
                   'session-expired', 'stale'];
    const seen = {};
    kinds.forEach(k => {
      api.setFeedback(k, 'detail');
      seen[k] = { cls: box.className, text: box.textContent, state: box.getAttribute('data-state') };
    });
    assert('80_all_feedback_states_render', kinds.every(k => seen[k].text.length > 0));
    assert('81_feedback_states_distinguishable',
      new Set(kinds.map(k => seen[k].text)).size === kinds.length,
      kinds.map(k => seen[k].text).join(' / '));
    assert('82_feedback_carries_state_attr', kinds.every(k => seen[k].state === k));
    assert('83_failure_is_not_a_toast_class', seen['failed'].cls.indexOf('bad') >= 0, seen['failed'].cls);
    assert('84_session_expired_distinct', seen['session-expired'].text.indexOf('Session expired') >= 0);
    api.setFeedback(null);
    assert('85_feedback_clears', box.textContent === '');
  }

  // ---- a 401 renders a persistent session-expired panel, never a silent failure ---------------------
  {
    const { dom, api } = newEnv({ research: { status: 401, body: { error: 'SESSION_REQUIRED',
      readiness: 'SESSION7_13_SESSION_REQUIRED' } } });
    api.renderResearch(); await flush();
    const root = dom.doc.getElementById('view-root');
    const fb = dom.doc.getElementById('global-feedback');
    assert('86_session_expired_panel', root.textContent.indexOf('session expired') >= 0
      || root.textContent.indexOf('Your local session expired') >= 0, root.textContent.slice(0, 160));
    assert('87_session_expired_feedback', fb.getAttribute('data-state') === 'session-expired');
    assert('88_session_expired_reassures',
      root.textContent.indexOf('Nothing was changed') >= 0, root.textContent.slice(0, 200));
  }

  // ---- a rejected request surfaces visibly ------------------------------------------------------------
  {
    const { dom, api } = newEnv({ research: { status: 400, body: { error: 'QUERY_VALUE_TOO_LONG',
      detail: 'filter' } } });
    api.renderResearch(); await flush();
    const root = dom.doc.getElementById('view-root');
    assert('89_rejected_request_visible', root.textContent.indexOf('QUERY_VALUE_TOO_LONG') >= 0);
    assert('90_rejected_request_feedback',
      dom.doc.getElementById('global-feedback').getAttribute('data-state') === 'failed');
  }

  // ---- a dead server is explained in owner language, with the fix ---------------------------------------
  {
    const dom0 = makeDom();
    const sandbox = { document: dom0.doc, navigator: { clipboard: null },
      fetch: () => Promise.reject(new Error('down')), console,
      setTimeout: () => 0, clearTimeout: () => {}, setInterval: () => 0,
      window: { location: { hash: '#overview' }, addEventListener() {}, setTimeout: () => 0,
        clearTimeout: () => {}, setInterval: () => 0 } };
    sandbox.globalThis = sandbox;
    vm.createContext(sandbox);
    const hooked = APP_SRC.replace(/\}\)\(\);\s*$/,
      '; if (typeof window !== "undefined") { window.__api = { renderOverview: renderOverview }; } })();');
    vm.runInContext(hooked, sandbox, { filename: 'app.js' });
    sandbox.window.__api.renderOverview(); await flush();
    const root = dom0.doc.getElementById('view-root');
    assert('91_dead_server_explained', root.textContent.indexOf('did not answer') >= 0, root.textContent.slice(0, 160));
    assert('92_dead_server_gives_fix', root.textContent.indexOf('Start-AMZ-Toolkit') >= 0);
  }

  // ---- no owner-facing PPC / causal wording in any rendered page ------------------------------------------
  // Seller-account vocabulary is deliberately NOT banned page-wide: System Health must be able to
  // name each seller destination in order to show it is refused. That vocabulary IS banned inside
  // the next-action guidance, which the Python suite enforces against every rule.
  {
    const banned = ['advertising analysis', 'ppc', 'campaign', 'bid', 'ad group',
                    'caused', 'proves', 'guaranteed', 'will increase', 'roi'];
    const pages = ['renderOverview', 'renderResearch', 'renderAlerts', 'renderBackups',
                   'renderSystem', 'renderActivity', 'renderManualActions', 'renderFollowups'];
    const hits = [];
    const rx = banned.map(b => [b, new RegExp('(?<![a-z0-9])' + b.replace(/ /g, '\\s+') + '(?![a-z0-9])')]);
    for (const p of pages) {
      const { dom, api } = newEnv();
      api[p](); await flush();
      const txt = dom.doc.getElementById('view-root').textContent.toLowerCase();
      rx.forEach(pair => { if (pair[1].test(txt)) hits.push(p + ':' + pair[0]); });
    }
    assert('93_no_banned_owner_wording', hits.length === 0, hits.join(' | '));
  }

  // ---- System Health states the permanent Amazon boundary --------------------------------------------------
  {
    const { dom, api } = newEnv();
    api.renderSystem(); await flush();
    const txt = dom.doc.getElementById('view-root').textContent;
    assert('93b_boundary_named', txt.indexOf('Seller Central') >= 0 && txt.indexOf('REFUSED') >= 0,
      txt.slice(0, 200));
  }

  // ---- every page renders a single H1 and no heading level is skipped -------------------------------------
  {
    const pages = ['renderOverview', 'renderResearch', 'renderAlerts', 'renderBackups',
                   'renderSystem', 'renderActivity', 'renderManualActions', 'renderFollowups',
                   'renderAnalysis', 'renderNotificationsSafe'];
    const bad = [];
    for (const p of pages) {
      const { dom, api } = newEnv();
      if (!api[p]) continue;
      api[p](); await flush();
      const root = dom.doc.getElementById('view-root');
      const h1 = byTag(root, 'H1').length;
      const h3 = byTag(root, 'H3').length;
      if (h1 !== 1) bad.push(p + ':h1=' + h1);
      if (h3 > 0 && byTag(root, 'H2').length === 0) bad.push(p + ':h3-without-h2');
    }
    assert('94_heading_hierarchy', bad.length === 0, bad.join(','));
  }

  // ---- modal regression: the accepted Phase 7.13 flow is untouched -------------------------------------------
  {
    const { dom, api } = newEnv();
    api.wireModal();
    api.startAction('refresh-overview', {}, 'Refresh now'); await flush();
    assert('95_modal_opens', dom.doc.getElementById('modal-backdrop').hidden === false);
    assert('96_modal_not_blank', dom.doc.getElementById('modal-desc').textContent.length > 0);
    assert('97_modal_titled', dom.doc.getElementById('modal-title').textContent === 'Refresh now');
    const blob = JSON.stringify(dom.doc.getElementById('modal-backdrop').textContent);
    assert('98_modal_hides_token', blob.indexOf(FX.prepare_default.body.data.action_token) < 0);
    assert('99_modal_hides_csrf', blob.indexOf(FX.session.body.data.csrf_token) < 0);
  }

  // ---- status bar shows the next action, badges hide at zero ----------------------------------------------------
  {
    const { dom, api } = newEnv();
    api.buildNav(); api.refreshStatusBar(); await flush();
    const bar = dom.doc.getElementById('status-metrics');
    assert('100_status_bar_zero_counter', bar.textContent.indexOf('Seller-account calls: 0') >= 0,
      bar.textContent);
    assert('101_status_bar_shows_next_action',
      bar.textContent.indexOf(FX.overview.body.data.next_action.owner_title) >= 0, bar.textContent);
    assert('102_badge_hidden_at_zero',
      dom.doc.getElementById('badge-alerts').hidden === true);
    assert('103_badge_shown_when_nonzero',
      dom.doc.getElementById('badge-analysis').hidden === false
      && dom.doc.getElementById('badge-analysis').textContent === '2',
      dom.doc.getElementById('badge-analysis').textContent);
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
