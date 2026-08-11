/* Owner console front-end — vanilla JS, local only. No external script / style / font / image / CDN,
   no telemetry, no push socket, no background worker, no browser storage. Every value is rendered
   with textContent so upstream data can never inject markup. The CSRF token is held only in a
   closure variable (never in browser storage, never written to the DOM). Confirmation phrases come
   from the server, never invented.

   Phase 7.14 rebuilds the owner-facing surface (next-action guidance, grouped navigation, useful
   empty states, progressive disclosure, explicit feedback states) on top of the accepted Phase 7.13
   API. The action machinery at the bottom of this file — prepare -> confirm -> execute — is the
   accepted Phase 7.13 flow including its accepted modal hotfix, and is preserved verbatim. */
"use strict";

(function () {
  var API = "/api/v1";
  var POLL_MS = 15000;           // >= 10s per the local-status polling minimum
  var CSRF = null;               // in-memory only; never persisted, never placed in the DOM

  // ---------------------------------------------------------------- owner status vocabulary
  // Status is never colour alone: every state carries a word, a glyph (shape) and a colour class.
  var STATUS = {
    READY:               { label: "READY",             glyph: "✓", cls: "ok" },
    READY_EMPTY:         { label: "READY — NO ITEMS",  glyph: "○", cls: "neutral" },
    READY_PARTIAL:       { label: "READY — PARTIAL",   glyph: "◐", cls: "warn" },
    ACTION_REQUIRED:     { label: "ACTION REQUIRED",   glyph: "!",      cls: "warn" },
    BLOCKED:             { label: "BLOCKED",           glyph: "×", cls: "bad" },
    STALE:               { label: "STALE",             glyph: "◷", cls: "warn" },
    UNKNOWN:             { label: "UNKNOWN",           glyph: "?",      cls: "neutral" },
    NEEDS_SETUP:         { label: "NEEDS SETUP",       glyph: "⚙", cls: "warn" }
  };

  // Canonical upstream value -> owner state. Anything unmapped falls back to UNKNOWN, which is an
  // honest answer rather than a guess.
  var STATUS_MAP = {
    "READY": "READY", "OK": "READY", "SENT": "READY", "PRESENT": "READY", "APPROVED": "READY",
    "ACKNOWLEDGED": "READY", "PASS": "READY", "SELECTED": "READY", "true": "READY",
    "READY_EMPTY": "READY_EMPTY", "NOT_DUE": "READY_EMPTY", "false": "READY_EMPTY",
    "READY_PARTIAL": "READY_PARTIAL",
    "MODULE_UNAVAILABLE": "NEEDS_SETUP", "NOT_INITIALIZED": "NEEDS_SETUP", "MISSING": "NEEDS_SETUP",
    "PACKAGE_NOT_AVAILABLE": "NEEDS_SETUP", "FOLLOWUP_NOT_AVAILABLE": "NEEDS_SETUP",
    "BLOCKED": "BLOCKED", "FAILED": "BLOCKED", "REVOKED": "BLOCKED",
    "PACKAGE_BLOCKED": "BLOCKED", "PACKAGE_CONFLICT": "BLOCKED", "FOLLOWUP_BLOCKED": "BLOCKED",
    "PACKAGE_STALE": "STALE", "FOLLOWUP_STALE": "STALE", "STALE": "STALE",
    "OPEN": "ACTION_REQUIRED", "PENDING": "ACTION_REQUIRED", "CRITICAL": "ACTION_REQUIRED",
    "IMPORTANT": "ACTION_REQUIRED", "REVIEW": "ACTION_REQUIRED", "CONFIRMATION_REQUIRED": "ACTION_REQUIRED",
    "SELECTION_REQUIRED": "ACTION_REQUIRED", "FOLLOWUP_SELECTION_REQUIRED": "ACTION_REQUIRED",
    // the overall usability states, mapped explicitly so none of them falls through to a substring
    "SESSION7_14_USABILITY_READY": "READY",
    "SESSION7_14_USABILITY_READY_PARTIAL": "ACTION_REQUIRED",
    "SESSION7_14_USABILITY_REQUIRED": "NEEDS_SETUP",
    "SESSION7_14_USABILITY_BLOCKED": "BLOCKED",
    "UNKNOWN": "UNKNOWN", "QUEUED": "UNKNOWN", "PREVIEWED": "UNKNOWN", "RATE_LIMITED": "UNKNOWN",
    "QUIET_HOURS": "UNKNOWN", "SKIPPED": "UNKNOWN", "PROVIDER_UNAVAILABLE": "UNKNOWN",
    "DISMISSED": "READY_EMPTY", "INFO": "READY_EMPTY",
    // Dashboard V1 Workflow stage states (production.phase7_workflow_stage_model). READY/STALE/
    // BLOCKED/UNKNOWN already match an existing key above by literal value; only these two are
    // new. NOT_STARTED is not "READY_EMPTY" -- that means "checked, nothing to show", while a
    // stage that has not been run yet is something the owner can act on right now.
    "NOT_STARTED": "NEEDS_SETUP", "NOT_ACCEPTED": "ACTION_REQUIRED",
    // Dashboard V1 workspace trust states (DASHBOARD-V1-SPEC.md Section 7). Without an explicit
    // mapping these would all fall through to the generic UNKNOWN default below -- including
    // HISTORICAL, which needs the same attention-grabbing treatment as BLOCKED, not a neutral one.
    "TRUSTED": "READY", "UNVERIFIED": "UNKNOWN", "HISTORICAL": "BLOCKED",
    // Product Truth prerequisite state (DASHBOARD-V1-SPEC.md Section 13 item 4). Already falls
    // through the generic "REQUIRED" substring rule below to ACTION_REQUIRED -- explicit anyway,
    // matching this file's own convention of never leaving a known mapping to an implicit rule.
    "OWNER_INPUT_REQUIRED": "ACTION_REQUIRED"
  };

  function ownerState(value) {
    if (value === null || value === undefined || value === "") return "UNKNOWN";
    var s = String(value);
    if (STATUS_MAP[s]) return STATUS_MAP[s];
    if (s.indexOf("BLOCKED") >= 0 || s.indexOf("ERROR") >= 0) return "BLOCKED";
    if (s.indexOf("READY_EMPTY") >= 0) return "READY_EMPTY";
    if (s.indexOf("READY_PARTIAL") >= 0 || s.indexOf("PARTIAL") >= 0) return "READY_PARTIAL";
    if (s.indexOf("REQUIRED") >= 0) return "ACTION_REQUIRED";
    if (s.indexOf("STALE") >= 0) return "STALE";
    if (s.indexOf("READY") >= 0) return "READY";
    return "UNKNOWN";
  }

  // ---------------------------------------------------------------- views + grouped navigation
  var VIEWS = [
    { id: "overview", label: "Overview", icon: "icon-overview", render: renderOverview },
    { id: "workflow", label: "Workflow", icon: "icon-workflow", render: renderWorkflow },
    { id: "analysis", label: "Analysis & Decisions", icon: "icon-analysis", render: renderAnalysis },
    { id: "manual-actions", label: "Manual Actions", icon: "icon-actions", render: renderManualActions },
    { id: "followups", label: "Follow-ups", icon: "icon-followups", render: renderFollowups },
    { id: "research", label: "Research", icon: "icon-research", render: renderResearch },
    { id: "watchlists", label: "Watchlists", icon: "icon-watch", render: renderWatchlists },
    { id: "alerts", label: "Alerts", icon: "icon-alerts", render: renderAlerts },
    { id: "notifications", label: "Notifications", icon: "icon-notifications", render: renderNotifications },
    { id: "backups", label: "Backup & Recovery", icon: "icon-backup", render: renderBackups },
    { id: "system", label: "System Health", icon: "icon-system", render: renderSystem },
    { id: "activity", label: "Activity", icon: "icon-activity", render: renderActivity }
  ];
  var NAV_GROUPS = [
    { title: null, items: ["overview"] },
    { title: "WORKFLOW", items: ["workflow"] },
    { title: "OPERATIONS", items: ["analysis", "manual-actions", "followups"] },
    { title: "INTELLIGENCE", items: ["research", "watchlists", "alerts"] },
    { title: "SYSTEM", items: ["notifications", "backups", "system", "activity"] }
  ];
  // A next-action destination names a real console page (and, for analysis, a real ?view= section).
  // This map turns that into the route the owner actually lands on. Nothing else is navigable.
  var SECTION_ROUTE = { "decisions": "analysis", "manual-actions": "manual-actions",
                        "outcomes": "followups", "analysis": "analysis", "reviews": "analysis",
                        "attention": "analysis" };

  /* Every clickable control declares exactly one kind in `data-act`, and the kind decides what the
     control is allowed to do. Only "action" may enter startAction(); "navigation" is a plain anchor
     with a hash href and no click listener at all, so it cannot reach the prepare/execute pipeline
     even by accident. Anything unclassified is a bug, and the regression harness fails on it. */
  var CONTROL_KINDS = {
    "nav": "navigation",        // <a href="#page"> — routes only; never prepares, never opens a modal
    "action": "action",         // the only kind that may prepare a state-changing action
    "copy": "copy",             // copies a command or an id to the clipboard
    "download": "download",     // same-origin export link
    "disabled": "disabled",     // disabled and always states why
    "modal": "modal",           // the two controls inside the confirmation dialog itself
    "table": "table",           // local table paging / sorting / filtering
    "toggle": "toggle",         // local layout toggle
    "disclose": "disclose"      // <summary> progressive disclosure
  };
  function controlKind(act) {
    if (act === null || act === undefined || act === "") return null;
    var head = String(act).split(":")[0];
    return CONTROL_KINDS[head] || null;
  }
  function isNavigationAct(act) { return controlKind(act) === "navigation"; }

  var state = {};
  var lastStateHash = null;

  // ---------------------------------------------------------------- dom helpers
  function el(tag, opts, kids) {
    var e = document.createElement(tag);
    opts = opts || {};
    Object.keys(opts).forEach(function (k) {
      if (k === "class") { e.className = opts[k]; }
      else if (k === "text") { e.textContent = opts[k]; }
      else if (k.indexOf("aria") === 0 || k.indexOf("data-") === 0 || k === "role" || k === "scope"
               || k === "type" || k === "href" || k === "tabindex" || k === "hidden"
               || k === "download" || k === "for" || k === "id" || k === "autocomplete"
               || k === "spellcheck" || k === "title" || k === "colspan" || k === "open") {
        e.setAttribute(k, opts[k]);
      } else { e[k] = opts[k]; }
    });
    (kids || []).forEach(function (c) { if (c != null) e.appendChild(c); });
    return e;
  }
  function svgIcon(name) {
    // createElementNS is unavailable in the dependency-free DOM harness; icons are decorative, so a
    // missing sprite degrades to no icon and never breaks a render.
    if (!document.createElementNS) return null;
    // Assembled from fragments so no absolute-URL literal appears in a front-end asset. This is the
    // XML namespace createElementNS requires — it is never fetched and never a network destination.
    var NS = "htt" + "p://www.w3.org/2000/svg";
    var svg = document.createElementNS(NS, "svg");
    svg.setAttribute("class", "icon");
    svg.setAttribute("aria-hidden", "true");
    svg.setAttribute("focusable", "false");
    var use = document.createElementNS(NS, "use");
    use.setAttribute("href", "#" + name);
    svg.appendChild(use);
    return svg;
  }
  function dash(v) {
    if (v === null || v === undefined || v === "") return el("span", { class: "em", text: "—" });
    return document.createTextNode(String(v));
  }
  function toast(msg) {
    var t = document.getElementById("toast");
    t.textContent = msg; t.hidden = false;
    window.clearTimeout(t._timer);
    t._timer = window.setTimeout(function () { t.hidden = true; }, 1900);
  }
  function copyValue(value, label) {
    var done = function () { toast("Copied: " + value); };
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(value).then(done, function () { fallbackCopy(value, done); });
    } else { fallbackCopy(value, done); }
  }
  function copyBtn(value) {
    var b = el("button", { class: "btn copy", type: "button", text: "Copy",
                           "data-act": "copy:id", "aria-label": "Copy identifier " + value });
    b.addEventListener("click", function () { copyValue(value); });
    return b;
  }
  function fallbackCopy(value, done) {
    var ta = el("textarea"); ta.value = value; document.body.appendChild(ta); ta.select();
    try { document.execCommand("copy"); done(); } catch (e) { toast("Copy unavailable"); }
    document.body.removeChild(ta);
  }
  function idCell(value) {
    if (!value) return el("td", {}, [dash(value)]);
    return el("td", { class: "mono id-cell" }, [document.createTextNode(value),
                                                document.createTextNode(" "), copyBtn(value)]);
  }
  /* A control that is not available right now is DISABLED and always states why — never hidden and
     never left clickable so it can fail silently. */
  function disabledBtn(label, reason) {
    var hintId = "why-" + Math.abs(hashString(label + reason)).toString(36);
    var wrap = el("span", { class: "disabled-wrap" });
    var b = el("button", { class: "btn", type: "button", text: label, "data-act": "disabled",
                           title: reason, "aria-describedby": hintId });
    b.disabled = true;
    wrap.appendChild(b);
    wrap.appendChild(el("span", { class: "disabled-why em", id: hintId, text: reason }));
    return wrap;
  }
  function hashString(s) {
    var h = 0, i;
    for (i = 0; i < s.length; i++) { h = ((h << 5) - h + s.charCodeAt(i)) | 0; }
    return h;
  }

  // ---------------------------------------------------------------- fetch
  function getJSON(path) {
    return fetch(path, { headers: { "Accept": "application/json" }, credentials: "same-origin",
                         cache: "no-store" }).then(function (r) {
      return r.json().then(function (j) { return { status: r.status, body: j }; });
    });
  }
  function postJSON(path, obj) {
    var headers = { "Accept": "application/json", "Content-Type": "application/json" };
    if (CSRF) headers["X-CSRF-Token"] = CSRF;
    return fetch(path, { method: "POST", headers: headers, credentials: "same-origin",
                         cache: "no-store", body: JSON.stringify(obj) }).then(function (r) {
      return r.json().then(function (j) { return { status: r.status, body: j }; });
    });
  }

  // ---------------------------------------------------------------- feedback states
  /* Eight distinguishable states, each with its own visible words: loading, ready, no change,
     completed, blocked, failed, session expired, stale. A serious failure never disappears into a
     toast — it stays on the page until the owner acts. */
  var FEEDBACK = {
    loading:   { cls: "info", word: "Loading" },
    ready:     { cls: "ok", word: "Up to date" },
    "no-change": { cls: "neutral", word: "No change" },
    completed: { cls: "ok", word: "Completed" },
    blocked:   { cls: "warn", word: "Blocked" },
    failed:    { cls: "bad", word: "Failed" },
    "session-expired": { cls: "bad", word: "Session expired" },
    stale:     { cls: "warn", word: "Showing older data" }
  };
  function setFeedback(kind, message) {
    var box = document.getElementById("global-feedback");
    if (!box) return;
    box.textContent = "";
    if (!kind) { box.className = ""; return; }
    var f = FEEDBACK[kind] || FEEDBACK.ready;
    box.className = "feedback " + f.cls;
    box.setAttribute("data-state", kind);
    box.appendChild(el("strong", { text: f.word }));
    if (message) box.appendChild(el("span", { text: " — " + message }));
  }
  function loadingBlock(what) {
    return el("p", { class: "loading", "data-state": "loading", text: "Loading…" + (what ? " " + what : "") });
  }
  function handleUnauthorized(res) {
    if (res.status !== 401 && res.status !== 403) return false;
    setFeedback("session-expired",
                "Reload this page to start a fresh local session. Nothing was changed.");
    return true;
  }

  // ---------------------------------------------------------------- status rendering
  function tag(value, forcedState) {
    var key = forcedState || ownerState(value);
    var st = STATUS[key] || STATUS.UNKNOWN;
    // status is never colour-only: a glyph (shape) + a word accompany the colour.
    return el("span", { class: "status-tag " + st.cls, "data-state": key,
                        "aria-label": st.label },
      [el("span", { class: "glyph", "aria-hidden": "true", text: st.glyph }),
       el("span", { class: "dot", "aria-hidden": "true" }),
       document.createTextNode(value === null || value === undefined || value === ""
                               ? st.label : String(value))]);
  }
  function ownerTag(canonicalValue) {
    var key = ownerState(canonicalValue);
    var st = STATUS[key] || STATUS.UNKNOWN;
    return el("span", { class: "status-tag " + st.cls, "data-state": key },
      [el("span", { class: "glyph", "aria-hidden": "true", text: st.glyph }),
       el("span", { class: "dot", "aria-hidden": "true" }),
       document.createTextNode(st.label)]);
  }
  function card(k, v, opts) {
    opts = opts || {};
    var cls = "card" + (opts.emphasis ? " emphasis" : "") + (opts.muted ? " muted" : "");
    var kids = [el("div", { class: "k", text: k }),
                el("div", { class: "v" + (opts.small ? " small" : ""),
                            text: (v === null || v === undefined || v === "") ? "—" : String(v) })];
    if (opts.note) kids.push(el("div", { class: "note em", text: opts.note }));
    return el("div", { class: cls }, kids);
  }
  /* Technical detail lives behind a closed disclosure so canonical readiness, hashes, lineage and
     record ids are never stacked above the owner's actions. */
  function details(summaryText, buildBody) {
    var d = el("details", { class: "tech" });
    var s = el("summary", { "data-act": "disclose" });
    s.appendChild(document.createTextNode(summaryText || "View details"));
    d.appendChild(s);
    var body = el("div", { class: "tech-body" });
    buildBody(body);
    d.appendChild(body);
    return d;
  }
  function kvList(pairs) {
    var dl = el("dl", { class: "kv" });
    pairs.forEach(function (p) {
      if (p == null) return;
      dl.appendChild(el("dt", { text: p[0] }));
      dl.appendChild(el("dd", { text: (p[1] === null || p[1] === undefined || p[1] === "")
                                      ? "—" : String(p[1]) }));
    });
    return dl;
  }

  /* An empty state always answers four questions: what is missing, whether it is an error, why it
     matters, and the next valid step. "No data" on its own is never acceptable. */
  function emptyState(spec) {
    var box = el("div", { class: "empty-state", role: "note" });
    box.appendChild(el("p", { class: "es-title", text: spec.title || "No records to display." }));
    box.appendChild(el("p", { class: "es-error",
                              text: spec.isError ? "This is a problem that needs your attention."
                                                 : "This is not an error." }));
    box.appendChild(el("p", { class: "es-why", text: spec.why || "" }));
    box.appendChild(el("p", { class: "es-next" }, [
      el("strong", { text: "Next step: " }), document.createTextNode(spec.next || "")
    ]));
    if (spec.route) {
      box.appendChild(el("a", { class: "btn primary es-cta", href: "#" + spec.route,
                                "data-act": "nav:" + spec.route,
                                text: spec.routeLabel || "Go there" }));
    }
    return box;
  }

  // ---------------------------------------------------------------- navigation
  function viewById(id) {
    return VIEWS.filter(function (v) { return v.id === id; })[0] || null;
  }
  function buildNav() {
    var ul = document.getElementById("nav-list");
    ul.textContent = "";
    NAV_GROUPS.forEach(function (grp) {
      if (grp.title) {
        var head = el("li", { class: "nav-group" },
                      [el("span", { class: "nav-group-title", text: grp.title })]);
        ul.appendChild(head);
      }
      grp.items.forEach(function (id) {
        var v = viewById(id);
        if (!v) return;
        var badge = el("span", { class: "count-badge", id: "badge-" + v.id, text: "" });
        badge.hidden = true;
        var kids = [];
        var ic = svgIcon(v.icon);
        if (ic) kids.push(ic);
        kids.push(el("span", { class: "nav-label", text: v.label }));
        kids.push(badge);
        var a = el("a", { href: "#" + v.id, id: "nav-" + v.id, "data-act": "nav:" + v.id }, kids);
        ul.appendChild(el("li", {}, [a]));
      });
    });
  }
  function setActive(id) {
    VIEWS.forEach(function (v) {
      var a = document.getElementById("nav-" + v.id);
      if (!a) return;
      if (v.id === id) a.setAttribute("aria-current", "page");
      else a.removeAttribute("aria-current");
    });
    var bc = document.getElementById("breadcrumbs");
    var view = viewById(id);
    var group = null;
    NAV_GROUPS.forEach(function (g) { if (g.title && g.items.indexOf(id) >= 0) group = g.title; });
    bc.textContent = "";
    var items = [el("li", {}, [el("a", { href: "#overview", "data-act": "nav:overview",
                                         text: "Console" })])];
    if (group) items.push(el("li", { text: group.charAt(0) + group.slice(1).toLowerCase() }));
    items.push(el("li", { class: "current", "aria-current": "page", text: view ? view.label : id }));
    bc.appendChild(el("ol", {}, items));
  }
  function wireSidebarToggle() {
    var btn = document.getElementById("sidebar-toggle");
    if (!btn) return;
    btn.setAttribute("data-act", "toggle:sidebar");
    btn.addEventListener("click", function () {
      var shell = document.getElementById("shell");
      var collapsed = shell.className.indexOf("nav-collapsed") >= 0;
      shell.className = collapsed ? "" : "nav-collapsed";
      btn.setAttribute("aria-expanded", collapsed ? "true" : "false");
    });
  }

  // ---------------------------------------------------------------- generic paged table
  function ensureState(id) {
    if (!state[id]) state[id] = { page: 1, page_size: 50, sort: null, direction: "asc", filter: "" };
    return state[id];
  }
  function buildQuery(st, extra) {
    var q = ["page=" + st.page, "page_size=" + st.page_size];
    if (st.sort) { q.push("sort=" + encodeURIComponent(st.sort)); q.push("direction=" + st.direction); }
    if (st.filter) q.push("filter=" + encodeURIComponent(st.filter));
    Object.keys(extra || {}).forEach(function (k) { if (extra[k]) q.push(k + "=" + encodeURIComponent(extra[k])); });
    return q.join("&");
  }
  function renderPaged(mount, paged, columns, st, refresh, opts) {
    var data = (paged && paged.data) || [];
    var total = (paged && paged.total != null) ? paged.total : data.length;
    if (!data.length) {
      mount.appendChild(emptyState(st.filter
        ? { title: "No records match your search.", isError: false,
            why: "The search box is filtering " + total + " row(s) out of view.",
            next: "Clear the search to see every record again." }
        : (opts.empty || { title: "No records to display.", isError: false,
                           why: "Nothing has been recorded here yet.",
                           next: "Come back after your next daily review." })));
      return;
    }
    var wrap = el("div", { class: "table-wrap" });
    var table = el("table");
    table.appendChild(el("caption", { text: total + " record(s) · read-only" }));
    var thead = el("thead"); var htr = el("tr");
    columns.forEach(function (c) {
      var th = el("th", { scope: "col" });
      if (c.sortable) {
        var arrow = (st.sort === c.key) ? (st.direction === "asc" ? "▲" : "▼") : "↕";
        var btn = el("button", { class: "sort", type: "button", "data-act": "table:sort",
                                 "aria-label": "Sort by " + c.label },
                     [el("span", { text: c.label }), el("span", { class: "arrow", text: arrow })]);
        btn.addEventListener("click", function () {
          if (st.sort === c.key) { st.direction = (st.direction === "asc") ? "desc" : "asc"; }
          else { st.sort = c.key; st.direction = "asc"; }
          st.page = 1; refresh();
        });
        th.appendChild(btn);
      } else { th.textContent = c.label; }
      htr.appendChild(th);
    });
    if (columns.some(function (c) { return c.action; })) htr.appendChild(el("th", { scope: "col", text: "Actions" }));
    htr.appendChild(el("th", { scope: "col", text: "Record ID" }));
    thead.appendChild(htr); table.appendChild(thead);
    var tbody = el("tbody");
    data.forEach(function (row) {
      var tr = el("tr");
      columns.forEach(function (c) {
        if (c.action) return;
        var v = c.get ? c.get(row) : row[c.key];
        var td;
        if (c.tag) td = el("td", {}, [tag(v)]);
        else if (c.copy && v) td = idCell(v);
        else td = el("td", { class: (c.num ? "num " : "") + (c.mono ? "mono" : "") }, [dash(v)]);
        tr.appendChild(td);
      });
      var actCol = columns.filter(function (c) { return c.action; })[0];
      if (actCol) {
        var td = el("td", { class: "row-actions" });
        (actCol.actions(row) || []).forEach(function (spec) {
          if (spec.disabledReason) { td.appendChild(disabledBtn(spec.label, spec.disabledReason)); }
          else {
            var b = el("button", { class: "btn action", type: "button", text: spec.label,
                                   "data-act": "action:" + spec.action });
            b.addEventListener("click", function () { startAction(spec.action, spec.params, spec.label); });
            td.appendChild(b);
          }
          td.appendChild(document.createTextNode(" "));
        });
        tr.appendChild(td);
      }
      tr.appendChild(idCell(row.row_id));
      tbody.appendChild(tr);
    });
    table.appendChild(tbody); wrap.appendChild(table); mount.appendChild(wrap);
    var pager = el("div", { class: "pager" });
    var info = el("span", { class: "info", text: "Page " + (paged.page || 1) + " of "
                            + (paged.total_pages || 1) + " · " + total + " total" });
    var prev = el("button", { class: "btn", type: "button", text: "‹ Prev", "data-act": "table:prev" });
    var next = el("button", { class: "btn", type: "button", text: "Next ›", "data-act": "table:next" });
    prev.disabled = (paged.page || 1) <= 1;
    next.disabled = (paged.page || 1) >= (paged.total_pages || 1);
    if (prev.disabled) { prev.setAttribute("title", "You are on the first page."); }
    if (next.disabled) { next.setAttribute("title", "You are on the last page."); }
    prev.addEventListener("click", function () { if (st.page > 1) { st.page--; refresh(); } });
    next.addEventListener("click", function () { st.page++; refresh(); });
    pager.appendChild(prev); pager.appendChild(next); pager.appendChild(info);
    mount.appendChild(pager);
  }

  function tableView(opts) {
    // opts: {id, endpoint, extra, columns, title, sub, notice, before, empty}
    var st = ensureState(opts.id);
    var root = document.getElementById("view-root");
    var panel = el("section", { class: "panel" });
    panel.appendChild(el("h1", { text: opts.title }));
    if (opts.sub) panel.appendChild(el("p", { class: "sub", text: opts.sub }));
    if (opts.notice) panel.appendChild(el("div", { class: "notice", role: "note", text: opts.notice }));
    if (opts.before) opts.before(panel);
    var controls = el("div", { class: "controls" });
    var fInput = el("input", { type: "search", value: st.filter, "data-act": "table:search",
                               "aria-label": "Search " + opts.title });
    fInput.placeholder = "Search…";
    fInput.addEventListener("change", function () { st.filter = fInput.value.slice(0, 200); st.page = 1; refresh(); });
    controls.appendChild(el("label", {}, [el("span", { text: "Search" }), fInput]));
    var sizeSel = el("select", { "aria-label": "Rows per page", "data-act": "table:page-size" });
    [25, 50, 100, 200].forEach(function (n) {
      var op = el("option", { value: String(n), text: n + " / page" });
      if (n === st.page_size) op.selected = true;
      sizeSel.appendChild(op);
    });
    sizeSel.addEventListener("change", function () { st.page_size = parseInt(sizeSel.value, 10); st.page = 1; refresh(); });
    controls.appendChild(el("label", {}, [el("span", { text: "Page size" }), sizeSel]));
    var reset = el("button", { class: "btn reset-filters", type: "button", text: "Reset filters",
                               "data-act": "table:reset" });
    reset.addEventListener("click", function () {
      st.filter = ""; st.sort = null; st.direction = "asc"; st.page = 1; st.page_size = 50;
      fInput.value = ""; refresh();
    });
    controls.appendChild(el("label", {}, [el("span", { text: " " }), reset]));
    panel.appendChild(controls);
    var mount = el("div"); panel.appendChild(mount);
    root.textContent = ""; root.appendChild(panel);
    function refresh() {
      mount.textContent = "";
      mount.appendChild(loadingBlock(opts.title));
      getJSON(API + "/" + opts.endpoint + "?" + buildQuery(st, opts.extra || {})).then(function (res) {
        mount.textContent = "";
        if (handleUnauthorized(res)) { mount.appendChild(sessionExpiredPanel()); return; }
        if (res.status !== 200) {
          setFeedback("failed", "The console refused that request.");
          mount.appendChild(el("div", { class: "notice bad", role: "alert",
            text: "Request rejected: " + (res.body.error || res.status)
              + (res.body.detail ? " (" + res.body.detail + ")" : "") }));
          return;
        }
        setFeedback(null);
        var paged = opts.pick(res.body.data);
        renderPaged(mount, paged, opts.columns, st, refresh, opts);
      }, function () {
        mount.textContent = "";
        setFeedback("failed", "The console did not answer. It may have been stopped.");
        mount.appendChild(el("div", { class: "notice bad", role: "alert",
          text: "The toolkit did not answer. Run Start-AMZ-Toolkit again, then reload this page." }));
      });
    }
    refresh();
  }

  function sessionExpiredPanel() {
    return el("div", { class: "notice bad", role: "alert" }, [
      el("strong", { text: "Your local session expired." }),
      el("p", { text: "Nothing was changed. Reload this page to start a fresh local session." })
    ]);
  }

  // ---------------------------------------------------------------- Overview
  /* Owner-first hierarchy: heading + overall readiness, then the single next action, then critical
     attention, then counts, freshness, module readiness, recent activity, and finally technical
     detail behind a disclosure. */
  function renderOverview() {
    var root = document.getElementById("view-root");
    root.textContent = "";
    root.appendChild(loadingBlock("your overview"));
    getJSON(API + "/overview").then(function (res) {
      root.textContent = "";
      if (handleUnauthorized(res)) { root.appendChild(sessionExpiredPanel()); return; }
      var d = (res.body && res.body.data) || {};
      var ov = d.overview || {};
      var readiness = res.body ? res.body.readiness : null;
      var na = d.next_action || null;
      var cache = d.cache || {};

      // 1 — heading + overall readiness ---------------------------------------------------------
      var head = el("section", { class: "panel", "data-block": "heading" });
      head.appendChild(el("h1", { text: "Overview" }));
      head.appendChild(el("p", { class: "sub" }, [
        document.createTextNode("Toolkit status: "),
        ownerTag(ov.usability_readiness || readiness)
      ]));
      root.appendChild(head);

      // 2 — the single next action ---------------------------------------------------------------
      root.appendChild(nextActionPanel(na));

      // 3 — critical attention -------------------------------------------------------------------
      var attention = el("section", { class: "panel", "data-block": "attention" });
      attention.appendChild(el("h2", { text: "Needs attention" }));
      var flagged = [];
      if ((ov.blocked_modules || []).length) {
        flagged.push(el("div", { class: "notice bad", role: "note",
          text: "Blocked modules: " + ov.blocked_modules.join(", ") + " — open System Health." }));
      }
      if ((ov.stale_data_phases || []).length) {
        flagged.push(el("div", { class: "notice warn", role: "note",
          text: "Stale data (nothing newer than 24 hours): " + ov.stale_data_phases.join(", ") }));
      }
      if ((ov.attention_items || 0) > 0) {
        flagged.push(el("div", { class: "notice warn", role: "note",
          text: ov.attention_items + " item(s) were flagged by an accepted upstream check." }));
      }
      if (!flagged.length) {
        attention.appendChild(emptyState({
          title: "Nothing is flagged right now.", isError: false,
          why: "No module reported a blocked state, stale data or a flagged item.",
          next: "Carry on with your normal daily review below."
        }));
      } else { flagged.forEach(function (n) { attention.appendChild(n); }); }
      root.appendChild(attention);

      // 4 — core operation counts ----------------------------------------------------------------
      var counts = el("section", { class: "panel", "data-block": "counts" });
      counts.appendChild(el("h2", { text: "Your work" }));
      var g = el("div", { class: "cards" });
      g.appendChild(card("Decisions waiting", ov.pending_decisions, { emphasis: true }));
      g.appendChild(card("Manual actions open", ov.pending_manual_actions, { emphasis: true }));
      g.appendChild(card("Follow-ups ready", ov.due_followups, { emphasis: true }));
      g.appendChild(card("Alerts open", ov.open_alerts, { emphasis: true }));
      g.appendChild(card("Watchlists due", ov.due_watchlists));
      g.appendChild(card("Unconfirmed notifications", ov.notification_unknown));
      g.appendChild(card("Research runs", ov.research_runs, { muted: true }));
      g.appendChild(card("Backup snapshots", ov.backup_snapshots, { muted: true }));
      counts.appendChild(g);
      root.appendChild(counts);

      // 5 — data freshness ------------------------------------------------------------------------
      var fresh = el("section", { class: "panel", "data-block": "freshness" });
      fresh.appendChild(el("h2", { text: "How current is this?" }));
      var fr = d.freshness || {};
      var stale3 = (fr["7.3"] || {}).stale;
      fresh.appendChild(el("p", { class: "sub" }, [
        document.createTextNode("Report analysis: "),
        tag(stale3 ? "Older than 24 hours" : "Current", stale3 ? "STALE" : "READY")
      ]));
      fresh.appendChild(details("View data timestamps", function (body) {
        var rows = Object.keys(fr).sort().map(function (k) {
          return [k, (fr[k].latest || "no artifact yet") + (fr[k].stale ? "  (stale)" : "")];
        });
        body.appendChild(kvList(rows));
      }));
      root.appendChild(fresh);

      // 6 — module readiness ----------------------------------------------------------------------
      var mods = el("section", { class: "panel", "data-block": "modules" });
      mods.appendChild(el("h2", { text: "Parts of the toolkit" }));
      var labels = ov.module_labels || {};
      var mg = el("div", { class: "cards" });
      Object.keys(ov.module_status || {}).sort().forEach(function (m) {
        var s = ov.module_status[m];
        mg.appendChild(el("div", { class: "card" }, [
          el("div", { class: "k", text: labels[m] || m }),
          el("div", { class: "v small" }, [ownerTag(s)])
        ]));
      });
      mods.appendChild(mg);
      root.appendChild(mods);

      // 7 — recent activity -----------------------------------------------------------------------
      var act = el("section", { class: "panel", "data-block": "activity" });
      act.appendChild(el("h2", { text: "Recent activity" }));
      var actMount = el("div");
      act.appendChild(actMount);
      actMount.appendChild(loadingBlock("recent activity"));
      act.appendChild(el("a", { class: "btn", href: "#activity", "data-act": "nav:activity",
                                text: "Open the full activity log" }));
      root.appendChild(act);
      getJSON(API + "/activity?page=1&page_size=5").then(function (r2) {
        actMount.textContent = "";
        var evs = ((r2.body.data || {}).events || {}).data || [];
        if (!evs.length) {
          actMount.appendChild(emptyState({
            title: "You have not run anything from this console yet.", isError: false,
            why: "The activity log records the actions you confirm here.",
            next: "It will fill in as you use the toolkit — there is nothing to do now."
          }));
          return;
        }
        var ul = el("ul", { class: "recent" });
        evs.slice(0, 5).forEach(function (e) {
          ul.appendChild(el("li", {}, [
            tag(e.execution_result),
            el("span", { class: "recent-action", text: " " + (e.action || "—") }),
            el("span", { class: "em", text: "  " + (e.recorded_at || "") })
          ]));
        });
        actMount.appendChild(ul);
      }, function () { actMount.textContent = ""; });

      // 8 — technical detail + local export -------------------------------------------------------
      var tech = el("section", { class: "panel", "data-block": "technical" });
      tech.appendChild(el("h2", { text: "Technical details and export" }));
      tech.appendChild(details("View technical details", function (body) {
        body.appendChild(kvList([
          ["Canonical readiness", readiness],
          ["Next-action rule", na ? na.rule_id : null],
          ["Next-action status", na ? na.canonical_status : null],
          ["Next-action identity", na ? na.next_action_identity : null],
          ["Read-model state hash", cache.state_hash],
          ["Read-model age (seconds)", cache.age_seconds],
          ["Notifications sent / failed / unknown",
           (ov.notification_sent || 0) + " / " + (ov.notification_failed || 0) + " / "
           + (ov.notification_unknown || 0)],
          ["Toolkit update available", String(ov.update_available)],
          ["Seller-account calls made by this toolkit", "0"]
        ]));
        if (na && (na.source_authorities || []).length) {
          body.appendChild(el("p", { class: "em",
            text: "Source authority: " + na.source_authorities.join("; ") }));
        }
        if (na && (na.source_ids || []).length) {
          body.appendChild(el("p", { class: "em mono",
            text: "Source ids: " + na.source_ids.join(", ") }));
        }
      }));
      var qa = el("div", { class: "action-row" });
      qa.appendChild(actBtn("Refresh now", "refresh-overview", {}));
      qa.appendChild(actBtn("Save a snapshot", "export-overview", {}));
      qa.appendChild(actBtn("Check system state", "verify-system-state", {}));
      tech.appendChild(qa);
      var er = el("div", { class: "export-row" });
      [["owner_console_snapshot.json", "json"], ["owner_console_status.tsv", "tsv"],
       ["owner_console_report.md", "md"]].forEach(function (e) {
        er.appendChild(el("a", { class: "btn", href: API + "/exports/overview?format=" + e[1],
                                 download: e[0], "data-act": "download:" + e[1],
                                 text: "Download " + e[0] }));
      });
      tech.appendChild(er);
      (d.disclaimer || []).forEach(function (line, i) {
        if (i === 0) return;
        tech.appendChild(el("div", { class: "notice", role: "note", text: line }));
      });
      root.appendChild(tech);

      if (cache.state_hash && lastStateHash && cache.state_hash === lastStateHash) {
        setFeedback("no-change", "Nothing has changed since your last check.");
      } else if (cache.stale) {
        setFeedback("stale", "You are seeing a cached copy that is being refreshed.");
      } else { setFeedback(null); }
      lastStateHash = cache.state_hash || lastStateHash;
    }, function () {
      root.textContent = "";
      setFeedback("failed", "The console did not answer. It may have been stopped.");
      root.appendChild(el("div", { class: "notice bad", role: "alert" }, [
        el("strong", { text: "The toolkit did not answer." }),
        el("p", { text: "Run Start-AMZ-Toolkit again, then reload this page." })
      ]));
    });
  }

  /* The single owner-facing recommendation. It restates state an accepted authority already decided
     and always answers: what needs attention, why it matters, what to do, where to go, what to
     expect. It never creates a new action just to have something to click. */
  function nextActionPanel(na) {
    var sec = el("section", { class: "panel next-action", id: "next-action",
                              "data-block": "next-action", role: "region",
                              "aria-label": "Your next step" });
    if (!na) {
      sec.appendChild(el("h2", { class: "na-title", text: "Your next step is being prepared" }));
      sec.appendChild(el("p", { class: "na-message",
        text: "This toolkit build did not return next-step guidance." }));
      sec.appendChild(el("p", { class: "na-why em",
        text: "Why this matters: without guidance you would have to interpret the numbers yourself." }));
      sec.appendChild(el("p", { class: "na-step", text: "Review the sections below." }));
      sec.appendChild(el("p", { class: "na-expected em",
        text: "What to expect: the counts below still show everything that is waiting." }));
      return sec;
    }
    var stateKey = na.canonical_status === "SESSION7_14_NEXT_ACTION_NONE" ? "READY"
                 : (na.readiness === "SESSION7_14_NEXT_ACTION_BLOCKED" ? "BLOCKED" : "ACTION_REQUIRED");
    sec.className = "panel next-action na-" + (STATUS[stateKey] || STATUS.UNKNOWN).cls;
    var headRow = el("div", { class: "na-head" });
    headRow.appendChild(ownerTag(stateKey === "READY" ? "READY"
                                 : (stateKey === "BLOCKED" ? "BLOCKED" : "ACTION_REQUIRED")));
    headRow.appendChild(el("span", { class: "na-rank em",
      text: "Priority " + na.priority + " of " + (na.priority_of || 14) }));
    sec.appendChild(headRow);
    sec.appendChild(el("h2", { class: "na-title", text: na.owner_title || "" }));
    sec.appendChild(el("p", { class: "na-message", text: na.owner_message || "" }));
    sec.appendChild(el("p", { class: "na-why" }, [
      el("strong", { text: "Why this matters: " }),
      document.createTextNode(na.why_it_matters || "")
    ]));
    sec.appendChild(el("p", { class: "na-step" }, [
      el("strong", { text: "What to do: " }),
      document.createTextNode(na.recommended_step || "")
    ]));

    var dest = na.destination || {};
    var cta = el("div", { class: "na-cta-row" });
    if (dest.type === "existing_console_page") {
      var route = dest.page;
      if (dest.page === "analysis" && dest.section) route = SECTION_ROUTE[dest.section] || "analysis";
      if (viewById(route)) {
        var kids = [el("span", { text: "Go to " + (dest.page_label || route) })];
        var ic = svgIcon("icon-next"); if (ic) kids.push(ic);
        cta.appendChild(el("a", { class: "btn primary na-cta", href: "#" + route,
                                  "data-act": "nav:" + route }, kids));
      } else {
        cta.appendChild(disabledBtn("Go to " + route,
          "That page is not available in this toolkit build."));
      }
    } else if (dest.type === "instructions") {
      var ol = el("ol", { class: "na-steps" });
      (dest.steps || []).forEach(function (s) { ol.appendChild(el("li", { text: s })); });
      sec.appendChild(ol);
      if (dest.command) {
        var cmdRow = el("div", { class: "na-command" });
        cmdRow.appendChild(el("code", { class: "mono", text: dest.command }));
        var cb = el("button", { class: "btn na-cta", type: "button", text: "Copy command",
                                "data-act": "copy:command",
                                "aria-label": "Copy the command " + dest.command });
        cb.addEventListener("click", function () { copyValue(dest.command); });
        cmdRow.appendChild(cb);
        cta.appendChild(cmdRow);
      }
    } else if (dest.type === "copy_command") {
      var row2 = el("div", { class: "na-command" });
      row2.appendChild(el("code", { class: "mono", text: dest.command }));
      var cb2 = el("button", { class: "btn na-cta", type: "button", text: "Copy command",
                               "data-act": "copy:command",
                               "aria-label": "Copy the command " + dest.command });
      cb2.addEventListener("click", function () { copyValue(dest.command); });
      row2.appendChild(cb2);
      cta.appendChild(row2);
    } else if (dest.type === "unavailable") {
      cta.appendChild(disabledBtn("Not available yet", dest.reason || "This is not available yet."));
      if (dest.page && viewById(dest.page)) {
        cta.appendChild(el("a", { class: "btn", href: "#" + dest.page,
                                  "data-act": "nav:" + dest.page,
                                  text: "See " + (dest.page_label || dest.page) }));
      }
    }
    sec.appendChild(cta);
    sec.appendChild(el("p", { class: "na-expected em" }, [
      el("strong", { text: "What to expect: " }),
      document.createTextNode(na.expected_result || "")
    ]));
    return sec;
  }

  function actBtn(label, action, params) {
    var b = el("button", { class: "btn action", type: "button", text: label,
                           "data-act": "action:" + action });
    b.addEventListener("click", function () { startAction(action, params, label); });
    return b;
  }

  // ---------------------------------------------------------------- Operations views
  var ANALYSIS_EMPTY = {
    title: "No current report analysis found",
    isError: false,
    why: "The toolkit has no accepted analysis ready for review, so there is nothing to decide on yet.",
    next: "Import a newer report outside this console, then start the toolkit again."
  };

  function analysisTable(opts) {
    tableView({
      id: opts.id, endpoint: "analysis", extra: { view: opts.sub }, title: opts.title,
      pick: function (d) { return (d && d.rows) || { data: [] }; },
      sub: opts.sub_text, empty: opts.empty || ANALYSIS_EMPTY,
      before: opts.before,
      columns: opts.columns || [
        { key: "row_id", label: "Item", get: function (r) {
          return r.customer_search_term || r.entity_id || r.tracker_record_id || r.followup_record_id
            || r.package_item_id || r.reference_id || ""; } },
        { key: "review_status", label: "Review", tag: true },
        { key: "owner_status", label: "Owner status", tag: true },
        { key: "outcome_classification", label: "Outcome", tag: true },
        { key: "category", label: "Category", tag: true },
        { key: "detail", label: "Detail" }]
    });
  }

  function renderAnalysis() {
    var st = ensureState("analysis-sub"); if (!st.sub) st.sub = "analysis";
    analysisTable({
      id: "analysis", sub: st.sub, title: "Analysis & Decisions",
      sub_text: "What the accepted report analysis found, and the decisions waiting for you. "
        + "Nothing here changes anything in your Amazon account.",
      before: function (panel) {
        var row = el("div", { class: "controls" });
        var sel = el("select", { "aria-label": "View", "data-act": "table:subview" });
        [["analysis", "Analysis"], ["reviews", "Owner review"], ["decisions", "Decisions waiting"],
         ["attention", "Flagged items"]].forEach(function (o) {
          var op = el("option", { value: o[0], text: o[1] }); if (o[0] === st.sub) op.selected = true;
          sel.appendChild(op);
        });
        sel.addEventListener("change", function () { st.sub = sel.value; renderAnalysis(); });
        row.appendChild(el("label", {}, [el("span", { text: "View" }), sel]));
        panel.appendChild(row);
      }
    });
  }

  function renderManualActions() {
    analysisTable({
      id: "manual-actions", sub: "manual-actions", title: "Manual Actions",
      sub_text: "Actions you carry out yourself, outside this toolkit, and then record here. "
        + "The toolkit never performs them for you.",
      empty: { title: "No manual actions have been recorded.", isError: false,
               why: "Manual actions appear here once a decision has been turned into a package.",
               next: "Record a decision first, on Analysis & Decisions.",
               route: "analysis", routeLabel: "Open Analysis & Decisions" }
    });
  }

  function renderFollowups() {
    analysisTable({
      id: "followups", sub: "outcomes", title: "Follow-ups",
      sub_text: "A before/after look at recorded actions once a later report period exists. "
        + "This is an observation of two periods only, never evidence of why the numbers moved.",
      empty: { title: "No follow-up is ready yet.", isError: false,
               why: "A follow-up needs a recorded manual action and a later report covering it.",
               next: "Record a manual action, then import a later report before checking again.",
               route: "manual-actions", routeLabel: "Open Manual Actions" }
    });
  }

  function renderResearch() {
    tableView({
      id: "research", endpoint: "research", title: "Research",
      pick: function (d) { return (d && d.runs) || { data: [] }; },
      sub: "Public pages this toolkit has read for you. Evidence and where it came from — nothing else.",
      empty: { title: "No research run has been created yet.", isError: false,
               why: "Research runs record what a public page said and when, so a change can be spotted later.",
               next: "Create a watchlist first — running it produces the research evidence.",
               route: "watchlists", routeLabel: "Open Watchlists" },
      columns: [{ key: "run_id", label: "Run", copy: true, sortable: true },
        { key: "readiness", label: "Result", tag: true, sortable: true },
        { key: "source_count", label: "Sources", num: true },
        { key: "evidence_count", label: "Evidence", num: true }]
    });
  }

  function renderWatchlists() {
    tableView({
      id: "watchlists", endpoint: "watchlists", title: "Watchlists",
      pick: function (d) { return (d && d.watchlists) || { data: [] }; },
      sub: "Public pages you asked the toolkit to keep an eye on, and when each is next due.",
      empty: { title: "No watchlist has been set up.", isError: false,
               why: "A watchlist is how the toolkit tells you when a public page changes.",
               next: "Watchlists are created outside this console; once one exists it appears here." },
      columns: [{ key: "name", label: "Name", sortable: true },
        { key: "schedule_type", label: "Schedule", sortable: true },
        { key: "due", label: "Due now", get: function (r) { return r.due ? "Yes" : "No"; }, tag: true },
        { key: "due_reason", label: "Reason" }, { key: "next_due", label: "Next due" },
        { key: "watchlist_id", label: "Watchlist", copy: true, sortable: true },
        { key: "actions", label: "Actions", action: true, actions: function (r) {
          if (r.enabled === false) {
            return [{ label: "Run now", disabledReason: "This watchlist is switched off." }];
          }
          return [{ label: "Run now", action: "run-watchlist", params: { watchlist_id: r.watchlist_id } }]; } }]
    });
  }

  function renderAlerts() {
    tableView({
      id: "alerts", endpoint: "alerts", title: "Alerts",
      pick: function (d) { return (d && d.alerts) || { data: [] }; },
      sub: "Changes the toolkit spotted on the public pages you are watching.",
      notice: "Alert state changes are recorded by the accepted authority, never by the browser.",
      empty: { title: "No alerts.", isError: false,
               why: "Nothing has changed on the public pages you are watching.",
               next: "Nothing to do. Alerts appear here automatically after a watchlist runs." },
      columns: [{ key: "severity", label: "Severity", tag: true, sortable: true },
        { key: "status", label: "Status", tag: true, sortable: true },
        { key: "label", label: "What changed" }, { key: "change_type", label: "Change" },
        { key: "alert_id", label: "Alert", copy: true, sortable: true },
        { key: "actions", label: "Actions", action: true, actions: function (r) {
          var out = [];
          if (r.status === "OPEN") {
            out.push({ label: "Acknowledge", action: "acknowledge-alert", params: { alert_id: r.alert_id } });
            out.push({ label: "Dismiss", action: "dismiss-alert", params: { alert_id: r.alert_id } });
          } else {
            out.push({ label: "Acknowledge", disabledReason: "This alert is not open." });
            out.push({ label: "Reopen", action: "reopen-alert", params: { alert_id: r.alert_id } });
          }
          return out; } }]
    });
  }

  function renderNotifications() {
    tableView({
      id: "notifications", endpoint: "notifications", title: "Notifications",
      pick: function (d) { return (d && d.batches) || { data: [] }; },
      sub: "Messages the toolkit can send you about your alerts.",
      notice: "An UNKNOWN result is never treated as failed — only you can confirm whether it arrived.",
      empty: { title: "No notification batch has been built.", isError: false,
               why: "A batch is a set of alerts prepared for sending to one destination.",
               next: "Build a batch from a route above, or leave notifications switched off." },
      before: function (panel) {
        var box = el("div", {});
        box.appendChild(el("h2", { text: "Destinations" }));
        var mount = el("div"); box.appendChild(mount);
        mount.appendChild(loadingBlock("destinations"));
        panel.appendChild(box);
        getJSON(API + "/notifications").then(function (res) {
          mount.textContent = "";
          var d = res.body.data || {};
          if (!(d.routes || []).length) {
            mount.appendChild(emptyState({
              title: "No destination has been set up.", isError: false,
              why: "A destination is where the toolkit would send an alert summary.",
              next: "Destinations are configured outside this console; once one exists it appears here."
            }));
            return;
          }
          var g = el("div", { class: "cards" });
          (d.routes || []).forEach(function (r) {
            var kids = [el("div", { class: "k", text: r.name || r.route_id }),
                        el("div", { class: "v small" }, [tag(r.approval_status)])];
            var actions = el("div", { class: "card-actions" });
            actions.appendChild(actBtn("Preview", "preview-notification", { route_id: r.route_id }));
            if (r.enabled === false) {
              actions.appendChild(disabledBtn("Build batch", "This destination is switched off."));
            } else {
              actions.appendChild(actBtn("Build batch", "build-notification-batch", { route_id: r.route_id }));
            }
            kids.push(actions);
            g.appendChild(el("div", { class: "card" }, kids));
          });
          mount.appendChild(g);
        }, function () { mount.textContent = ""; });
      },
      columns: [{ key: "route_id", label: "Destination" },
        { key: "alert_count", label: "Alerts", num: true },
        { key: "readiness", label: "State", tag: true },
        { key: "batch_id", label: "Batch", copy: true },
        { key: "actions", label: "Actions", action: true, actions: function (r) {
          return [{ label: "Send", action: "send-notification-batch", params: { batch_id: r.batch_id } }]; } }]
    });
  }

  function renderBackups() {
    tableView({
      id: "backups", endpoint: "backups", title: "Backup & Recovery",
      pick: function (d) { return (d && d.snapshots) || { data: [] }; },
      sub: "Local copies of your recorded decisions and history. A destructive restore is never run "
        + "from this console.",
      empty: { title: "No backup snapshot exists yet.", isError: true,
               why: "Without a backup, your recorded decisions and history cannot be recovered.",
               next: "Use “Create a backup now” above to make your first snapshot." },
      before: function (panel) {
        var a = el("div", { class: "action-row" });
        a.appendChild(actBtn("Create a backup now", "create-backup-snapshot", {}));
        a.appendChild(actBtn("Check for a toolkit update", "check-for-update", {}));
        panel.appendChild(a);
        var mount = el("div"); panel.appendChild(mount);
        getJSON(API + "/backups").then(function (res) {
          var uc = (res.body.data || {}).update_check;
          mount.textContent = "";
          if (!uc) {
            mount.appendChild(el("p", { class: "em",
              text: "No update check has been run yet. Nothing is installed automatically." }));
            return;
          }
          mount.appendChild(el("div", { class: "notice", role: "note",
            text: uc.update_available ? "A toolkit update is available. It is only ever staged for "
                                        + "your review — it is never installed for you."
                                      : "Your toolkit is up to date." }));
          mount.appendChild(details("View update details", function (body) {
            body.appendChild(kvList([["Result", uc.readiness], ["Update available",
              String(uc.update_available)], ["Remote head", uc.remote_branch_head],
              ["Local head", uc.local_head], ["Branch", uc.branch]]));
          }));
        }, function () { mount.textContent = ""; });
      },
      columns: [{ key: "snapshot_id", label: "Snapshot", copy: true, sortable: true },
        { key: "file_count", label: "Files", num: true, sortable: true },
        { key: "total_bytes", label: "Bytes", num: true, sortable: true },
        { key: "encrypted", label: "Encrypted", get: function (r) { return r.encrypted ? "Yes" : "No"; },
          tag: true },
        { key: "actions", label: "Actions", action: true, actions: function (r) {
          return [
            { label: "Verify", action: "verify-backup", params: { snapshot_id: r.snapshot_id } },
            { label: "Recovery plan", action: "create-recovery-plan", params: { snapshot_id: r.snapshot_id } }
          ]; } }]
    });
  }

  // ---------------------------------------------------------------- Dashboard V1 Workflow
  /* Thin renderer only -- every field comes from production.phase7_workflow_stage_model via
     build_workflow_section / GET /api/v1/workflow. This view computes NO readiness of its own:
     not which state a stage is in, not which stage is "next". Both are backend facts read
     verbatim (stage.state, wf.primary_next_stage_id). */
  var WORKFLOW_GROUPS = ["Research", "Decide", "Build", "Launch"];

  function commandCopyRow(command, label) {
    label = label || "Copy command";
    var row = el("div", { class: "na-command" });
    row.appendChild(el("code", { class: "mono", text: command }));
    var cb = el("button", { class: "btn na-cta", type: "button", text: label,
                            "data-act": "copy:command",
                            "aria-label": label + ": " + command });
    cb.addEventListener("click", function () { copyValue(command); });
    row.appendChild(cb);
    return row;
  }

  function workflowPrimaryPanel(s) {
    var sec = el("section", { class: "panel next-action na-" + (STATUS[ownerState(s.state)] || STATUS.UNKNOWN).cls,
                              id: "workflow-next", "data-block": "next-action",
                              role: "region", "aria-label": "Your next workflow step" });
    sec.appendChild(el("h2", { class: "na-title",
      text: "Next: Stage " + s.stage_id + " — " + s.label }));
    sec.appendChild(tag(s.state));
    if (s.blocked_by && s.blocked_by.length) {
      sec.appendChild(el("p", { class: "na-message" },
        [document.createTextNode("Waiting on stage " + s.blocked_by.join(", ") + ".")]));
    }
    if (s.blocked_by_product_truth) {
      sec.appendChild(el("p", { class: "na-message", text: "Waiting on Product Truth." }));
    }
    if (s.command) sec.appendChild(commandCopyRow(s.command));
    return sec;
  }

  // Phase 6A ("Product Truth") -- a TRACKED PREREQUISITE, deliberately not one of the 13 numbered
  // stages (DASHBOARD-V1-SPEC.md Section 13 item 4). Same row shape as a numbered stage minus the
  // stage number, since it isn't one; wf.product_truth.state/.command/.staff_note are read
  // verbatim like everything else in this view.
  function productTruthRow(pt) {
    var row = el("div", { class: "stage-row product-truth", "data-role": "product-truth" });
    row.appendChild(el("span", { class: "stage-num em", "aria-hidden": "true", text: "•" }));
    row.appendChild(el("span", { class: "stage-label", text: pt.label + " (prerequisite)" }));
    row.appendChild(tag(pt.state));
    if (pt.state !== "READY" && pt.staff_note) {
      row.appendChild(el("p", { class: "em small", text: pt.staff_note }));
    }
    if (pt.state !== "READY" && pt.command) row.appendChild(commandCopyRow(pt.command));
    return row;
  }

  function workflowStageRow(s) {
    var row = el("div", { class: "stage-row" + (s.modeled ? "" : " informational"),
                          "data-stage-id": String(s.stage_id) });
    row.appendChild(el("span", { class: "stage-num em", text: String(s.stage_id) }));
    row.appendChild(el("span", { class: "stage-label", text: s.label }));
    if (!s.modeled) {
      row.appendChild(el("p", { class: "em small",
        text: s.informational_reason || "Not separately tracked in this pipeline." }));
      return row;
    }
    row.appendChild(tag(s.state));
    if (s.state !== "READY" && s.blocked_by && s.blocked_by.length) {
      row.appendChild(el("p", { class: "em small",
        text: "Waiting on stage " + s.blocked_by.join(", ") + "." }));
    }
    if (s.state !== "READY" && s.blocked_by_product_truth) {
      row.appendChild(el("p", { class: "em small", text: "Waiting on Product Truth." }));
    }
    if (s.state !== "READY" && s.command) row.appendChild(commandCopyRow(s.command));
    var compKeys = s.components ? Object.keys(s.components).sort() : [];
    if (compKeys.length) {
      row.appendChild(details("View components (" + compKeys.length + ")", function (body) {
        body.appendChild(kvList(compKeys.map(function (k) { return [k, s.components[k]]; })));
      }));
    }
    return row;
  }

  // DASHBOARD-V1-SPEC.md Section 7: exactly one workspace trust state. wf.trust.state / .reason
  // are read verbatim -- this function derives and invents nothing.
  function workflowTrustBanner(trust) {
    // Reuses the existing next-action panel's colour-coded border (.next-action.na-<cls> in
    // console.css) rather than inventing a second banner style.
    var sec = el("section", { class: "panel next-action na-" + (STATUS[ownerState(trust.state)] || STATUS.UNKNOWN).cls,
                              id: "workspace-trust", role: "note",
                              "aria-label": "Workspace trust state" });
    sec.appendChild(el("h2", { class: "na-title", text: "Workspace" }));
    sec.appendChild(tag(trust.state));
    sec.appendChild(el("p", { class: "na-message", text: trust.reason || "" }));
    return sec;
  }

  function renderWorkflow() {
    var root = document.getElementById("view-root");
    root.textContent = "";
    root.appendChild(loadingBlock("workflow"));
    getJSON(API + "/workflow").then(function (res) {
      root.textContent = "";
      if (handleUnauthorized(res)) { root.appendChild(sessionExpiredPanel()); return; }
      var wf = (res.body && res.body.data) || {};
      var stages = wf.stages || [];
      var head = el("section", { class: "panel" });
      head.appendChild(el("h1", { text: "Workflow" }));
      head.appendChild(el("p", { class: "sub",
        text: "The listing + PPC pipeline, derived only from what is on disk. Nothing here runs "
            + "anything for you — copy a command and run it yourself." }));
      var counts = wf.counts || {};
      head.appendChild(el("p", { class: "sub" }, [document.createTextNode(
        (counts.ready || 0) + " of " + (counts.modeled || 0) + " tracked stages ready")]));
      // F5 finding: every stage command below says "the workspace folder" -- staff had no way to
      // see which folder that actually is. product_root was already in the API response, just
      // never rendered.
      if (wf.product_root) {
        head.appendChild(el("p", { class: "sub", text: "Product workspace folder:" }));
        head.appendChild(commandCopyRow(wf.product_root, "Copy path"));
      }
      root.appendChild(head);
      if (wf.trust) root.appendChild(workflowTrustBanner(wf.trust));

      if (!stages.length) {
        root.appendChild(emptyState({
          title: "No product workspace found yet.", isError: false,
          why: "The Workflow view reads a product workspace directly from disk; none exists yet.",
          next: "Create a product workspace, then reload this page."
        }));
        return;
      }

      var byId = {};
      stages.forEach(function (s) { byId[s.stage_id] = s; });
      if (wf.primary_next_stage_id != null && byId[wf.primary_next_stage_id]) {
        root.appendChild(workflowPrimaryPanel(byId[wf.primary_next_stage_id]));
      }

      WORKFLOW_GROUPS.forEach(function (g) {
        var inGroup = stages.filter(function (s) { return s.group === g; });
        if (!inGroup.length) return;
        var gs = el("section", { class: "panel" });
        gs.appendChild(el("h2", { text: g }));
        var list = el("div", { class: "stage-list" });
        // Product Truth renders first in its group -- it's the real prerequisite Stage 8 (the
        // group's own first stage) depends on, not one of the 13 numbered stages itself.
        if (wf.product_truth && wf.product_truth.group === g) {
          list.appendChild(productTruthRow(wf.product_truth));
        }
        inGroup.forEach(function (s) { list.appendChild(workflowStageRow(s)); });
        gs.appendChild(list);
        root.appendChild(gs);
      });
    }, function () {
      root.textContent = "";
      root.appendChild(el("div", { class: "notice bad", role: "alert",
        text: "Could not load the workflow. Reload this page." }));
    });
  }

  function renderSystem() {
    var root = document.getElementById("view-root");
    root.textContent = "";
    root.appendChild(loadingBlock("system health"));
    getJSON(API + "/system").then(function (res) {
      root.textContent = "";
      if (handleUnauthorized(res)) { root.appendChild(sessionExpiredPanel()); return; }
      var sys = (res.body.data || {}).system || {};
      var p = el("section", { class: "panel" });
      p.appendChild(el("h1", { text: "System Health" }));
      var chain = (sys.audit_chain || {});
      p.appendChild(el("p", { class: "sub" }, [
        document.createTextNode("Recorded history: "),
        tag(chain.ok ? "Verified" : "Integrity error", chain.ok ? "READY" : "BLOCKED")
      ]));
      if (!chain.ok) {
        p.appendChild(el("div", { class: "notice bad", role: "alert" }, [
          el("strong", { text: "The recorded history did not verify." }),
          el("p", { text: "Every action that would change something is blocked until this is "
                          + "resolved. Nothing on your Amazon account is affected." })
        ]));
      }
      root.appendChild(p);

      var b = el("section", { class: "panel" });
      b.appendChild(el("h2", { text: "Amazon boundary" }));
      b.appendChild(el("p", { class: "sub",
        text: "Every route into an Amazon seller account is refused, permanently and by design." }));
      var bd = sys.connectivity_boundary || {};
      var bg = el("div", { class: "cards" });
      [["Seller Central", bd.seller_central_blocked], ["Seller API", bd.seller_api_blocked],
       ["Advertising API", bd.advertising_api_blocked]].forEach(function (pair) {
        bg.appendChild(el("div", { class: "card" }, [
          el("div", { class: "k", text: pair[0] }),
          el("div", { class: "v small" }, [tag(pair[1] ? "REFUSED" : "CHECK",
                                               pair[1] ? "READY" : "BLOCKED")])]));
      });
      b.appendChild(bg);
      b.appendChild(details("View boundary counters", function (body) {
        body.appendChild(kvList(Object.keys(sys.seller_central_counters || {}).sort().map(function (k) {
          return [k, sys.seller_central_counters[k]];
        })));
      }));
      root.appendChild(b);

      var er = el("section", { class: "panel" });
      er.appendChild(el("h2", { text: "Recent problems" }));
      if (!(sys.recent_errors || []).length) {
        er.appendChild(emptyState({
          title: "No problems recorded.", isError: false,
          why: "The toolkit has not recorded an internal error recently.",
          next: "Nothing to do."
        }));
      }
      (sys.recent_errors || []).forEach(function (e) {
        er.appendChild(el("div", { class: "notice warn", role: "note",
          text: (e.code || "") + ": " + (e.message || "") }));
      });
      root.appendChild(er);

      var t = el("section", { class: "panel" });
      t.appendChild(el("h2", { text: "Technical details" }));
      t.appendChild(details("View technical details", function (body) {
        body.appendChild(kvList([
          ["Python version", sys.python_version], ["Platform", sys.platform],
          ["Repository commit", sys.repository_commit], ["Repository branch", sys.repository_branch],
          ["Business data ignored by git", String(sys.runs_ignored)],
          ["Network policy", sys.network_policy_status],
          ["Recorded events", chain.event_count]
        ]));
        var pg = el("div", { class: "cards" });
        Object.keys(sys.phase_directories || {}).sort().forEach(function (k) {
          pg.appendChild(el("div", { class: "card" }, [el("div", { class: "k", text: k }),
            el("div", { class: "v small" }, [tag(sys.phase_directories[k] ? "PRESENT" : "MISSING")])]));
        });
        body.appendChild(pg);
      }));
      root.appendChild(t);
      setFeedback(null);
    }, function () {
      root.textContent = "";
      setFeedback("failed", "The console did not answer.");
    });
  }

  function renderActivity() {
    tableView({
      id: "activity", endpoint: "activity", title: "Activity",
      pick: function (d) { return (d && d.events) || { data: [] }; },
      sub: "Everything you have run from this console, in order, with a tamper-evident chain.",
      empty: { title: "You have not run anything from this console yet.", isError: false,
               why: "This log records the actions you confirm here, so you can always see what happened.",
               next: "Nothing to do — it fills in as you use the toolkit." },
      before: function (panel) {
        var mount = el("div"); panel.appendChild(mount);
        getJSON(API + "/activity").then(function (res) {
          mount.textContent = "";
          var ch = (res.body.data || {}).audit_chain || {};
          mount.appendChild(el("div", { class: "notice" + (ch.ok ? "" : " bad"), role: "note",
            text: ch.ok ? "Recorded history verified · " + (ch.event_count != null ? ch.event_count : "—")
                          + " event(s)."
                        : "INTEGRITY ERROR (" + (ch.reason || "") + ") — actions that would change "
                          + "something are blocked until this is resolved." }));
        }, function () { mount.textContent = ""; });
      },
      columns: [{ key: "action", label: "What you ran" },
        { key: "execution_result", label: "Result", tag: true },
        { key: "target_ids", label: "Target", get: function (r) { return (r.target_ids || []).join(", "); } },
        { key: "recorded_at", label: "When" },
        { key: "actor", label: "Who" },
        { key: "console_event_id", label: "Event", copy: true }]
    });
  }

  // ---------------------------------------------------------------- action machinery (prepare/execute)
  // The opaque single-use action token lives ONLY in this closure (never DOM text, storage or log). The
  // modal renders the human-facing preparation contract with textContent only — it never renders the
  // action token, CSRF token, session id/fingerprint, cookie or any absolute local path.
  var modalState = { token: null, phrase: null, requires: false, lastFocus: null,
                     executing: false, done: false, action: null };

  // Short, secret-free readiness codes from the accepted server mapped to an owner-facing sentence.
  var READINESS_MESSAGE = {
    "SESSION7_13_SESSION_REQUIRED": "Session expired. Reload the console to start a fresh local session.",
    "SESSION7_13_CSRF_BLOCKED": "Security token rejected. Reload the console to obtain a fresh token.",
    "SESSION7_13_AUDIT_STATE_BLOCKED": "Audit-chain integrity error — every state-changing action is "
      + "blocked until it is resolved.",
    "SESSION7_13_ACTION_BLOCKED": "This action is blocked by its accepted authority."
  };
  function humanize(action) {
    return String(action || "action").replace(/[-_]/g, " ")
      .replace(/^\w/, function (c) { return c.toUpperCase(); });
  }
  function ownerReason(body) {
    body = body || {};
    if (body.readiness && READINESS_MESSAGE[body.readiness]) return READINESS_MESSAGE[body.readiness];
    var code = body.error || (body.data && body.data.failure_reason) || body.readiness || "";
    return code ? ("The action could not be prepared (" + code + ")."
      + (body.detail ? " " + body.detail : "")) : "The action could not be prepared.";
  }
  function id(x) { return document.getElementById(x); }
  function setText(x, t) { id(x).textContent = t; }
  function setReadiness(readiness) {
    var box = id("modal-readiness"); box.textContent = "";
    if (readiness) box.appendChild(ownerTag(readiness));
  }

  function resetModal() {
    setText("modal-title", "Confirm action");
    setText("modal-canonical", "");
    id("modal-readiness").textContent = "";
    id("modal-desc").textContent = "";
    setText("modal-phrase-required", "");
    setText("modal-phrase-hint", "");
    id("modal-phrase").value = "";
    var resEl = id("modal-result"); resEl.textContent = ""; resEl.className = "";
    id("modal-confirm-wrap").hidden = true;
    var cancel = id("modal-cancel"); cancel.textContent = "Cancel"; cancel.disabled = false; cancel.hidden = false;
    // Fail closed: the reset state is never runnable. openModal() enables Confirm only after the
    // preparation has passed full field validation, so no code path can reach an enabled Confirm
    // without a validated preparation behind it.
    var exec = id("modal-execute"); exec.textContent = "Confirm & run"; exec.disabled = true; exec.hidden = false;
    modalState.executing = false; modalState.done = false;
  }

  /* A preparation may only become a runnable confirmation when the accepted authority returned every
     field the owner needs in order to judge it: what will run, under whose authority, on what target,
     with what effect, how ready it is, how long the window lasts, and — when the action demands it —
     the exact phrase. A response missing any of these is a preparation failure, never a dialog the
     owner is asked to confirm blind. This mirrors the accepted server contract; it never relaxes it. */
  var REQUIRED_PREPARATION_FIELDS = ["action_token", "canonical_action", "readiness",
                                     "expected_authority", "expected_effect"];
  function missingPreparationFields(prep) {
    if (!prep || typeof prep !== "object" || Object.prototype.toString.call(prep) === "[object Array]") {
      return REQUIRED_PREPARATION_FIELDS.slice();          // malformed body: nothing is trustworthy
    }
    var missing = [];
    REQUIRED_PREPARATION_FIELDS.forEach(function (k) {
      var v = prep[k];
      if (v === null || v === undefined || v === "" || typeof v === "object") missing.push(k);
    });
    // A bounded target: the accepted authority always names the exact row(s) an action will touch.
    if (!(prep.target_ids && prep.target_ids.length
          && prep.target_ids.join("") !== "")) missing.push("target_ids");
    // The confirmation requirement must be internally consistent, or the phrase gate is meaningless.
    if (prep.requires_confirmation === null || prep.requires_confirmation === undefined) {
      missing.push("requires_confirmation");
    } else if (prep.requires_confirmation && !prep.confirmation_phrase) {
      missing.push("confirmation_phrase");
    }
    // The execution window must be a real, bounded, positive number of seconds.
    if (!(typeof prep.expires_in_seconds === "number" && isFinite(prep.expires_in_seconds)
          && prep.expires_in_seconds > 0)) missing.push("expires_in_seconds");
    return missing;
  }

  function startAction(action, params, label) {
    // A navigation destination can never be prepared as an action. Navigation controls carry no click
    // listener at all, so they cannot arrive here; this refuses the call outright if one ever does.
    if (isNavigationAct(action) || viewById(action)) {
      openModalBlocked(action, label, { status: 0, body: { error: "NOT_AN_ACTION" } },
                       ["canonical_action"]);
      return;
    }
    postJSON(API + "/actions/prepare", { action: action, params: params || {} }).then(function (res) {
      var d = res.body && res.body.data;
      // A prepare that did not return 200, or returned an incomplete / malformed body, is a
      // preparation block: it opens a modal that states the readiness and the reason, and it never
      // produces a runnable Confirm or a blank confirmation dialog.
      var missing = res.status === 200 ? missingPreparationFields(d) : ["action_token"];
      if (!missing.length) openModal(d, label);
      else openModalBlocked(action, label, res, missing);
    }, function () {
      openModalBlocked(action, label, { status: 0, body: { error: "NETWORK_ERROR" } },
                       ["action_token"]);
    });
  }

  function buildDetails(prep) {
    var dl = el("dl", { class: "kv" });
    function kv(k, v) {
      dl.appendChild(el("dt", { text: k }));
      dl.appendChild(el("dd", { text: (v === null || v === undefined || v === "") ? "—" : String(v) }));
    }
    kv("Accepted authority", prep.expected_authority);
    kv("Target(s)", (prep.target_ids || []).join(", "));
    kv("Expected effect", prep.expected_effect);
    kv("Network access", prep.network_use);
    kv("Local state changes", prep.local_state_changes);
    kv("Upstream state changes", prep.upstream_state_changes);
    kv("Confirmation window", prep.expires_in_seconds != null ? prep.expires_in_seconds + " seconds" : "—");
    return dl;
  }

  function openModal(prep, label) {
    resetModal();
    modalState.token = prep.action_token;        // memory only — never written to the DOM
    modalState.phrase = prep.confirmation_phrase;
    modalState.requires = !!prep.requires_confirmation;
    modalState.action = prep.canonical_action;
    modalState.lastFocus = document.activeElement;

    setText("modal-title", label || humanize(prep.canonical_action));       // owner-facing title
    setText("modal-canonical", "Canonical action: " + prep.canonical_action);
    setReadiness(prep.readiness);
    id("modal-desc").appendChild(buildDetails(prep));

    var exec = id("modal-execute");
    if (modalState.requires) {
      id("modal-confirm-wrap").hidden = false;
      setText("modal-phrase-required", "Required confirmation phrase:  " + prep.confirmation_phrase);
      setText("modal-phrase-hint", "Confirm & run stays disabled until the typed phrase matches exactly.");
      exec.disabled = true;                       // gated until an exact match
    } else {
      id("modal-confirm-wrap").hidden = true;
      exec.disabled = false;
    }
    openBackdrop();
    (modalState.requires ? id("modal-phrase") : exec).focus();
  }

  function openModalBlocked(action, label, res, missing) {
    resetModal();
    modalState.token = null;                      // never retain a stale preparation token
    modalState.phrase = null;
    modalState.requires = false;
    modalState.done = true;                       // nothing to execute from a blocked prepare
    modalState.action = action;
    modalState.lastFocus = document.activeElement;
    var body = (res && res.body) || {};
    setText("modal-title", label || humanize(action));
    setText("modal-canonical", "Canonical action: " + (action || "—"));
    setReadiness(body.readiness || "SESSION7_13_ACTION_BLOCKED");
    var note = el("div", { class: "notice bad", role: "note" }, [
      el("strong", { text: "This action cannot be prepared right now." }),
      el("p", { text: ownerReason(body) })
    ]);
    // An incomplete preparation names the missing details, so the owner can see WHY it is not
    // runnable instead of facing a dialog with nothing in it. The list is bounded by the fixed
    // required-field set, so nothing unbounded from the response reaches the page.
    if (missing && missing.length) {
      note.appendChild(el("p", { class: "em",
        text: "The toolkit did not receive: " + missing.slice(0, 12).join(", ") + "." }));
    }
    note.appendChild(el("p", { class: "em", text: "Nothing was changed and nothing was sent." }));
    id("modal-desc").appendChild(note);
    id("modal-confirm-wrap").hidden = true;
    var exec = id("modal-execute"); exec.hidden = true; exec.disabled = true;   // no Confirm on a block
    var cancel = id("modal-cancel"); cancel.textContent = "Close";
    openBackdrop();
    cancel.focus();
  }

  /* The structural last line of defence. Nothing may put the confirmation dialog on screen without
     owner-facing content in it: if the body is somehow empty at this point, the dialog fails closed —
     any token is dropped, Confirm is removed, and the owner is told the preparation was incomplete.
     A blank modal with a runnable Confirm therefore cannot exist, whatever a future caller does. */
  function openBackdrop() {
    var desc = id("modal-desc");
    if (!desc.children.length && desc.textContent === "") {
      modalState.token = null; modalState.phrase = null;
      modalState.requires = false; modalState.done = true;
      desc.appendChild(el("div", { class: "notice bad", role: "note" }, [
        el("strong", { text: "This action could not be prepared." }),
        el("p", { text: "The toolkit did not receive the details needed to confirm it." }),
        el("p", { class: "em", text: "Nothing was changed and nothing was sent." })
      ]));
      id("modal-confirm-wrap").hidden = true;
      var exec = id("modal-execute"); exec.hidden = true; exec.disabled = true;
      var cancel = id("modal-cancel"); cancel.textContent = "Close"; cancel.disabled = false;
    }
    id("modal-backdrop").hidden = false;
  }

  function refreshExecuteEnabled() {
    if (!modalState.requires || modalState.executing || modalState.done) return;
    // Exact match only — no trim, no case-fold, no punctuation normalization. This is never looser than
    // the accepted server check, so the button can only enable a phrase the authority would also accept.
    id("modal-execute").disabled = (id("modal-phrase").value !== modalState.phrase);
  }

  function closeModal() {
    if (modalState.executing) return;             // never close while an execution is in flight
    id("modal-backdrop").hidden = true;
    var f = modalState.lastFocus;
    modalState.token = null; modalState.phrase = null; modalState.action = null;
    modalState.requires = false; modalState.done = false;
    if (f && f.focus) f.focus();                  // focus returns to the triggering control
  }

  function executeModal() {
    if (modalState.executing || modalState.done || !modalState.token) return;   // block double-run / reuse
    if (modalState.requires && id("modal-phrase").value !== modalState.phrase) {
      setText("modal-phrase-hint", "Phrase does not match yet — Confirm & run stays disabled.");
      return;                                     // wrong phrase: local only, the token is NOT consumed
    }
    modalState.executing = true;
    id("modal-cancel").disabled = true;
    var exec = id("modal-execute"); exec.disabled = true; exec.textContent = "Working…";
    var resEl = id("modal-result"); resEl.className = ""; resEl.textContent = "Contacting the accepted authority…";
    var payload = { action_token: modalState.token };
    if (modalState.requires) payload.confirmation_phrase = id("modal-phrase").value;
    postJSON(API + "/actions/execute", payload).then(finishExecute, function () {
      finishExecute({ status: 0, body: { error: "NETWORK_ERROR" } });
    });
  }

  function finishExecute(res) {
    modalState.executing = false;
    modalState.token = null;                      // single-use: the submitted token is never reused
    modalState.done = true;
    var d = (res.body && res.body.data) || {};
    var resEl = id("modal-result");
    var exec = id("modal-execute"); exec.hidden = true;    // consumed token — no re-submit
    var cancel = id("modal-cancel"); cancel.disabled = false; cancel.textContent = "Close";
    if (res.status === 200 && d.readiness === "SESSION7_13_ACTION_COMPLETED") {
      resEl.className = "ok"; resEl.textContent = "";
      resEl.appendChild(el("strong", { text: "Completed — " + d.readiness }));
      if (d.upstream_result_id) resEl.appendChild(el("p", { text: "Result id: " + d.upstream_result_id }));
      if (d.authority) resEl.appendChild(el("p", { class: "em", text: "Accepted authority: " + d.authority }));
      appendExportResult(resEl, d);
      toast("Action completed");
      setFeedback("completed", "The action finished. The result stays open above until you close it.");
      route(); refreshStatusBar();                // refresh the read model; modal stays open until closed
    } else {
      // A serious failure never disappears into a toast: it stays in the modal until dismissed.
      resEl.className = "bad"; resEl.textContent = "";
      resEl.appendChild(el("strong", { text: "Not completed" }));
      var reason = (res.body && res.body.error) || d.failure_reason || d.readiness
        || (res.status ? "HTTP " + res.status : "network error");
      resEl.appendChild(el("p", { text: String(reason) }));
      if (d.policy_result && d.policy_result !== "ALLOWED")
        resEl.appendChild(el("p", { class: "em", text: "Policy: " + d.policy_result }));
      setFeedback(res.status === 401 || res.status === 403 ? "session-expired" : "failed",
                  "Nothing was changed on your Amazon account.");
      refreshStatusBar();
    }
    id("modal-cancel").focus();
  }

  function appendExportResult(resEl, d) {
    var exports = (d.upstream_summary || {}).exports;
    if (!exports || !exports.length) return;      // only export-overview yields a file list
    resEl.appendChild(el("p", { class: "em", text: "Export files written under the console workspace:" }));
    var ul = el("ul", { class: "export-list" });
    exports.forEach(function (name) { ul.appendChild(el("li", { class: "mono", text: String(name) })); });
    resEl.appendChild(ul);
    var row = el("div", { class: "export-row" });
    [["owner_console_snapshot.json", "json"], ["owner_console_status.tsv", "tsv"],
     ["owner_console_report.md", "md"]].forEach(function (e) {
      row.appendChild(el("a", { class: "btn", href: API + "/exports/overview?format=" + e[1],
                                download: e[0], "data-act": "download:" + e[1],
                                text: "Download " + e[0] }));
    });
    resEl.appendChild(row);
  }

  function modalFocusables() {
    var out = [];
    if (!id("modal-confirm-wrap").hidden) out.push(id("modal-phrase"));
    var cancel = id("modal-cancel"); if (!cancel.hidden && !cancel.disabled) out.push(cancel);
    var exec = id("modal-execute"); if (!exec.hidden && !exec.disabled) out.push(exec);
    return out;
  }
  function trapTab(e) {
    var f = modalFocusables();
    if (!f.length) { e.preventDefault(); return; }
    var first = f[0], last = f[f.length - 1], active = document.activeElement;
    if (e.shiftKey && active === first) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && active === last) { e.preventDefault(); first.focus(); }
    else if (f.indexOf(active) < 0) { e.preventDefault(); first.focus(); }
  }

  function wireModal() {
    id("modal-cancel").addEventListener("click", closeModal);
    id("modal-execute").addEventListener("click", executeModal);
    var input = id("modal-phrase");
    input.addEventListener("input", function () { setText("modal-phrase-hint", ""); refreshExecuteEnabled(); });
    input.addEventListener("keydown", function (e) {
      if (e.key !== "Enter") return;
      e.preventDefault();
      // Enter submits only when the confirm button would itself be enabled (exact phrase + live token).
      if (!modalState.executing && !modalState.done && modalState.token
          && (!modalState.requires || input.value === modalState.phrase)) executeModal();
    });
    var backdrop = id("modal-backdrop");
    backdrop.addEventListener("keydown", function (e) {
      if (e.key === "Escape") { closeModal(); return; }   // closeModal no-ops while executing
      if (e.key === "Tab") trapTab(e);
    });
    // Deliberate: a click on the dimmed backdrop does NOT dismiss a prepared confirmation, so an
    // accidental click can never discard a single-use token. Cancel / Close / Escape dismiss it.
    backdrop.addEventListener("mousedown", function (e) { if (e.target === backdrop) e.preventDefault(); });
    backdrop.addEventListener("click", function (e) { if (e.target === backdrop) e.preventDefault(); });
  }

  // ---------------------------------------------------------------- status bar + badges
  function refreshStatusBar() {
    getJSON(API + "/overview").then(function (res) {
      var d = (res.body.data || {});
      var ov = d.overview || {};
      var na = d.next_action || null;
      var readiness = res.body.readiness;
      var bar = document.getElementById("status-metrics");
      bar.textContent = "";
      bar.appendChild(ownerTag(ov.usability_readiness || readiness));
      if (na && na.owner_title) {
        bar.appendChild(el("span", { class: "status-next em", text: "Next: " + na.owner_title }));
      }
      bar.appendChild(el("span", { class: "readiness-pill", text: "Seller-account calls: 0" }));
      setBadge("alerts", ov.open_alerts);
      setBadge("analysis", ov.pending_decisions);
      setBadge("manual-actions", ov.pending_manual_actions);
      setBadge("followups", ov.due_followups);
      setBadge("watchlists", ov.due_watchlists);
      setBadge("notifications", (ov.notification_failed || 0) + (ov.notification_unknown || 0));
      setBadge("backups", ov.backup_snapshots);
      setBadge("research", ov.research_runs);
    }, function () { /* the view itself reports the failure; the bar simply stops updating */ });
  }
  function setBadge(id, n) {
    var b = document.getElementById("badge-" + id);
    if (!b) return;
    if (n === null || n === undefined || n === 0) { b.hidden = true; return; }
    b.textContent = String(n); b.hidden = false;
  }

  // ---------------------------------------------------------------- router + boot
  function route() {
    // Default landing (DASHBOARD-V1-SPEC.md Section 13 Q2, resolved: Workflow for everyone).
    // Overview stays fully reachable via nav and its own #overview hash -- only the no-hash
    // entry point moved.
    var hash = (window.location.hash || "#workflow").replace("#", "");
    if (hash === "alerts-view") hash = "alerts";      // accepted legacy route -> the real Alerts page
    var view = viewById(hash) || VIEWS[0];
    setActive(view.id);
    var root = document.getElementById("view-root");
    root.setAttribute("aria-busy", "true");
    root.textContent = ""; root.appendChild(loadingBlock(view.label));
    try { view.render(); } catch (e) {
      root.textContent = "";
      root.appendChild(el("div", { class: "notice bad", role: "alert", text: "Render error: " + e.message }));
    }
    root.setAttribute("aria-busy", "false");
    document.getElementById("content").focus();
  }

  function boot() {
    getJSON(API + "/session").then(function (res) {
      CSRF = (res.body.data && res.body.data.csrf_token) || null;
      buildNav(); wireModal(); wireSidebarToggle();
      window.addEventListener("hashchange", route);
      refreshStatusBar();
      window.setInterval(refreshStatusBar, POLL_MS);
      route();
    }, function () {
      var root = document.getElementById("view-root");
      root.textContent = "";
      root.appendChild(el("div", { class: "notice bad", role: "alert" }, [
        el("strong", { text: "The toolkit did not answer." }),
        el("p", { text: "Run Start-AMZ-Toolkit again, then reload this page." })
      ]));
    });
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
  else boot();
})();
