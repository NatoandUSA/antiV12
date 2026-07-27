/* Phase 7.13 Unified Owner Console — vanilla JS, local, no external scripts / fonts / CDN / telemetry,
   no WebSocket, no service worker, no analytics. Every value is rendered with textContent so upstream
   data can never inject markup. The CSRF token is held only in a closure variable (never in browser
   storage, never written to the DOM). Confirmation phrases come from the server, never invented. */
"use strict";

(function () {
  var API = "/api/v1";
  var POLL_MS = 15000;           // >= 10s per the local-status polling minimum
  var CSRF = null;               // in-memory only; never persisted, never placed in the DOM

  var VIEWS = [
    { id: "overview", label: "Overview", render: renderOverview },
    { id: "analysis", label: "Analysis & Decisions", render: renderAnalysis },
    { id: "research", label: "Research", render: renderResearch },
    { id: "watchlists", label: "Watchlists & Alerts", render: renderWatchlists },
    { id: "notifications", label: "Notifications", render: renderNotifications },
    { id: "backups", label: "Backup & Recovery", render: renderBackups },
    { id: "system", label: "System Health", render: renderSystem },
    { id: "activity", label: "Activity", render: renderActivity }
  ];
  var state = {};

  // ---------------------------------------------------------------- dom helpers
  function el(tag, opts, kids) {
    var e = document.createElement(tag);
    opts = opts || {};
    Object.keys(opts).forEach(function (k) {
      if (k === "class") { e.className = opts[k]; }
      else if (k === "text") { e.textContent = opts[k]; }
      else if (k.indexOf("aria") === 0 || k === "role" || k === "scope" || k === "type"
               || k === "href" || k === "tabindex" || k === "hidden" || k === "download"
               || k === "for" || k === "id" || k === "autocomplete" || k === "spellcheck") {
        e.setAttribute(k, opts[k]);
      } else { e[k] = opts[k]; }
    });
    (kids || []).forEach(function (c) { if (c != null) e.appendChild(c); });
    return e;
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
  function copyBtn(value) {
    var b = el("button", { class: "btn copy", type: "button", text: "Copy",
                           "aria-label": "Copy identifier " + value });
    b.addEventListener("click", function () {
      var done = function () { toast("Copied: " + value); };
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(value).then(done, function () { fallbackCopy(value, done); });
      } else { fallbackCopy(value, done); }
    });
    return b;
  }
  function fallbackCopy(value, done) {
    var ta = el("textarea"); ta.value = value; document.body.appendChild(ta); ta.select();
    try { document.execCommand("copy"); done(); } catch (e) { toast("Copy unavailable"); }
    document.body.removeChild(ta);
  }
  function idCell(value) {
    if (!value) return el("td", {}, [dash(value)]);
    return el("td", { class: "mono" }, [document.createTextNode(value), document.createTextNode(" "),
                                        copyBtn(value)]);
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

  // ---------------------------------------------------------------- status classes
  function readinessClass(r) {
    if (!r) return "";
    if (r.indexOf("READY") >= 0 && r.indexOf("BLOCKED") < 0) return "ok";
    if (r.indexOf("REQUIRED") >= 0 || r.indexOf("PARTIAL") >= 0 || r.indexOf("STALE") >= 0) return "warn";
    return "bad";
  }
  function statusClass(s) {
    if (!s) return "";
    var str = String(s);
    if (str.indexOf("BLOCKED") >= 0 || str === "FAILED" || str.indexOf("ERROR") >= 0) return "bad";
    if (str.indexOf("READY") >= 0 || str === "SENT" || str === "OK" || str === "ACKNOWLEDGED"
        || str === "PRESENT" || str === "APPROVED") return "ok";
    if (str.indexOf("UNAVAILABLE") >= 0 || str.indexOf("PARTIAL") >= 0 || str === "UNKNOWN"
        || str === "OPEN" || str === "PENDING" || str === "NOT_DUE" || str === "DISMISSED"
        || str === "QUEUED" || str === "RATE_LIMITED" || str === "QUIET_HOURS") return "warn";
    return "";
  }
  function tag(value, cls) {
    // status is never color-only: a text label + a shape dot accompany the colour.
    return el("span", { class: "status-tag" + (cls ? " " + cls : "") }, [
      el("span", { class: "dot", "aria-hidden": "true" }),
      document.createTextNode(value == null ? "—" : String(value))
    ]);
  }
  function card(k, v, small) {
    return el("div", { class: "card" }, [
      el("div", { class: "k", text: k }),
      el("div", { class: "v" + (small ? " small" : ""),
                  text: (v === null || v === undefined || v === "") ? "—" : String(v) })
    ]);
  }

  // ---------------------------------------------------------------- navigation
  function buildNav() {
    var ul = document.getElementById("nav-list");
    ul.textContent = "";
    VIEWS.forEach(function (v) {
      var badge = el("span", { class: "count-badge", id: "badge-" + v.id, text: "" });
      badge.hidden = true;
      var a = el("a", { href: "#" + v.id, id: "nav-" + v.id },
                 [el("span", { text: v.label }), badge]);
      ul.appendChild(el("li", {}, [a]));
    });
  }
  function setActive(id) {
    VIEWS.forEach(function (v) {
      var a = document.getElementById("nav-" + v.id);
      if (a) { if (v.id === id) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current"); }
    });
    var bc = document.getElementById("breadcrumbs");
    var view = VIEWS.filter(function (v) { return v.id === id; })[0];
    bc.textContent = "";
    bc.appendChild(el("ol", {}, [
      el("li", { text: "Console" }),
      el("li", { text: view ? view.label : id })
    ]));
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
  function renderPaged(mount, paged, columns, st, refresh) {
    var data = (paged && paged.data) || [];
    var wrap = el("div", { class: "table-wrap" });
    var table = el("table");
    table.appendChild(el("caption", { text: (paged.total != null ? paged.total : data.length)
                                      + " row(s), read-only" }));
    var thead = el("thead"); var htr = el("tr");
    htr.appendChild(el("th", { scope: "col", text: "Copy ID" }));
    columns.forEach(function (c) {
      var th = el("th", { scope: "col" });
      if (c.sortable) {
        var arrow = (st.sort === c.key) ? (st.direction === "asc" ? "▲" : "▼") : "↕";
        var btn = el("button", { class: "sort", type: "button", "aria-label": "Sort by " + c.label },
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
    thead.appendChild(htr); table.appendChild(thead);
    var tbody = el("tbody");
    if (!data.length) {
      var tr = el("tr");
      var td = el("td", { class: "empty" }, [document.createTextNode("No records to display.")]);
      td.setAttribute("colspan", String(columns.length + 2));
      tr.appendChild(td); tbody.appendChild(tr);
    }
    data.forEach(function (row) {
      var tr = el("tr");
      tr.appendChild(idCell(row.row_id));
      columns.forEach(function (c) {
        if (c.action) return;
        var v = c.get ? c.get(row) : row[c.key];
        var td;
        if (c.tag) td = el("td", {}, [tag(v, statusClass(v))]);
        else if (c.copy && v) td = idCell(v);
        else td = el("td", { class: (c.num ? "num " : "") + (c.mono ? "mono" : "") }, [dash(v)]);
        tr.appendChild(td);
      });
      var actCol = columns.filter(function (c) { return c.action; })[0];
      if (actCol) {
        var td = el("td", {});
        (actCol.actions(row) || []).forEach(function (spec) {
          var b = el("button", { class: "btn action", type: "button", text: spec.label });
          b.addEventListener("click", function () { startAction(spec.action, spec.params, spec.label); });
          td.appendChild(b); td.appendChild(document.createTextNode(" "));
        });
        tr.appendChild(td);
      }
      tbody.appendChild(tr);
    });
    table.appendChild(tbody); wrap.appendChild(table); mount.appendChild(wrap);
    var pager = el("div", { class: "pager" });
    var info = el("span", { class: "info", text: "Page " + (paged.page || 1) + " of "
                            + (paged.total_pages || 1) + " · " + (paged.total != null ? paged.total : data.length) + " total" });
    var prev = el("button", { class: "btn", type: "button", text: "‹ Prev" });
    var next = el("button", { class: "btn", type: "button", text: "Next ›" });
    prev.disabled = (paged.page || 1) <= 1;
    next.disabled = (paged.page || 1) >= (paged.total_pages || 1);
    prev.addEventListener("click", function () { if (st.page > 1) { st.page--; refresh(); } });
    next.addEventListener("click", function () { st.page++; refresh(); });
    pager.appendChild(prev); pager.appendChild(next); pager.appendChild(info);
    mount.appendChild(pager);
  }

  function tableView(opts) {
    // opts: {id, endpoint, extra, columns, title, sub, notice, before}
    var st = ensureState(opts.id);
    var root = document.getElementById("view-root");
    var panel = el("section", { class: "panel" });
    panel.appendChild(el("h1", { text: opts.title }));
    if (opts.sub) panel.appendChild(el("p", { class: "sub", text: opts.sub }));
    if (opts.notice) panel.appendChild(el("div", { class: "notice", role: "note", text: opts.notice }));
    if (opts.before) opts.before(panel);
    var controls = el("div", { class: "controls" });
    var fInput = el("input", { type: "search", value: st.filter, "aria-label": "Search " + opts.title });
    fInput.placeholder = "Search…";
    fInput.addEventListener("change", function () { st.filter = fInput.value.slice(0, 200); st.page = 1; refresh(); });
    controls.appendChild(el("label", {}, [el("span", { text: "Search" }), fInput]));
    var sizeSel = el("select", { "aria-label": "Rows per page" });
    [25, 50, 100, 200].forEach(function (n) {
      var op = el("option", { value: String(n), text: n + " / page" });
      if (n === st.page_size) op.selected = true;
      sizeSel.appendChild(op);
    });
    sizeSel.addEventListener("change", function () { st.page_size = parseInt(sizeSel.value, 10); st.page = 1; refresh(); });
    controls.appendChild(el("label", {}, [el("span", { text: "Page size" }), sizeSel]));
    panel.appendChild(controls);
    var mount = el("div"); panel.appendChild(mount);
    root.textContent = ""; root.appendChild(panel);
    function refresh() {
      mount.textContent = "";
      mount.appendChild(el("p", { class: "loading", text: "Loading…" }));
      getJSON(API + "/" + opts.endpoint + "?" + buildQuery(st, opts.extra || {})).then(function (res) {
        mount.textContent = "";
        if (res.status !== 200) {
          mount.appendChild(el("div", { class: "notice bad", role: "alert",
            text: "Request rejected: " + (res.body.error || res.status)
              + (res.body.detail ? " (" + res.body.detail + ")" : "") }));
          return;
        }
        var paged = opts.pick(res.body.data);
        renderPaged(mount, paged, opts.columns, st, refresh);
      });
    }
    refresh();
  }

  // ---------------------------------------------------------------- views
  function renderOverview() {
    var root = document.getElementById("view-root");
    getJSON(API + "/overview").then(function (res) {
      var d = res.body.data || {}; var ov = d.overview || {};
      var readiness = res.body.readiness;
      root.textContent = "";
      var head = el("section", { class: "panel" });
      head.appendChild(el("h1", { text: "Overview" }));
      head.appendChild(el("p", { class: "sub" }, [
        document.createTextNode("Console readiness: "),
        el("span", { class: "readiness-pill " + readinessClass(readiness), text: readiness || "—" }),
        document.createTextNode("  "),
        el("span", { class: "status-tag " + statusClass(ov.owner_label), text: ov.owner_label || "" })
      ]));
      var qa = el("div", { class: "action-row" });
      qa.appendChild(actBtn("Refresh overview", "refresh-overview", {}));
      qa.appendChild(actBtn("Export snapshot", "export-overview", {}));
      qa.appendChild(actBtn("Verify system state", "verify-system-state", {}));
      head.appendChild(qa);
      (d.disclaimer || []).forEach(function (line, i) {
        if (i === 0) return;
        head.appendChild(el("div", { class: "notice", role: "note", text: line }));
      });
      root.appendChild(head);

      var counts = el("section", { class: "panel" });
      counts.appendChild(el("h2", { text: "Attention & counts" }));
      var g = el("div", { class: "cards" });
      g.appendChild(card("Open alerts", ov.open_alerts));
      g.appendChild(card("Pending decisions", ov.pending_decisions));
      g.appendChild(card("Pending manual actions", ov.pending_manual_actions));
      g.appendChild(card("Due follow-ups", ov.due_followups));
      g.appendChild(card("Due watchlists", ov.due_watchlists));
      g.appendChild(card("Notifications sent", ov.notification_sent));
      g.appendChild(card("Notifications failed", ov.notification_failed));
      g.appendChild(card("Notifications unknown", ov.notification_unknown));
      g.appendChild(card("Research runs", ov.research_runs));
      g.appendChild(card("Backup snapshots", ov.backup_snapshots));
      g.appendChild(card("Update available", String(ov.update_available)));
      g.appendChild(card("Attention items", ov.attention_items));
      counts.appendChild(g);
      root.appendChild(counts);

      var mods = el("section", { class: "panel" });
      mods.appendChild(el("h2", { text: "Module readiness" }));
      var mg = el("div", { class: "cards" });
      Object.keys(ov.module_status || {}).sort().forEach(function (m) {
        var s = ov.module_status[m];
        mg.appendChild(el("div", { class: "card" }, [
          el("div", { class: "k", text: m }),
          el("div", { class: "v small" }, [tag(s, statusClass(s))])
        ]));
      });
      mods.appendChild(mg);
      if ((ov.blocked_modules || []).length) {
        mods.appendChild(el("div", { class: "notice bad", role: "note",
          text: "Blocked modules: " + ov.blocked_modules.join(", ") + " — see System Health." }));
      }
      if ((ov.stale_data_phases || []).length) {
        mods.appendChild(el("div", { class: "notice warn", role: "note",
          text: "Stale data (older than 24h): " + ov.stale_data_phases.join(", ") }));
      }
      root.appendChild(mods);

      var exports = el("section", { class: "panel" });
      exports.appendChild(el("h2", { text: "Consolidated snapshot (local export)" }));
      var er = el("div", { class: "export-row" });
      [["owner_console_snapshot.json", "json"], ["owner_console_status.tsv", "tsv"],
       ["owner_console_report.md", "md"]].forEach(function (e) {
        er.appendChild(el("a", { class: "btn", href: API + "/exports/overview?format=" + e[1],
                                 download: e[0], text: "Download " + e[0] }));
      });
      exports.appendChild(er);
      root.appendChild(exports);
    });
  }

  function actBtn(label, action, params) {
    var b = el("button", { class: "btn action", type: "button", text: label });
    b.addEventListener("click", function () { startAction(action, params, label); });
    return b;
  }

  function renderAnalysis() {
    var st = ensureState("analysis-sub"); if (!st.sub) st.sub = "analysis";
    tableView({
      id: "analysis", endpoint: "analysis", extra: { view: st.sub },
      title: "Analysis & Decisions", pick: function (d) { return (d && d.rows) || { data: [] }; },
      sub: "Accepted Phase 7.3–7.7 business views via the Phase 7.8 authority (read-only, never recomputed here).",
      before: function (panel) {
        var row = el("div", { class: "controls" });
        var sel = el("select", { "aria-label": "View" });
        [["analysis", "Analysis"], ["reviews", "Owner review"], ["decisions", "Decision packages"],
         ["manual-actions", "Manual actions"], ["outcomes", "Outcome follow-up"],
         ["attention", "Attention"]].forEach(function (o) {
          var op = el("option", { value: o[0], text: o[1] }); if (o[0] === st.sub) op.selected = true;
          sel.appendChild(op);
        });
        sel.addEventListener("change", function () { st.sub = sel.value; renderAnalysis(); });
        row.appendChild(el("label", {}, [el("span", { text: "View" }), sel]));
        panel.appendChild(row);
      },
      columns: [{ key: "row_id", label: "Detail", get: function (r) {
        return r.customer_search_term || r.entity_id || r.tracker_record_id || r.followup_record_id
          || r.package_item_id || r.reference_id || ""; } },
        { key: "review_status", label: "Review", tag: true },
        { key: "owner_status", label: "Owner status", tag: true },
        { key: "outcome_classification", label: "Outcome", tag: true },
        { key: "category", label: "Category", tag: true },
        { key: "detail", label: "Detail" }]
    });
  }

  function renderResearch() {
    tableView({
      id: "research", endpoint: "research", title: "Public Research (Phase 7.10)",
      pick: function (d) { return (d && d.runs) || { data: [] }; },
      sub: "Accepted Phase 7.10 research runs — evidence and provenance only (read-only).",
      columns: [{ key: "run_id", label: "Run", copy: true, sortable: true },
        { key: "readiness", label: "Readiness", tag: true, sortable: true },
        { key: "source_count", label: "Sources", num: true },
        { key: "evidence_count", label: "Evidence", num: true }]
    });
  }

  function renderWatchlists() {
    tableView({
      id: "watchlists", endpoint: "watchlists", title: "Watchlists & Alerts (Phase 7.11)",
      pick: function (d) { return (d && d.watchlists) || { data: [] }; },
      sub: "Accepted Phase 7.11 watchlists and schedules. Alerts are on the Alerts view.",
      before: function (panel) {
        var a = el("div", { class: "action-row" });
        a.appendChild(el("a", { class: "btn", href: "#alerts-view", text: "View alerts →" }));
        panel.appendChild(a);
      },
      columns: [{ key: "watchlist_id", label: "Watchlist", copy: true, sortable: true },
        { key: "name", label: "Name", sortable: true },
        { key: "schedule_type", label: "Schedule", sortable: true },
        { key: "due", label: "Due", get: function (r) { return String(r.due); }, tag: true },
        { key: "due_reason", label: "Reason" }, { key: "next_due", label: "Next due" },
        { key: "actions", label: "Actions", action: true, actions: function (r) {
          return [{ label: "Run", action: "run-watchlist", params: { watchlist_id: r.watchlist_id } }]; } }]
    });
  }

  function renderAlerts() {
    tableView({
      id: "alerts", endpoint: "alerts", title: "Owner Alerts (Phase 7.11)",
      pick: function (d) { return (d && d.alerts) || { data: [] }; },
      sub: "Accepted Phase 7.11 owner alerts. State changes go through a confirmed console action.",
      notice: "Alert state changes are recorded by the accepted Phase 7.11 authority, never by the browser.",
      columns: [{ key: "alert_id", label: "Alert", copy: true, sortable: true },
        { key: "severity", label: "Severity", tag: true, sortable: true },
        { key: "status", label: "Status", tag: true, sortable: true },
        { key: "label", label: "Label" }, { key: "change_type", label: "Change" },
        { key: "actions", label: "Actions", action: true, actions: function (r) {
          return [
            { label: "Acknowledge", action: "acknowledge-alert", params: { alert_id: r.alert_id } },
            { label: "Dismiss", action: "dismiss-alert", params: { alert_id: r.alert_id } },
            { label: "Reopen", action: "reopen-alert", params: { alert_id: r.alert_id } }
          ]; } }]
    });
  }

  function renderNotifications() {
    tableView({
      id: "notifications", endpoint: "notifications", title: "Notifications (Phase 7.12)",
      pick: function (d) { return (d && d.batches) || { data: [] }; },
      sub: "Accepted Phase 7.12 batches. Live send preserves every accepted Phase 7.12 gate.",
      notice: "UNKNOWN delivery is never treated as FAILED. Live send needs the accepted Phase 7.12 environment gate.",
      before: function (panel) {
        getJSON(API + "/notifications").then(function (res) {
          var d = res.body.data || {};
          var box = el("div", {});
          box.appendChild(el("h2", { text: "Routes" }));
          var g = el("div", { class: "cards" });
          (d.routes || []).forEach(function (r) {
            g.appendChild(el("div", { class: "card" }, [
              el("div", { class: "k", text: r.name || r.route_id }),
              el("div", { class: "v small" }, [tag(r.approval_status, statusClass(r.approval_status))]),
              actBtn("Preview", "preview-notification", { route_id: r.route_id }),
              actBtn("Build batch", "build-notification-batch", { route_id: r.route_id })
            ]));
          });
          box.appendChild(g);
          panel.insertBefore(box, panel.children[panel.children.length - 1]);
        });
      },
      columns: [{ key: "batch_id", label: "Batch", copy: true },
        { key: "route_id", label: "Route" }, { key: "alert_count", label: "Alerts", num: true },
        { key: "readiness", label: "Readiness", tag: true },
        { key: "actions", label: "Actions", action: true, actions: function (r) {
          return [{ label: "Send", action: "send-notification-batch", params: { batch_id: r.batch_id } }]; } }]
    });
  }

  function renderBackups() {
    tableView({
      id: "backups", endpoint: "backups", title: "Backup & Recovery (Phase 7.9)",
      pick: function (d) { return (d && d.snapshots) || { data: [] }; },
      sub: "Accepted Phase 7.9 snapshots. A destructive restore is never run from this console.",
      before: function (panel) {
        var a = el("div", { class: "action-row" });
        a.appendChild(actBtn("Create snapshot", "create-backup-snapshot", {}));
        a.appendChild(actBtn("Check for update", "check-for-update", {}));
        panel.appendChild(a);
        getJSON(API + "/backups").then(function (res) {
          var uc = (res.body.data || {}).update_check;
          if (uc) {
            panel.appendChild(el("div", { class: "notice", role: "note",
              text: "Update check: " + (uc.readiness || "") + " · available: " + String(uc.update_available)
                + " · remote head: " + (uc.remote_branch_head || "—") }));
          }
        });
      },
      columns: [{ key: "snapshot_id", label: "Snapshot", copy: true, sortable: true },
        { key: "file_count", label: "Files", num: true, sortable: true },
        { key: "total_bytes", label: "Bytes", num: true, sortable: true },
        { key: "encrypted", label: "Encrypted", get: function (r) { return String(r.encrypted); } },
        { key: "actions", label: "Actions", action: true, actions: function (r) {
          return [
            { label: "Verify", action: "verify-backup", params: { snapshot_id: r.snapshot_id } },
            { label: "Recovery plan", action: "create-recovery-plan", params: { snapshot_id: r.snapshot_id } }
          ]; } }]
    });
  }

  function renderSystem() {
    var root = document.getElementById("view-root");
    getJSON(API + "/system").then(function (res) {
      var sys = (res.body.data || {}).system || {};
      root.textContent = "";
      var p = el("section", { class: "panel" });
      p.appendChild(el("h1", { text: "System Health" }));
      var dl = el("dl", { class: "kv" });
      function kv(k, v) { dl.appendChild(el("dt", { text: k })); dl.appendChild(el("dd", { text: v == null ? "—" : String(v) })); }
      kv("Python version", sys.python_version);
      kv("Platform", sys.platform);
      kv("Repository commit", sys.repository_commit);
      kv("Repository branch", sys.repository_branch);
      kv("runs/ ignored by git", String(sys.runs_ignored));
      kv("Network policy", sys.network_policy_status);
      kv("Audit chain OK", String((sys.audit_chain || {}).ok));
      kv("Audit events", (sys.audit_chain || {}).event_count);
      p.appendChild(dl);
      root.appendChild(p);

      var b = el("section", { class: "panel" });
      b.appendChild(el("h2", { text: "Amazon boundary (all seller-account access refused)" }));
      var bd = sys.connectivity_boundary || {};
      var bg = el("div", { class: "cards" });
      bg.appendChild(el("div", { class: "card" }, [el("div", { class: "k", text: "Seller Central" }),
        el("div", { class: "v small" }, [tag(bd.seller_central_blocked ? "REFUSED" : "?", "ok")])]));
      bg.appendChild(el("div", { class: "card" }, [el("div", { class: "k", text: "Seller API" }),
        el("div", { class: "v small" }, [tag(bd.seller_api_blocked ? "REFUSED" : "?", "ok")])]));
      bg.appendChild(el("div", { class: "card" }, [el("div", { class: "k", text: "Advertising API" }),
        el("div", { class: "v small" }, [tag(bd.advertising_api_blocked ? "REFUSED" : "?", "ok")])]));
      Object.keys(sys.seller_central_counters || {}).forEach(function (k) {
        bg.appendChild(card(k, sys.seller_central_counters[k]));
      });
      b.appendChild(bg);
      root.appendChild(b);

      var ph = el("section", { class: "panel" });
      ph.appendChild(el("h2", { text: "Phase directory availability" }));
      var pg = el("div", { class: "cards" });
      Object.keys(sys.phase_directories || {}).forEach(function (k) {
        pg.appendChild(el("div", { class: "card" }, [el("div", { class: "k", text: k }),
          el("div", { class: "v small" }, [tag(sys.phase_directories[k] ? "PRESENT" : "MISSING",
            sys.phase_directories[k] ? "ok" : "warn")])]));
      });
      ph.appendChild(pg);
      root.appendChild(ph);

      var er = el("section", { class: "panel" });
      er.appendChild(el("h2", { text: "Recent errors (redacted)" }));
      if (!(sys.recent_errors || []).length) er.appendChild(el("p", { class: "em", text: "No recent errors." }));
      (sys.recent_errors || []).forEach(function (e) {
        er.appendChild(el("div", { class: "notice", role: "note",
          text: (e.code || "") + ": " + (e.message || "") }));
      });
      root.appendChild(er);
    });
  }

  function renderActivity() {
    tableView({
      id: "activity", endpoint: "activity", title: "Activity — Orchestration Audit",
      pick: function (d) { return (d && d.events) || { data: [] }; },
      sub: "Append-only, hash-chained console orchestration audit trail.",
      before: function (panel) {
        getJSON(API + "/activity").then(function (res) {
          var ch = (res.body.data || {}).audit_chain || {};
          panel.appendChild(el("div", { class: "notice" + (ch.ok ? "" : " bad"), role: "note",
            text: "Audit chain: " + (ch.ok ? "OK" : "INTEGRITY ERROR (" + (ch.reason || "") + ") — "
              + "state-changing actions are blocked until resolved.") + " · events: "
              + (ch.event_count != null ? ch.event_count : "—") }));
        });
      },
      columns: [{ key: "console_event_id", label: "Event", copy: true },
        { key: "action", label: "Console action" }, { key: "authority", label: "Authority" },
        { key: "target_ids", label: "Target", get: function (r) { return (r.target_ids || []).join(", "); } },
        { key: "execution_result", label: "Result", tag: true },
        { key: "upstream_result_id", label: "Result ID", mono: true },
        { key: "policy_result", label: "Policy", tag: true },
        { key: "actor", label: "Actor" }, { key: "recorded_at", label: "When" }]
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
    if (readiness) box.appendChild(el("span", { class: "readiness-pill " + readinessClass(readiness),
                                                text: readiness }));
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
    var exec = id("modal-execute"); exec.textContent = "Confirm & run"; exec.disabled = false; exec.hidden = false;
    modalState.executing = false; modalState.done = false;
  }

  function startAction(action, params, label) {
    postJSON(API + "/actions/prepare", { action: action, params: params || {} }).then(function (res) {
      var d = res.body && res.body.data;
      // A successful prepare always carries an action_token; anything else is a preparation block and
      // must still open a modal that shows the readiness + reason (never a blank confirmation dialog).
      if (res.status === 200 && d && d.action_token) openModal(d, label);
      else openModalBlocked(action, label, res);
    }, function () {
      openModalBlocked(action, label, { status: 0, body: { error: "NETWORK_ERROR" } });
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

  function openModalBlocked(action, label, res) {
    resetModal();
    modalState.token = null;                      // never retain a stale preparation token
    modalState.requires = false;
    modalState.done = true;                       // nothing to execute from a blocked prepare
    modalState.action = action;
    modalState.lastFocus = document.activeElement;
    var body = (res && res.body) || {};
    setText("modal-title", label || humanize(action));
    setText("modal-canonical", "Canonical action: " + action);
    setReadiness(body.readiness || "SESSION7_13_ACTION_BLOCKED");
    id("modal-desc").appendChild(el("div", { class: "notice bad", role: "note" }, [
      el("strong", { text: "This action cannot be prepared right now." }),
      el("p", { text: ownerReason(body) })
    ]));
    id("modal-confirm-wrap").hidden = true;
    var exec = id("modal-execute"); exec.hidden = true; exec.disabled = true;   // no Confirm on a block
    var cancel = id("modal-cancel"); cancel.textContent = "Close";
    openBackdrop();
    cancel.focus();
  }

  function openBackdrop() { id("modal-backdrop").hidden = false; }

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
      route(); refreshStatusBar();                // refresh the read model; modal stays open until closed
    } else {
      resEl.className = "bad"; resEl.textContent = "";
      resEl.appendChild(el("strong", { text: "Not completed" }));
      var reason = (res.body && res.body.error) || d.failure_reason || d.readiness
        || (res.status ? "HTTP " + res.status : "network error");
      resEl.appendChild(el("p", { text: String(reason) }));
      if (d.policy_result && d.policy_result !== "ALLOWED")
        resEl.appendChild(el("p", { class: "em", text: "Policy: " + d.policy_result }));
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
                                download: e[0], text: "Download " + e[0] }));
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
      var ov = (res.body.data || {}).overview || {};
      var readiness = res.body.readiness;
      var bar = document.getElementById("status-metrics");
      bar.textContent = "";
      bar.appendChild(el("span", { class: "readiness-pill " + readinessClass(readiness), text: readiness || "—" }));
      bar.appendChild(el("span", { class: "readiness-pill", text: "Seller-account calls: 0" }));
      setBadge("watchlists", ov.open_alerts);
      setBadge("notifications", (ov.notification_failed || 0) + (ov.notification_unknown || 0));
      setBadge("backups", ov.backup_snapshots);
      setBadge("research", ov.research_runs);
    });
  }
  function setBadge(id, n) {
    var b = document.getElementById("badge-" + id);
    if (!b) return;
    if (n === null || n === undefined) { b.hidden = true; return; }
    b.textContent = String(n); b.hidden = false;
  }

  // ---------------------------------------------------------------- router + boot
  function route() {
    var hash = (window.location.hash || "#overview").replace("#", "");
    if (hash === "alerts-view") { renderAlertsRoute(); return; }
    var view = VIEWS.filter(function (v) { return v.id === hash; })[0] || VIEWS[0];
    setActive(view.id);
    var root = document.getElementById("view-root");
    root.setAttribute("aria-busy", "true");
    root.textContent = ""; root.appendChild(el("p", { class: "loading", text: "Loading…" }));
    try { view.render(); } catch (e) {
      root.textContent = "";
      root.appendChild(el("div", { class: "notice bad", role: "alert", text: "Render error: " + e.message }));
    }
    root.setAttribute("aria-busy", "false");
    document.getElementById("content").focus();
  }
  function renderAlertsRoute() {
    setActive("watchlists");
    document.getElementById("content").focus();
    renderAlerts();
  }

  function boot() {
    getJSON(API + "/session").then(function (res) {
      CSRF = (res.body.data && res.body.data.csrf_token) || null;
      buildNav(); wireModal();
      window.addEventListener("hashchange", route);
      refreshStatusBar();
      window.setInterval(refreshStatusBar, POLL_MS);
      route();
    });
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
  else boot();
})();
