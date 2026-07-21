/* Phase 7.4 Owner Review Dashboard — offline, vanilla JS. No external code, no network beyond the
 * same-origin local API. It never contacts Amazon and never performs an Amazon action; the owner is
 * the only manual bridge to Seller Central. Decimal values arrive as strings and are shown verbatim
 * — the browser formats but never recomputes an analytical value. */
"use strict";

const DASH = "—"; // em dash for missing values
const state = { model: null, view: "overview", sort: {}, filters: {}, pending: {}, selected: {} };

/* ------------------------------------------------------------------ helpers */
function el(tag, attrs, children) {
  const node = document.createElement(tag);
  if (attrs) for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") node.className = v;
    else if (k === "text") node.textContent = v;
    else if (k === "html") node.innerHTML = v;
    else if (k.startsWith("on") && typeof v === "function") node.addEventListener(k.slice(2), v);
    else if (v !== null && v !== undefined) node.setAttribute(k, v);
  }
  if (children) for (const c of [].concat(children)) if (c != null) node.append(c);
  return node;
}
function fmt(v) { return (v === null || v === undefined || v === "") ? DASH : String(v); }
function isMissing(v) { return v === null || v === undefined || v === ""; }
async function api(path, opts) {
  const res = await fetch(path, opts);
  const text = await res.text();
  let body = null;
  try { body = text ? JSON.parse(text) : null; } catch (e) { body = { raw: text }; }
  if (!res.ok) throw new Error((body && (body.detail || body.error)) || res.status);
  return body;
}
function toast(msg, bad) {
  const t = document.getElementById("toast");
  t.textContent = msg; t.hidden = false; t.className = "toast" + (bad ? " bad" : "");
  clearTimeout(toast._t); toast._t = setTimeout(() => { t.hidden = true; }, 3200);
}

/* ------------------------------------------------------------------ view registry */
const VIEWS = [
  { key: "overview", label: "Overview" },
  { key: "campaigns", label: "Campaigns", reviewable: true },
  { key: "ad_groups", label: "Ad Groups", reviewable: true },
  { key: "targets", label: "Targets", reviewable: true },
  { key: "search_terms", label: "Search Terms", reviewable: true },
  { key: "recommendations", label: "Recommendations", reviewable: true },
  { key: "blocked_recommendations", label: "Blocked", reviewable: true },
  { key: "manual_review_queue", label: "Manual Review", reviewable: true },
  { key: "observations", label: "Observations" },
  { key: "policy_requirements", label: "Owner Policy" },
  { key: "data_health", label: "Data Health" },
];

const COLUMNS = {
  campaigns: [
    ["campaign", "Campaign"], ["campaign_id", "Campaign ID"], ["spend", "Spend", 1],
    ["sales", "Sales", 1], ["orders", "Orders", 1], ["clicks", "Clicks", 1],
    ["impressions", "Impr.", 1], ["ctr", "CTR", 1], ["cpc", "CPC", 1],
    ["conversion_rate", "CVR", 1], ["acos", "ACoS", 1], ["roas", "ROAS", 1],
    ["classification", "Classification"], ["observation_count", "Obs.", 1],
    ["recommendation_count", "Recs", 1],
  ],
  ad_groups: [
    ["campaign", "Campaign"], ["ad_group", "Ad Group"], ["spend", "Spend", 1], ["sales", "Sales", 1],
    ["orders", "Orders", 1], ["clicks", "Clicks", 1], ["impressions", "Impr.", 1], ["cpc", "CPC", 1],
    ["conversion_rate", "CVR", 1], ["acos", "ACoS", 1], ["roas", "ROAS", 1], ["classification", "Classification"],
  ],
  targets: [
    ["campaign", "Campaign"], ["ad_group", "Ad Group"], ["targeting_expression", "Targeting"],
    ["match_type", "Match"], ["spend", "Spend", 1], ["sales", "Sales", 1], ["orders", "Orders", 1],
    ["clicks", "Clicks", 1], ["impressions", "Impr.", 1], ["cpc", "CPC", 1], ["conversion_rate", "CVR", 1],
    ["acos", "ACoS", 1], ["roas", "ROAS", 1], ["classification", "Classification"], ["reason", "Reason"],
  ],
  search_terms: [
    ["campaign", "Campaign"], ["ad_group", "Ad Group"], ["targeting", "Target"],
    ["customer_search_term", "Search Term"], ["match_type", "Match"], ["spend", "Spend", 1],
    ["sales", "Sales", 1], ["orders", "Orders", 1], ["clicks", "Clicks", 1], ["impressions", "Impr.", 1],
    ["cpc", "CPC", 1], ["conversion_rate", "CVR", 1], ["acos", "ACoS", 1], ["roas", "ROAS", 1],
    ["primary_classification", "Classification"], ["owner_review_label", "Review Label"],
  ],
  recommendations: [
    ["recommendation_id", "Rec ID"], ["recommendation_type", "Type"], ["entity_type", "Entity"],
    ["campaign", "Campaign"], ["ad_group", "Ad Group"], ["customer_search_term", "Search Term"],
    ["primary_classification", "Classification"], ["reason", "Reason"], ["confidence", "Conf."],
    ["policy_requirement", "Owner Policy"],
  ],
  blocked_recommendations: [
    ["blocked_item", "Blocked Item"], ["blocking_reason", "Blocking Reason"],
    ["missing_owner_policy", "Missing Policy"], ["missing_data", "Missing Data"],
    ["conflicting_evidence", "Conflicts"], ["required_owner_decision", "Required Decision"],
  ],
  manual_review_queue: [
    ["priority", "Priority", 1], ["entity", "Entity"], ["suggested_review", "Suggested Review"],
    ["reason", "Reason"], ["policy_dependency", "Policy Dependency"],
  ],
  observations: [
    ["reason_code", "Observation"], ["description", "Description"], ["row_count", "Rows", 1],
  ],
  policy_requirements: [
    ["requirement", "Requirement"], ["detail", "Detail"], ["status", "Status"],
  ],
};

/* ------------------------------------------------------------------ boot */
async function boot() {
  document.getElementById("drawer-close").addEventListener("click", closeDrawer);
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeDrawer(); });
  await reload();
}
async function reload() {
  try {
    state.model = await api("/api/model");
    state.pending = {}; state.selected = {};
    renderStatus(); renderNav(); renderView();
  } catch (e) {
    document.getElementById("view-root").replaceChildren(
      el("div", { class: "blocked-panel", text: "Failed to load dashboard: " + e.message }));
  }
}

/* ------------------------------------------------------------------ status bar + nav */
function renderStatus() {
  const m = state.model, ov = m.overview || {};
  const cont = document.getElementById("status-metrics");
  const ready = m.readiness || "";
  const cls = m.ok ? (m.readiness === "SESSION7_4_SOURCE_CHANGED_REVIEW_REQUIRED" ? "warn" : "ok") : "bad";
  const ac = m.amazon_counters || {};
  cont.replaceChildren(
    el("span", { class: "status-pill " + cls, text: ready }),
    el("span", { html: "source rows <b>" + fmt(ov.source_row_count) + "</b>" }),
    el("span", { html: "analyzed <b>" + fmt(ov.analyzed_row_count) + "</b>" }),
    el("span", { html: "queue <b>" + fmt(ov.decision_queue_row_count) + "</b>" }),
    el("span", { html: "blocked <b>" + fmt(ov.blocked_row_count) + "</b>" }),
    el("span", { html: "amazon calls <b>" + fmt(ac.amazon_api_calls) + "</b> · mutations <b>" + fmt(ac.amazon_mutations) + "</b>" }),
  );
}
function renderNav() {
  const m = state.model;
  const list = document.getElementById("nav-list");
  list.replaceChildren();
  for (const v of VIEWS) {
    const rows = Array.isArray(m[v.key]) ? m[v.key].length : null;
    const btn = el("button", {
      class: v.key === state.view ? "active" : "", type: "button",
      onclick: () => { state.view = v.key; renderNav(); renderView(); },
    }, [el("span", { text: v.label }), rows !== null ? el("span", { class: "count", text: rows }) : null]);
    list.append(el("li", null, btn));
  }
}

/* ------------------------------------------------------------------ view rendering */
function renderView() {
  const root = document.getElementById("view-root");
  root.replaceChildren();
  document.getElementById("content").focus();
  const m = state.model;
  if (!m.ok) return renderBlocked(root);
  if (state.view === "overview") return renderOverview(root);
  if (state.view === "data_health") return renderHealth(root);
  return renderTableView(root, state.view);
}

function renderBlocked(root) {
  const m = state.model;
  root.append(el("h1", { class: "view-title", text: "Source blocked" }));
  const panel = el("div", { class: "blocked-panel" });
  panel.append(el("p", { text: "The Phase 7.3 source did not validate. Recommendation data is hidden until it is fixed. Readiness: " + m.readiness }));
  const ul = el("ul");
  for (const r of (m.reasons || (m.data_health && m.data_health.reasons) || []))
    ul.append(el("li", { html: "<code>" + fmt(r.code) + "</code> — " + fmt(typeof r.detail === "string" ? r.detail : JSON.stringify(r.detail)) }));
  panel.append(ul);
  root.append(panel);
}

function renderOverview(root) {
  const ov = state.model.overview, h = state.model.data_health || {};
  root.append(el("h1", { class: "view-title", text: "Overview" }),
    el("p", { class: "view-sub", text: "Read-only summary of the promoted Phase 7.3 analysis. Suggested review only — no Amazon action has been performed." }));
  const cards = [
    ["Phase 7.3 readiness", ov.phase7_3_readiness, true], ["Source rows", ov.source_row_count],
    ["Analyzed rows", ov.analyzed_row_count], ["Decision queue", ov.decision_queue_row_count],
    ["Blocked rows", ov.blocked_row_count], ["Campaigns", ov.campaign_count],
    ["Ad groups", ov.ad_group_count], ["Targets", ov.targeting_count],
    ["Search terms", ov.search_term_count], ["Observations", ov.observation_count],
    ["Recommendations", ov.recommendation_count], ["Currency", ov.currency, true],
    ["Attribution", (ov.attribution_windows || []).join(", "), true],
    ["Report range", (ov.report_date_range ? (fmt(ov.report_date_range.earliest) + " → " + fmt(ov.report_date_range.latest)) : DASH), true],
    ["Manifest validation", ov.manifest_validation, true], ["Source integrity", ov.source_integrity_status, true],
    ["Review items", ov.review_item_count], ["Load status", ov.dashboard_load_status, true],
  ];
  const grid = el("div", { class: "kpi-grid" });
  for (const [label, value, small] of cards)
    grid.append(el("div", { class: "kpi" }, [
      el("div", { class: "label", text: label }),
      el("div", { class: "value" + (small ? " small" : ""), text: fmt(value) }),
    ]));
  root.append(grid);
}

function renderHealth(root) {
  const h = state.model.data_health || {};
  root.append(el("h1", { class: "view-title", text: "Data Health" }),
    el("p", { class: "view-sub", text: "Source integrity, hashes, schema versions and count consistency." }));
  const dl = el("dl", { class: "health-dl" });
  const cons = h.source_analyzed_consistency || {};
  const rows = [
    ["Source directory", h.source_directory], ["Manifest status", h.manifest_status],
    ["Manifest hash verified", String(h.manifest_hash_verified)],
    ["Missing files", (h.missing_files || []).join(", ") || "none"],
    ["Invalid files", (h.invalid_files || []).join(", ") || "none"],
    ["Currencies", (h.currencies || []).join(", ")], ["Attribution windows", (h.attribution_windows || []).join(", ")],
    ["Cumulative 7.2 rows", h.cumulative_phase7_2_row_count], ["Phase 7.2 readiness", h.phase7_2_readiness],
    ["Source/analyzed consistent", String(cons.consistent) + " (" + fmt(cons.source_row_count) + "=" + fmt(cons.analyzed_row_count) + "+" + fmt(cons.skipped_row_count) + "+" + fmt(cons.blocked_row_count) + ")"],
    ["Last analysis state", h.last_successful_analysis_state],
    ["Invalid numeric count", h.invalid_numeric_count],
  ];
  for (const [k, v] of rows) dl.append(el("dt", { text: k }), el("dd", { class: "mono", text: fmt(v) }));
  root.append(el("div", { class: "table-wrap" }, el("div", { class: "drawer-body" }, dl)));

  root.append(el("h2", { class: "view-title", text: "Artifacts" }));
  const wrap = el("div", { class: "table-wrap" });
  const t = el("table");
  t.append(el("thead", null, el("tr", null, [el("th", { text: "Artifact" }), el("th", { text: "SHA-256" })])));
  const tb = el("tbody");
  for (const name of (h.artifact_list || []))
    tb.append(el("tr", null, [el("td", { text: name }), el("td", { class: "mono", text: fmt((h.artifact_hashes || {})[name]) })]));
  t.append(tb); wrap.append(t); root.append(wrap);
  const sv = h.schema_versions || {};
  root.append(el("p", { class: "view-sub mono", text: "schemas: manifest=" + fmt(sv.manifest) + " analysis=" + fmt(sv.analysis) + " thresholds=" + fmt(sv.thresholds) }));
}

/* ------------------------------------------------------------------ tables */
function currentRows(view) {
  const raw = (state.model[view] || []).slice();
  const f = state.filters[view] || {};
  let rows = raw.filter((r) => rowMatches(r, f));
  const s = state.sort[view];
  if (s) rows = sortRows(rows, s.field, s.dir);
  return rows;
}
function rowMatches(r, f) {
  if (f.text) {
    const hay = Object.values(r).map((v) => (v == null ? "" : String(v))).join(" ").toLowerCase();
    if (!hay.includes(f.text.toLowerCase())) return false;
  }
  if (f.classification && r.primary_classification !== f.classification && r.classification !== f.classification) return false;
  if (f.campaign && r.campaign !== f.campaign) return false;
  if (f.review) {
    const st = effectiveStatus(r.entity_id);
    if (st !== f.review) return false;
  }
  for (const [field, min] of [["clicks", f.minClicks], ["spend", f.minSpend], ["orders", f.minOrders]]) {
    if (min !== undefined && min !== "" && !isNaN(min)) {
      const v = r[field];
      if (isMissing(v) || Number(v) < Number(min)) return false;
    }
  }
  return true;
}
function sortRows(rows, field, dir) {
  const mul = dir === "desc" ? -1 : 1;
  return rows.sort((a, b) => {
    const av = a[field], bv = b[field];
    const am = isMissing(av), bm = isMissing(bv);
    if (am && bm) return 0;
    if (am) return 1;   // missing always sorts last, never treated as zero
    if (bm) return -1;
    const an = Number(av), bn = Number(bv);
    if (!isNaN(an) && !isNaN(bn)) return (an - bn) * mul;
    return String(av).localeCompare(String(bv)) * mul;
  });
}

function effectiveStatus(entityId) {
  if (state.pending[entityId] && state.pending[entityId].review_status !== undefined)
    return state.pending[entityId].review_status;
  const rec = (state.model.review_state || {})[entityId];
  return rec ? (rec.review_status || "UNREVIEWED") : "UNREVIEWED";
}
function currentNote(entityId) {
  if (state.pending[entityId] && state.pending[entityId].owner_note !== undefined)
    return state.pending[entityId].owner_note;
  const rec = (state.model.review_state || {})[entityId];
  return rec ? (rec.owner_note || "") : "";
}

function renderTableView(root, view) {
  const meta = VIEWS.find((v) => v.key === view);
  const cols = COLUMNS[view] || [];
  root.append(el("h1", { class: "view-title", text: meta.label }));
  root.append(el("p", { class: "view-sub", text: meta.reviewable ? "Suggested review only. Choose a local status; nothing is sent to Amazon." : "Read-only." }));

  root.append(buildToolbar(view));
  if (meta.reviewable) root.append(buildReviewBar(view));

  const rows = currentRows(view);
  if (rows.length === 0) { root.append(emptyState(view)); return; }

  const wrap = el("div", { class: "table-wrap" });
  const table = el("table");
  const head = el("tr");
  if (meta.reviewable) head.append(el("th", null, el("input", { type: "checkbox", "aria-label": "select all", onchange: (e) => selectAll(view, e.target.checked) })));
  for (const [field, label, num] of cols) {
    const th = el("th", { class: "sortable" + (num ? " num" : "") + sortClass(view, field), text: label, tabindex: "0" });
    th.addEventListener("click", () => toggleSort(view, field));
    th.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); toggleSort(view, field); } });
    head.append(th);
  }
  if (meta.reviewable) { head.append(el("th", { text: "Review Status" })); head.append(el("th", { text: "Owner Note" })); head.append(el("th", { text: "" })); }
  table.append(el("thead", null, head));

  const tb = el("tbody");
  for (const r of rows) tb.append(buildRow(view, meta, cols, r));
  table.append(tb);
  wrap.append(table); root.append(wrap);
  root.append(el("p", { class: "view-sub", text: rows.length + " row(s) shown." }));
}

function buildRow(view, meta, cols, r) {
  const tr = el("tr");
  if (meta.reviewable) {
    tr.append(el("td", null, el("input", {
      type: "checkbox", checked: state.selected[r.entity_id] ? "checked" : null,
      "aria-label": "select row",
      onchange: (e) => { state.selected[r.entity_id] = e.target.checked; },
    })));
  }
  for (const [field, , num] of cols) {
    const v = r[field];
    tr.append(el("td", { class: (num ? "num " : "") + (isMissing(v) ? "missing" : ""), text: fmt(v) }));
  }
  if (meta.reviewable) {
    const st = effectiveStatus(r.entity_id);
    const rec = (state.model.review_state || {})[r.entity_id];
    const sel = el("select", { class: "status-select", "aria-label": "review status" });
    for (const s of state.model.review_statuses) sel.append(el("option", { value: s, text: s.replace(/_/g, " "), selected: s === st ? "selected" : null }));
    sel.addEventListener("change", (e) => setPending(r.entity_id, "review_status", e.target.value));
    const note = el("input", { class: "note-input", type: "text", value: currentNote(r.entity_id), "aria-label": "owner note", placeholder: "note…" });
    note.addEventListener("input", (e) => setPending(r.entity_id, "owner_note", e.target.value));
    const tdStatus = el("td", null, sel);
    if (rec && rec.requires_re_review) tdStatus.append(el("span", { class: "chip changed", text: "source changed — re-review" }));
    tr.append(tdStatus, el("td", null, note),
      el("td", null, el("button", { class: "link", type: "button", text: "details", onclick: () => openDrawer(r) })));
  }
  return tr;
}

function emptyState(view) {
  const ov = state.model.overview || {};
  const box = el("div", { class: "empty-state" });
  box.append(el("strong", { text: "No items currently meet the manual-review thresholds." }));
  box.append(el("p", { text: "This is expected when the analysis found nothing that requires an owner decision. The rest of the dashboard remains fully available." }));
  box.append(el("p", { class: "view-sub", text: "analyzed rows: " + fmt(ov.analyzed_row_count) + " · observations: " + fmt(ov.observation_count) + " · campaigns: " + fmt(ov.campaign_count) + " · search terms: " + fmt(ov.search_term_count) }));
  return box;
}

/* ------------------------------------------------------------------ sorting */
function sortClass(view, field) {
  const s = state.sort[view];
  if (!s || s.field !== field) return "";
  return s.dir === "asc" ? " sort-asc" : " sort-desc";
}
function toggleSort(view, field) {
  const s = state.sort[view];
  if (s && s.field === field) s.dir = s.dir === "asc" ? "desc" : "asc";
  else state.sort[view] = { field, dir: "asc" };
  renderView();
}

/* ------------------------------------------------------------------ toolbar / filters */
function buildToolbar(view) {
  const f = state.filters[view] || (state.filters[view] = {});
  const bar = el("div", { class: "toolbar" });
  bar.append(mkField("Search", el("input", { type: "search", value: f.text || "", oninput: (e) => { f.text = e.target.value; renderView(); } })));
  const filters = state.model.filters || {};
  const classSel = el("select", { onchange: (e) => { f.classification = e.target.value; renderView(); } });
  classSel.append(el("option", { value: "", text: "All classifications" }));
  for (const c of filters.classifications || []) classSel.append(el("option", { value: c, text: c, selected: f.classification === c ? "selected" : null }));
  bar.append(mkField("Classification", classSel));

  const campSel = el("select", { onchange: (e) => { f.campaign = e.target.value; renderView(); } });
  campSel.append(el("option", { value: "", text: "All campaigns" }));
  for (const c of filters.campaigns || []) campSel.append(el("option", { value: c, text: c.length > 40 ? c.slice(0, 40) + "…" : c, selected: f.campaign === c ? "selected" : null }));
  bar.append(mkField("Campaign", campSel));

  const revSel = el("select", { onchange: (e) => { f.review = e.target.value; renderView(); } });
  revSel.append(el("option", { value: "", text: "All review statuses" }));
  for (const s of state.model.review_statuses) revSel.append(el("option", { value: s, text: s.replace(/_/g, " "), selected: f.review === s ? "selected" : null }));
  bar.append(mkField("Review status", revSel));

  bar.append(mkField("Min clicks", el("input", { type: "number", min: "0", value: f.minClicks || "", oninput: (e) => { f.minClicks = e.target.value; renderView(); } })));
  bar.append(mkField("Min spend", el("input", { type: "number", min: "0", step: "0.01", value: f.minSpend || "", oninput: (e) => { f.minSpend = e.target.value; renderView(); } })));
  bar.append(mkField("Min orders", el("input", { type: "number", min: "0", value: f.minOrders || "", oninput: (e) => { f.minOrders = e.target.value; renderView(); } })));

  bar.append(el("div", { class: "spacer" }));
  bar.append(el("button", { class: "btn", type: "button", text: "Clear", onclick: () => { state.filters[view] = {}; renderView(); } }));
  return bar;
}
function mkField(label, control) { return el("div", { class: "field" }, [el("label", { text: label }), control]); }

/* ------------------------------------------------------------------ review + export bar */
function buildReviewBar(view) {
  const bar = el("div", { class: "review-bar toolbar" });
  const bulkSel = el("select", { class: "status-select", "aria-label": "bulk status" });
  for (const s of state.model.review_statuses) bulkSel.append(el("option", { value: s, text: s.replace(/_/g, " ") }));
  bar.append(mkField("Bulk status", bulkSel));
  bar.append(el("button", { class: "btn", type: "button", text: "Apply to selected", onclick: () => bulkApply(view, bulkSel.value) }));
  bar.append(el("button", { class: "btn primary", type: "button", text: "Save review", onclick: () => saveReview() }));
  bar.append(el("button", { class: "btn", type: "button", text: "Reset unsaved", onclick: () => { state.pending = {}; renderView(); toast("Unsaved changes discarded."); } }));
  bar.append(el("button", { class: "btn", type: "button", text: "Reload from disk", onclick: () => reload() }));
  bar.append(el("div", { class: "spacer" }));

  const fmtSel = el("select", { "aria-label": "export format" });
  for (const o of ["tsv", "json", "markdown"]) fmtSel.append(el("option", { value: o, text: o.toUpperCase() }));
  const scopeSel = el("select", { "aria-label": "export scope" });
  for (const [v, t] of [["all", "All reviewable"], ["filtered", "Current filtered"], ["approved_only", "Approved only"]]) scopeSel.append(el("option", { value: v, text: t }));
  bar.append(mkField("Export format", fmtSel), mkField("Export scope", scopeSel));
  bar.append(el("button", { class: "btn", type: "button", text: "Export", onclick: () => doExport(view, fmtSel.value, scopeSel.value) }));
  return bar;
}
function setPending(entityId, field, value) {
  (state.pending[entityId] = state.pending[entityId] || {})[field] = value;
}
function selectAll(view, checked) {
  for (const r of currentRows(view)) state.selected[r.entity_id] = checked;
  renderView();
}
function bulkApply(view, status) {
  let n = 0;
  for (const r of currentRows(view)) if (state.selected[r.entity_id]) { setPending(r.entity_id, "review_status", status); n++; }
  renderView();
  toast(n ? (n + " row(s) set to " + status.replace(/_/g, " ") + " — not yet saved.") : "Select rows first.", !n);
}
async function saveReview() {
  const updates = [];
  for (const [entity_id, p] of Object.entries(state.pending)) {
    const rec = (state.model.review_state || {})[entity_id] || {};
    updates.push({
      entity_id,
      review_status: p.review_status !== undefined ? p.review_status : (rec.review_status || "UNREVIEWED"),
      owner_note: p.owner_note !== undefined ? p.owner_note : (rec.owner_note || ""),
    });
  }
  if (!updates.length) return toast("Nothing to save.");
  try {
    const res = await api("/api/review/save", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ updates }) });
    toast("Saved " + res.count + " local review label(s). No Amazon action performed.");
    await reload();
  } catch (e) { toast("Save failed: " + e.message, true); }
}
async function doExport(view, format, scope) {
  const rows = currentRows(view);
  const entity_ids = scope === "filtered" ? rows.map((r) => r.entity_id) : null;
  const filter_summary = "view=" + view + "; scope=" + scope + "; shown=" + rows.length;
  try {
    const res = await api("/api/review/export", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ format, scope, entity_ids, filter_summary }),
    });
    const ex = res.export;
    downloadBlob(ex.name, ex.content, format);
    toast("Exported " + ex.item_count + " item(s) to " + ex.name + " — for manual review only.");
  } catch (e) { toast("Export failed: " + e.message, true); }
}
function downloadBlob(name, content, format) {
  const type = format === "json" ? "application/json" : (format === "markdown" ? "text/markdown" : "text/tab-separated-values");
  const blob = new Blob([content], { type });
  const url = URL.createObjectURL(blob);
  const a = el("a", { href: url, download: name });
  document.body.append(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

/* ------------------------------------------------------------------ detail drawer */
function openDrawer(r) {
  const drawer = document.getElementById("drawer");
  document.getElementById("drawer-title").textContent = r.recommendation_id || r.entity_id;
  const body = document.getElementById("drawer-body");
  const dl = el("dl");
  const rec = (state.model.review_state || {})[r.entity_id];
  const skip = new Set(["content_sha256", "evidence_detail"]);
  for (const [k, v] of Object.entries(r)) {
    if (skip.has(k)) continue;
    dl.append(el("dt", { text: k }), el("dd", { class: "mono", text: (v && typeof v === "object") ? JSON.stringify(v) : fmt(v) }));
  }
  if (rec) { dl.append(el("dt", { text: "review_status" }), el("dd", { text: fmt(rec.review_status) })); dl.append(el("dt", { text: "source_change_state" }), el("dd", { text: fmt(rec.source_change_state) })); }
  body.replaceChildren(el("div", { class: "drawer-body" }, dl));
  drawer.hidden = false; drawer.setAttribute("aria-hidden", "false");
  document.getElementById("drawer-close").focus();
}
function closeDrawer() {
  const d = document.getElementById("drawer");
  d.hidden = true; d.setAttribute("aria-hidden", "true");
}

document.addEventListener("DOMContentLoaded", boot);
