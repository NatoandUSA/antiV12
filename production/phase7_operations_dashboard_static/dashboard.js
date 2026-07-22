/* Phase 7.8 Owner Operations Dashboard — vanilla JS, offline, read-only.
   No external scripts, no fonts, no CDN, no telemetry, no service worker, no WebSocket.
   Every value is rendered with textContent so upstream data can never inject markup. */
"use strict";

(function () {
  var API = {
    health: "/api/health",
    overview: "/api/overview",
    analysis: "/api/analysis",
    reviews: "/api/reviews",
    decisionPackages: "/api/decision-packages",
    manualActions: "/api/manual-actions",
    outcomes: "/api/outcomes",
    attention: "/api/attention",
    lineage: "/api/lineage"
  };

  var VIEWS = [
    { id: "overview", label: "Overview", render: renderOverview },
    { id: "analysis", label: "Analysis", render: renderAnalysis },
    { id: "reviews", label: "Owner Review", render: renderReviews },
    { id: "decision-packages", label: "Decision Packages", render: renderDecision },
    { id: "manual-actions", label: "Manual Actions", render: renderManual },
    { id: "outcomes", label: "Outcome Follow-up", render: renderOutcomes },
    { id: "lineage", label: "Lineage & Integrity", render: renderLineage },
    { id: "attention", label: "Attention Required", render: renderAttention }
  ];

  // per-view table state (page / sort / direction / filter). Display only — never persisted.
  var state = {};

  // ---------------------------------------------------------------- dom helpers
  function el(tag, opts, kids) {
    var e = document.createElement(tag);
    opts = opts || {};
    Object.keys(opts).forEach(function (k) {
      if (k === "class") { e.className = opts[k]; }
      else if (k === "text") { e.textContent = opts[k]; }
      else if (k === "html") { /* intentionally unsupported */ }
      else if (k.indexOf("aria") === 0 || k === "role" || k === "scope" || k === "type"
               || k === "href" || k === "tabindex" || k === "hidden" || k === "download") {
        e.setAttribute(k, opts[k]);
      } else { e[k] = opts[k]; }
    });
    (kids || []).forEach(function (c) { if (c != null) e.appendChild(c); });
    return e;
  }
  function dash(v) {
    if (v === null || v === undefined || v === "") {
      return el("span", { class: "em", text: "—" });
    }
    return document.createTextNode(String(v));
  }
  function toast(msg) {
    var t = document.getElementById("toast");
    t.textContent = msg;
    t.hidden = false;
    window.clearTimeout(t._timer);
    t._timer = window.setTimeout(function () { t.hidden = true; }, 1800);
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
    var ta = el("textarea"); ta.value = value; document.body.appendChild(ta);
    ta.select();
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
    return fetch(path, { headers: { "Accept": "application/json" }, credentials: "omit",
                         cache: "no-store" }).then(function (r) {
      return r.json().then(function (j) { return { status: r.status, body: j }; });
    });
  }

  // ---------------------------------------------------------------- readiness helpers
  function readinessClass(readiness) {
    if (!readiness) return "";
    if (readiness.indexOf("READY_EMPTY") >= 0 || readiness === "SESSION7_8_OPERATIONS_DASHBOARD_READY"
        || readiness.indexOf("READY_PARTIAL") >= 0) return "ok";
    if (readiness.indexOf("SELECTION_REQUIRED") >= 0 || readiness.indexOf("REQUIRED") >= 0) return "warn";
    return "bad";
  }
  function statusClass(s) {
    if (!s) return "";
    var ok = ["SELECTED", "OK", "CURRENT"];
    var warn = ["MISSING", "NOT_INITIALIZED", "PACKAGE_NOT_AVAILABLE", "FOLLOWUP_NOT_AVAILABLE",
                "PACKAGE_STALE", "FOLLOWUP_STALE", "PENDING_MANUAL_CHECK", "DEFERRED",
                "FOLLOWUP_SELECTION_REQUIRED"];
    if (ok.indexOf(s) >= 0) return "ok";
    if (warn.indexOf(s) >= 0) return "warn";
    if (s.indexOf("BLOCKED") >= 0 || s.indexOf("CONFLICT") >= 0) return "bad";
    return "";
  }
  function tag(value, cls) {
    var t = el("span", { class: "status-tag" + (cls ? " " + cls : ""), text: value == null ? "—" : String(value) });
    return t;
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
  }

  // ---------------------------------------------------------------- generic table
  function ensureState(id, defaults) {
    if (!state[id]) state[id] = Object.assign({ page: 1, page_size: 50, sort: null, direction: "asc", filter: "" }, defaults || {});
    return state[id];
  }
  function buildQuery(st, extra) {
    var q = [];
    q.push("page=" + encodeURIComponent(st.page));
    q.push("page_size=" + encodeURIComponent(st.page_size));
    if (st.sort) { q.push("sort=" + encodeURIComponent(st.sort)); q.push("direction=" + encodeURIComponent(st.direction)); }
    if (st.filter) q.push("filter=" + encodeURIComponent(st.filter));
    Object.keys(extra || {}).forEach(function (k) { if (extra[k]) q.push(k + "=" + encodeURIComponent(extra[k])); });
    return q.join("&");
  }

  function tableView(opts) {
    // opts: {id, api, columns, title, sub, notice, extraFilters, before, after}
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

    (opts.extraFilters || []).forEach(function (ef) {
      var sel = el("select", { "aria-label": ef.label });
      sel.appendChild(el("option", { value: "", text: "All " + ef.label }));
      (ef.options || []).forEach(function (o) {
        var op = el("option", { value: String(o), text: String(o) });
        if (String(st[ef.param] || "") === String(o)) op.selected = true;
        sel.appendChild(op);
      });
      sel.addEventListener("change", function () { st[ef.param] = sel.value; st.page = 1; refresh(); });
      controls.appendChild(el("label", {}, [el("span", { text: ef.label }), sel]));
    });

    var sizeSel = el("select", { "aria-label": "Rows per page" });
    [25, 50, 100, 200].forEach(function (n) {
      var op = el("option", { value: String(n), text: n + " / page" });
      if (n === st.page_size) op.selected = true;
      sizeSel.appendChild(op);
    });
    sizeSel.addEventListener("change", function () { st.page_size = parseInt(sizeSel.value, 10); st.page = 1; refresh(); });
    controls.appendChild(el("label", {}, [el("span", { text: "Page size" }), sizeSel]));
    panel.appendChild(controls);

    var mount = el("div");
    panel.appendChild(mount);
    root.textContent = "";
    root.appendChild(panel);
    if (opts.after) opts.after(panel);

    function refresh() {
      var extra = {};
      (opts.extraFilters || []).forEach(function (ef) { if (st[ef.param]) extra[ef.param] = st[ef.param]; });
      mount.textContent = "";
      mount.appendChild(el("p", { class: "loading", text: "Loading…" }));
      getJSON(opts.api + "?" + buildQuery(st, extra)).then(function (res) {
        mount.textContent = "";
        if (res.status !== 200) {
          mount.appendChild(el("div", { class: "notice bad", role: "alert",
            text: "Request rejected: " + (res.body.error || res.status) + (res.body.detail ? " (" + res.body.detail + ")" : "") }));
          return;
        }
        renderTable(mount, res.body, opts, st, refresh);
      });
    }
    refresh();
  }

  function renderTable(mount, body, opts, st, refresh) {
    var data = body.data || [];
    var tableWrap = el("div", { class: "table-wrap" });
    var table = el("table");
    table.appendChild(el("caption", { text: opts.title + " — " + (body.total != null ? body.total : data.length) + " row(s), read-only" }));
    var thead = el("thead");
    var htr = el("tr");
    htr.appendChild(el("th", { scope: "col", text: "Copy ID" }));
    opts.columns.forEach(function (c) {
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
    thead.appendChild(htr);
    table.appendChild(thead);

    var tbody = el("tbody");
    if (!data.length) {
      var tr = el("tr");
      tr.appendChild(el("td", { class: "empty" }, [document.createTextNode("No records to display.")]));
      tr.firstChild.setAttribute("colspan", String(opts.columns.length + 1));
      tbody.appendChild(tr);
    }
    data.forEach(function (row) {
      var tr = el("tr");
      tr.appendChild(idCell(row.row_id));
      opts.columns.forEach(function (c) {
        var v = c.get ? c.get(row) : row[c.key];
        var td;
        if (c.tag) { td = el("td", {}, [tag(v, statusClass(v))]); }
        else if (c.copy && v) { td = idCell(v); }
        else { td = el("td", { class: (c.num ? "num " : "") + (c.mono ? "mono" : "") }, [dash(v)]); }
        tr.appendChild(td);
      });
      tbody.appendChild(tr);
    });
    table.appendChild(tbody);
    tableWrap.appendChild(table);
    mount.appendChild(tableWrap);

    // pager
    var pager = el("div", { class: "pager" });
    var info = el("span", { class: "info", text: "Page " + (body.page || 1) + " of " + (body.total_pages || 1)
                            + " · " + (body.total != null ? body.total : data.length) + " total" });
    var prev = el("button", { class: "btn", type: "button", text: "‹ Prev" });
    var next = el("button", { class: "btn", type: "button", text: "Next ›" });
    prev.disabled = (body.page || 1) <= 1;
    next.disabled = (body.page || 1) >= (body.total_pages || 1);
    prev.addEventListener("click", function () { if (st.page > 1) { st.page--; refresh(); } });
    next.addEventListener("click", function () { st.page++; refresh(); });
    pager.appendChild(prev); pager.appendChild(next); pager.appendChild(info);
    mount.appendChild(pager);
  }

  // ---------------------------------------------------------------- views
  function card(k, v, small) {
    return el("div", { class: "card" }, [
      el("div", { class: "k", text: k }),
      el("div", { class: "v" + (small ? " small" : ""), text: (v === null || v === undefined || v === "") ? "—" : String(v) })
    ]);
  }

  function renderOverview() {
    var root = document.getElementById("view-root");
    getJSON(API.overview).then(function (res) {
      var ov = (res.body && res.body.overview) || {};
      var readiness = (res.body && res.body.readiness) || ov.dashboard_readiness;
      root.textContent = "";

      var head = el("section", { class: "panel" });
      head.appendChild(el("h1", { text: "Operations Overview" }));
      head.appendChild(el("p", { class: "sub" }, [
        document.createTextNode("Dashboard readiness: "),
        el("span", { class: "readiness-pill " + readinessClass(readiness), text: readiness || "—" })
      ]));
      (res.body.disclaimer || []).forEach(function (line, i) {
        if (i === 0) return;
        head.appendChild(el("div", { class: "notice", role: "note", text: line }));
      });
      root.appendChild(head);

      var statuses = el("section", { class: "panel" });
      statuses.appendChild(el("h2", { text: "Phase readiness" }));
      var srow = el("div", { class: "cards" });
      [["Phase 7.3", ov.phase7_3_readiness], ["Phase 7.4 review", ov.phase7_4_review_status],
       ["Phase 7.5 package", ov.phase7_5_package_status], ["Phase 7.6 tracker", ov.phase7_6_tracker_status],
       ["Phase 7.7 follow-up", ov.phase7_7_followup_status]].forEach(function (p) {
        srow.appendChild(el("div", { class: "card" }, [
          el("div", { class: "k", text: p[0] }),
          el("div", { class: "v small" }, [tag(p[1], statusClass(p[1]))])
        ]));
      });
      statuses.appendChild(srow);
      root.appendChild(statuses);

      var counts = el("section", { class: "panel" });
      counts.appendChild(el("h2", { text: "Counts" }));
      var grid = el("div", { class: "cards" });
      grid.appendChild(card("Source rows", ov.source_rows));
      grid.appendChild(card("Analyzed rows", ov.analyzed_rows));
      grid.appendChild(card("Review records", ov.review_state_records));
      grid.appendChild(card("Approved", ov.approved_reviews));
      grid.appendChild(card("Deferred", ov.deferred_reviews));
      grid.appendChild(card("Eligible decisions", ov.eligible_decisions));
      grid.appendChild(card("Excluded decisions", ov.excluded_decisions));
      grid.appendChild(card("Tracker records", ov.tracker_records));
      grid.appendChild(card("Manually completed", ov.manually_completed));
      grid.appendChild(card("Pending manual check", ov.pending_manual_check));
      grid.appendChild(card("Eligible follow-ups", ov.eligible_followups));
      grid.appendChild(card("Excluded follow-ups", ov.excluded_followups));
      grid.appendChild(card("Attention items", ov.attention_item_count));
      grid.appendChild(card("Integrity blockers", ov.integrity_blockers));
      grid.appendChild(card("Policy blockers", ov.policy_blockers));
      grid.appendChild(card("Source-change warnings", ov.source_change_warnings));
      counts.appendChild(grid);
      counts.appendChild(el("p", { class: "sub", text: "Package id: " + (ov.decision_package_id || "—")
        + " · Follow-up id: " + (ov.followup_id || "—") }));
      root.appendChild(counts);

      var boundary = el("section", { class: "panel" });
      boundary.appendChild(el("h2", { text: "Amazon & external-network boundary (all zero)" }));
      var bgrid = el("div", { class: "cards" });
      [["Amazon connections", ov.amazon_connections], ["SP-API calls", ov.amazon_sp_api_calls],
       ["Ads-API calls", ov.amazon_ads_api_calls], ["Amazon mutations", ov.amazon_mutations],
       ["Browser automation", ov.browser_automation_actions], ["External network calls", ov.external_network_calls]
      ].forEach(function (p) { bgrid.appendChild(card(p[0], p[1] == null ? 0 : p[1])); });
      boundary.appendChild(bgrid);
      root.appendChild(boundary);

      var exports = el("section", { class: "panel" });
      exports.appendChild(el("h2", { text: "Operations snapshot (offline export)" }));
      exports.appendChild(el("p", { class: "sub", text: "Owner-facing operational summary only. Not an Amazon upload file." }));
      var erow = el("div", { class: "export-row" });
      [["operations_snapshot.json", "/api/export/operations.json"],
       ["operations_snapshot.tsv", "/api/export/operations.tsv"],
       ["operations_snapshot.md", "/api/export/operations.md"]].forEach(function (e) {
        erow.appendChild(el("a", { class: "btn", href: e[1], download: e[0], text: "Download " + e[0] }));
      });
      exports.appendChild(erow);
      root.appendChild(exports);
    });
  }

  function optionsFrom(filters, key) { return (filters && filters[key]) || []; }

  function renderAnalysis() {
    getJSON(API.analysis + "?page_size=1").then(function (res) {
      var filters = (res.body && res.body.filters) || {};
      tableView({
        id: "analysis", api: API.analysis, title: "Analysis (Phase 7.3)",
        sub: "Accepted Phase 7.3 search-term analysis (read-only). No new recommendation is generated.",
        extraFilters: [
          { label: "Campaign", param: "campaign", options: optionsFrom(filters, "campaign") },
          { label: "Classification", param: "classification", options: optionsFrom(filters, "classification") },
          { label: "Currency", param: "currency", options: optionsFrom(filters, "currency") },
          { label: "Review status", param: "review_status", options: optionsFrom(filters, "review_status") }
        ],
        columns: [
          { key: "campaign", label: "Campaign", sortable: true },
          { key: "ad_group", label: "Ad group", sortable: true },
          { key: "customer_search_term", label: "Search term", sortable: true },
          { key: "match_type", label: "Match", sortable: true },
          { key: "currency", label: "Cur", sortable: true },
          { key: "spend", label: "Spend", num: true, sortable: true },
          { key: "sales", label: "Sales", num: true, sortable: true },
          { key: "acos", label: "ACoS", num: true, sortable: true },
          { key: "classification", label: "Classification", tag: true, sortable: true },
          { key: "attribution_window", label: "Attr. window" },
          { key: "review_status", label: "Review", tag: true, sortable: true }
        ]
      });
    });
  }

  function renderReviews() {
    tableView({
      id: "reviews", api: API.reviews, title: "Owner Review Status (Phase 7.4)",
      sub: "Local owner-review labels (read-only).",
      notice: "Review updates must be made through the accepted Phase 7.4 owner-review authority.",
      columns: [
        { key: "entity_id", label: "Entity ID", copy: true },
        { key: "entity_type", label: "Type" },
        { key: "review_status", label: "Status", tag: true },
        { key: "effective_status", label: "Effective", tag: true },
        { key: "source_change_state", label: "Source change", tag: true },
        { key: "content_hash_match", label: "Content match", get: function (r) { return String(r.content_hash_match); } },
        { key: "source_manifest_match", label: "Manifest match", get: function (r) { return String(r.source_manifest_match); } },
        { key: "revision", label: "Rev", num: true },
        { key: "owner_note", label: "Owner note" }
      ]
    });
  }

  function renderDecision() {
    tableView({
      id: "decision-packages", api: API.decisionPackages, title: "Decision Packages (Phase 7.5)",
      sub: "The current, lineage-matched decision package (read-only). Packages are never created or modified here.",
      before: function (panel) {
        getJSON(API.decisionPackages + "?page_size=1").then(function (res) {
          var b = res.body || {};
          var pkg = b.package;
          var box = el("div", { class: "notice" + (statusClass(b.status) === "bad" ? " bad" : "") });
          box.appendChild(el("strong", { text: "Package status: " }));
          box.appendChild(tag(b.status, statusClass(b.status)));
          panel.insertBefore(box, panel.children[panel.children.length - 1]);
          if (pkg) {
            var dl = el("dl", { class: "kv" });
            [["Package id", pkg.package_id], ["Readiness", pkg.readiness],
             ["Eligible actions", pkg.eligible_action_count], ["Excluded items", pkg.excluded_item_count],
             ["Duplicate identical", pkg.duplicate_identical_count], ["Duplicate conflict", pkg.duplicate_conflict_count],
             ["Source changed", pkg.source_changed_count], ["Content hash", pkg.package_content_sha256],
             ["Lineage current", String(pkg.lineage_current)]].forEach(function (p) {
              dl.appendChild(el("dt", { text: p[0] }));
              dl.appendChild(el("dd", { class: p[0] === "Content hash" ? "mono" : "", text: p[1] == null ? "—" : String(p[1]) }));
            });
            panel.insertBefore(dl, panel.children[panel.children.length - 1]);
          }
        });
      },
      columns: [
        { key: "package_item_id", label: "Item ID", copy: true },
        { key: "recommendation_type", label: "Type" },
        { key: "campaign", label: "Campaign" },
        { key: "customer_search_term", label: "Search term" },
        { key: "currency", label: "Cur" },
        { key: "attribution_window", label: "Attr." },
        { key: "action_description", label: "Action" },
        { key: "review_status", label: "Review", tag: true }
      ]
    });
  }

  function renderManual() {
    tableView({
      id: "manual-actions", api: API.manualActions, title: "Manual Actions (Phase 7.6)",
      sub: "Owner-entered manual-action records (read-only).",
      notice: "Status values are owner-entered records. No Amazon action was performed by this software.",
      columns: [
        { key: "tracker_record_id", label: "Tracker ID", copy: true },
        { key: "package_item_id", label: "Package item" },
        { key: "owner_status", label: "Status", tag: true },
        { key: "owner_checked_date", label: "Checked" },
        { key: "owner_completed_date", label: "Completed" },
        { key: "before_value", label: "Before", num: true },
        { key: "after_value", label: "After", num: true },
        { key: "before_value_currency", label: "Cur" },
        { key: "binding_state", label: "Binding", tag: true },
        { key: "local_revision", label: "Rev", num: true },
        { key: "owner_note", label: "Note" }
      ]
    });
  }

  function renderOutcomes() {
    tableView({
      id: "outcomes", api: API.outcomes, title: "Outcome Follow-up (Phase 7.7)",
      sub: "Observational before/after follow-ups (read-only).",
      notice: "Observational classifications describe an observed before/after change only and never establish causation.",
      columns: [
        { key: "followup_record_id", label: "Follow-up ID", copy: true },
        { key: "campaign", label: "Campaign" },
        { key: "customer_search_term", label: "Search term" },
        { key: "currency", label: "Cur" },
        { key: "attribution_window", label: "Attr." },
        { key: "outcome_classification", label: "Classification", tag: true },
        { key: "confidence_status", label: "Evidence", tag: true },
        { key: "spend_before_after", label: "Spend (b→a)", get: function (r) {
            var b = r.before_metrics || {}, a = r.after_metrics || {};
            return (b.spend == null ? "—" : b.spend) + " → " + (a.spend == null ? "—" : a.spend); } },
        { key: "sales_before_after", label: "Sales (b→a)", get: function (r) {
            var b = r.before_metrics || {}, a = r.after_metrics || {};
            return (b.sales == null ? "—" : b.sales) + " → " + (a.sales == null ? "—" : a.sales); } }
      ]
    });
  }

  function renderAttention() {
    tableView({
      id: "attention", api: API.attention, title: "Attention Required",
      sub: "A consolidated list of existing upstream statuses only. No new priority, urgency, or risk score is invented.",
      columns: [
        { key: "category", label: "Category", tag: true },
        { key: "section", label: "Section" },
        { key: "reference_id", label: "Reference", copy: true },
        { key: "detail", label: "Detail" }
      ]
    });
  }

  function renderLineage() {
    var root = document.getElementById("view-root");
    getJSON(API.lineage).then(function (res) {
      var ln = (res.body && res.body.lineage) || {};
      root.textContent = "";
      var panel = el("section", { class: "panel" });
      panel.appendChild(el("h1", { text: "Lineage & Integrity" }));
      panel.appendChild(el("p", { class: "sub", text: "Normalized relative display paths only — no absolute local path is ever exposed." }));

      function block(title, obj) {
        var sec = el("section", { class: "panel" });
        sec.appendChild(el("h2", { text: title }));
        var dl = el("dl", { class: "kv" });
        Object.keys(obj || {}).forEach(function (k) {
          var v = obj[k];
          if (v && typeof v === "object") v = JSON.stringify(v);
          dl.appendChild(el("dt", { text: k }));
          var mono = /sha256|hash/.test(k);
          dl.appendChild(el("dd", { class: mono ? "mono" : "", text: (v === null || v === undefined) ? "—" : String(v) }));
        });
        sec.appendChild(dl);
        return sec;
      }
      root.appendChild(panel);
      root.appendChild(block("Phase 7.3 source & integrity", ln.phase7_3));
      root.appendChild(block("Phase 7.4 review state", ln.phase7_4));
      root.appendChild(block("Phase 7.5 decision package", ln.phase7_5));
      root.appendChild(block("Phase 7.6 tracker", ln.phase7_6));
      root.appendChild(block("Phase 7.7 follow-up", ln.phase7_7));
      root.appendChild(block("Input directories (relative)", ln.input_directories));
    });
  }

  // ---------------------------------------------------------------- status bar + badges
  function refreshStatusBar() {
    getJSON(API.overview).then(function (res) {
      var ov = (res.body && res.body.overview) || {};
      var readiness = (res.body && res.body.readiness) || ov.dashboard_readiness;
      var bar = document.getElementById("status-metrics");
      bar.textContent = "";
      bar.appendChild(el("span", { class: "readiness-pill " + readinessClass(readiness), text: readiness || "—" }));
      bar.appendChild(el("span", { class: "readiness-pill", text: "Amazon calls: 0" }));
      setBadge("attention", ov.attention_item_count);
      setBadge("analysis", ov.analyzed_rows);
      setBadge("reviews", ov.review_state_records);
      setBadge("manual-actions", ov.tracker_records);
      setBadge("outcomes", ov.eligible_followups);
    });
  }
  function setBadge(id, n) {
    var b = document.getElementById("badge-" + id);
    if (!b) return;
    if (n === null || n === undefined) { b.hidden = true; return; }
    b.textContent = String(n); b.hidden = false;
  }

  // ---------------------------------------------------------------- router
  function route() {
    var id = (window.location.hash || "#overview").replace("#", "");
    var view = VIEWS.filter(function (v) { return v.id === id; })[0] || VIEWS[0];
    setActive(view.id);
    var root = document.getElementById("view-root");
    root.textContent = "";
    root.appendChild(el("p", { class: "loading", text: "Loading…" }));
    try { view.render(); } catch (e) {
      root.textContent = "";
      root.appendChild(el("div", { class: "notice bad", role: "alert", text: "Render error: " + e.message }));
    }
    document.getElementById("content").focus();
  }

  function init() {
    buildNav();
    window.addEventListener("hashchange", route);
    refreshStatusBar();
    route();
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
