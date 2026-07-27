# Session 7.13 — Action Confirmation Modal Hotfix

**Scope:** Repair the Phase 7.13 Unified Owner Console action *preparation and confirmation UI* so every
supported action can be understood and executed safely. No new business capability. No Phase 7.14. No
weakening of the accepted session / CSRF / confirmation-token / audit / network / Amazon boundaries.

| | |
|---|---|
| Branch | `hotfix-phase7-13-action-modal` |
| Baseline | `1145a186b6ee7eb4da01b5021d27d9344b1b5bd0` |
| Checkpoint tag | `phase7-13-action-modal-hotfix-checkpoint-1145a18` |
| Implementation commit | `7f102742c45d8aeaef26702b319e50e4ceecd911` |
| Proof commit | see `SESSION7_13-ACTION-MODAL-HOTFIX-PROOF.json` (docs commit) |
| Backend (`phase7_unified_owner_console.py`) | **UNCHANGED** (0 server files in the fix commit) |

---

## 1. Exact root cause

The reported symptom was: *"clicking an Overview action opens a modal titled `Confirm action` with a
blank body and only Cancel / Confirm & run."*

Reproduction was done two independent ways against the **real local HTTP server** at the accepted
baseline `1145a18`:

1. **From-scratch dependency-free Node DOM harness** loading the real `app.js`.
2. **Real headless Microsoft Edge** (Chrome DevTools Protocol) driving the real page.

**Finding (evidence-based, not guessed):** the accepted-baseline modal *does* populate for a successful
`prepare` — both the DOM harness and real Edge rendered the title (`Confirm: refresh-overview`) and the
full detail body, with **no console errors**. So a literally-blank body for a *successful* prepare is not
a state the committed baseline produces.

What the reproduction *did* confirm is why the confirmation surface is unsafe/blank-prone and why the
defect could reach owner QA undetected — the true root cause is a **combination**:

- **No rendering test coverage.** The confirmation modal was populated by imperatively mutating static
  `index.html` nodes inside `openModal()`, and *every* existing Phase 7.13 test only string-scans
  `app.js` — none ever rendered the modal. Any state in which `openModal` fails to fill `#modal-desc`
  (a partial/stale asset, a response the code does not fully handle, or a future regression) presents
  exactly the reported static `Confirm action` + empty-body modal, and CI stays green.
- **Unhandled unsuccessful prepare.** `startAction()` handled only HTTP 200. A failed / `BLOCKED` /
  `SESSION_REQUIRED` / `CSRF_BLOCKED` prepare was dropped to a transient `toast()` and **never** rendered
  as a readiness/reason panel — so on any prepare failure the owner is left with no usable confirmation
  surface.
- **Confirm button never gated on the phrase.** Real Edge showed `modal-execute.disabled === false` on a
  confirmation action with an empty input. The exact-match rule was enforced *only* server-side; the
  owner got no local signal and could submit a wrong/empty phrase.
- **Missing modal UX guarantees:** no focus trap, no execution lock (double-click / token reuse), no
  deterministic Escape/backdrop policy, and export actions never surfaced the produced files.

The fix rebuilds the modal to fully populate for every successful prepare, render a bounded reason panel
for every failed/blocked prepare, gate Confirm on an exact phrase match, trap focus, lock during
execution, surface export results, and — critically — adds a DOM harness that renders the real `app.js`
so the contract is enforced. This eliminates any blank/partial state and every confirmed contract gap.

## 2. Reproduction steps

```
# real server (synthetic offline workspace, loopback only)
python scratchpad/server_run.py            # serves http://127.0.0.1:8713
# 1) Node DOM harness against the real app.js + real backend envelopes
node tests/phase7_13_modal_dom_harness.js <fixtures.json>
# 2) real Edge (CDP), baseline vs fixed:
#    baseline -> modal renders (title changes, body filled) — literal blank NOT reproduced
#    fixed    -> full contract body + phrase-gated Confirm, no console errors
```

## 3. Files changed

| File | Change |
|---|---|
| `production/phase7_unified_owner_console_static/app.js` | Action machinery rewrite: `openModal` (full contract), new `openModalBlocked` (reason panel), exact phrase gate, focus trap, execution lock, export result, owner-facing title threaded through `startAction(action, params, label)`. `.innerHTML` never used; token stays in closure memory. |
| `production/phase7_unified_owner_console_static/index.html` | Modal markup: added `#modal-canonical`, `#modal-readiness`, `#modal-phrase-required`; kept `role="alert"`/`aria-live`, `aria-modal`, labelled title, described body. |
| `production/phase7_unified_owner_console_static/styles.css` | Modal scroll (`max-height:88vh; overflow-y:auto`, `overflow-x:hidden`), styles for the new rows + export list. |
| `tests/test_phase7_13_unified_owner_console.py` | `TestActionModalStatic` (static contract + secrecy) and `TestActionModalDom` (drives the 40-check Node harness with real backend envelopes). Added `import subprocess`, `import shutil`. |
| `tests/phase7_13_modal_dom_harness.js` | **New** dependency-free Node DOM harness. |
| `.gitattributes` | Pin the new harness to `eol=lf` (matches the 7.13 asset block). |

## 4. Backend / API changes

**None.** The independently demonstrated root cause is entirely frontend. The `prepare` response already
carries every field the modal needs (`action_token`, `canonical_action`, `target_ids`,
`expected_authority`, `expected_effect`, `network_use`, `local_state_changes`, `upstream_state_changes`,
`requires_confirmation`, `confirmation_phrase`, `expires_in_seconds`, `readiness`), and the `execute`
response already carries `readiness`, `upstream_result_id`, `authority`, `policy_result`,
`failure_reason`, and `upstream_summary.exports`. The owner-facing title is derived client-side from the
triggering button label. `production/phase7_unified_owner_console.py` is byte-identical to baseline.

## 5. UI behavior — before / after

| Aspect | Before (baseline) | After (fix) |
|---|---|---|
| Successful prepare | 7-row `dl` only; terse labels; canonical name only | Owner title + canonical action + readiness pill + authority + target(s) + expected effect + network access + local & upstream state changes + expiration + exact phrase |
| Failed / BLOCKED / SESSION / CSRF prepare | transient toast, **no modal** | bounded readiness + owner reason panel; no Confirm; no stale token; no stack trace |
| Confirm & run | always enabled | disabled until typed phrase matches **exactly** (no trim/case-fold/normalize) |
| Enter key | n/a | submits only when the gate would enable Confirm |
| Wrong phrase | server rejects after submit | Confirm stays disabled, required phrase stays visible, **token not consumed**, **no network call** |
| Execution | text "Running…", auto-close after 900 ms | Cancel+Confirm disabled, "Working…", single-use token, no double-run, result stays visible until owner closes |
| Export result | "Result id: <hash>" only | safe relative filenames + download links (browser download); no absolute path |
| Focus / keyboard | Escape only | focus trap, initial focus, focus returns to trigger, Escape/Cancel close before execution, deliberate no-dismiss backdrop |
| Secrets | token in closure only | unchanged — token/CSRF/session never in DOM/text/attributes |

## 6. Tests added

`tests/phase7_13_modal_dom_harness.js` renders the **real** `app.js` in a minimal DOM and runs **40
contract checks** (fetch shimmed with real backend envelopes; no Internet), covering: non-empty modal;
title / canonical / authority / effect / network / local / upstream / expiration rendering; exact phrase
rendering + input presence + gating (exact enables; wrong/case/whitespace rejected); token/CSRF/session/
endpoint-secret never rendered; failed/BLOCKED/SESSION/CSRF prepare shows a reason and no blank modal;
bounded JS-exception handling; Cancel/Escape close; focus-return; Enter-after-exact; double-click blocked;
token consumed once; success/failure/export/refresh/verify result rendering; all 15 actions avoid a blank
modal; no external request; no unsafe `innerHTML`; deliberate no-dismiss backdrop.

`TestActionModalStatic` asserts the static modal contract in `index.html` and source-level gating/secrecy.
`TestActionModalDom` runs the harness via `node` (skips gracefully only if `node` is absent).

## 7. Test totals

| Suite | Result |
|---|---|
| Modal DOM harness (node) | **40 / 40 PASS**, 0 fail |
| Phase 7.13 focused (`test_phase7_13_unified_owner_console`) | **Ran 269, OK** (incl. 3 new modal tests) |
| Phase 7.12 + network/connectivity (`7_12`, `network_policy`, `connectivity_policy`, `connectivity_surface`, `connected_services`) | **Ran 292, OK** |
| Full in-place (`unittest discover -s tests`) | **Ran 4165, OK (skipped=4)**, 0 fail / 0 error |
| `compileall production core tests` | **exit 0** |

## 8. Fresh-worktree comparison

Clean `git worktree` at baseline `1145a18` vs the feature commit `7f10274`, full `unittest discover`.

**A fresh worktree does not contain `runs/T2`** (it is git-ignored), so the T2-regeneration and
Session 5D certification suites cannot run there. Both trees are therefore **non-zero**, and the correct
verdict is a *differential* one:

**`FRESH_WORKTREE_FULL_SUITE_BASELINE_EQUIVALENT_NONZERO`**

| | Collected | Ran | Passed | Skipped | Failures | Errors | Exit |
|---|---|---|---|---|---|---|---|
| Baseline `1145a18` | 4162 | 4160 | 3806 | 329 | 1 | 14 | 1 |
| Feature `7f10274` | 4165 | 4163 | 3809 | 329 | 1 | 14 | 1 |
| **Delta** | **+3** | **+3** | **+3** | **0** | **0** | **0** | **same** |

Node-level differential across all 4149 shared test nodes: **0 lost baseline passes, 0 verdict changes,
0 new failures, 0 new errors, 0 broadened skips, 0 changed skip reasons.** The only new nodes are the
three added modal tests, all `ok` (the Node DOM-harness test did **not** skip). The 1 failure + 14 errors
are identical in both trees and are entirely `test_backend_semantic_quality`, `test_backend_phrase_integrity`
and `test_session5d_certification` T2 cases that require the absent `runs/T2` dataset — unrelated to
Phase 7.13 and to this hotfix.

The **in-place** suite (where `runs/T2` is present) is fully green in both: see §7 — `Ran 4165, OK
(skipped=4)`.

## 9. Security regression results

Backend untouched, so all accepted server controls are preserved by construction and re-verified by the
green Phase 7.12 / Phase 7.13 / network suites:

- Loopback-only bind + strict `Host` validation; session cookie + CSRF-on-POST; single-use tokens bound
  to session/action/params/target; Phase 7.12 live-send **double gate**; fixed **15-action allowlist**;
  accepted-authority dispatch; audit-chain blocking; constant-zero Seller-Central counters; no Seller
  Central paths.
- Frontend static scan: **no** external request / CDN / `XMLHttpRequest` / WebSocket / `eval`; **no**
  `innerHTML` / `localStorage` / `sessionStorage` / `document.cookie`; **no** token/CSRF written to DOM
  text; **no** absolute local path; CSRF held in a closure variable and sent as `X-CSRF-Token`; session
  via `credentials: same-origin`. The opaque preparation token lives only in closure memory.

## 10. Known limitations

1. **The literal "blank body for a successful prepare" did not reproduce on the accepted baseline** — a
   from-scratch DOM harness and real headless Edge both render the baseline body. The fix hardens and
   completes the confirmation contract (the confirmed defects — no phrase gate, no failure modal, no focus
   trap/execution lock, no export result — *did* reproduce) and adds the previously-absent rendering tests
   so no supported action can present a blank/partial or ungated confirmation.
2. **Exact `===` phrase gate is intentionally stricter than the server.** The accepted server `strip()`s
   the submitted phrase; the frontend gate compares exactly (no trim), so it can keep Confirm disabled for
   a trailing-space phrase the server would accept. This never *enables* a phrase the server would reject
   (safe), and is deliberate for a precise confirmation.
3. **Focus-return after a *successful* execute.** On success `route()` re-renders the view behind the
   modal, replacing the original trigger button; focus-return is exact for the Cancel / Escape / blocked
   paths (no re-render) and falls back to document body after a re-rendering success.
4. **DOM harness fidelity.** The harness is a faithful minimal shim, not a full browser; it is complemented
   by the manual real-browser QA in §11. Committed DOM tests skip only if `node` is unavailable.

## 11. Owner browser QA checklist

Run against the real local server (`serve` on `127.0.0.1`). Expected visible behavior in **bold**.

**Setup**
- [ ] Open the console in **Chrome** and in **Edge**; **hard refresh** (Ctrl+Shift+R) → console loads, boundary banner visible, **no console errors**.
- [ ] Start a **new server session** (restart) → Overview renders; DevTools ▸ Network shows only bounded, same-origin `/api/v1/...` responses (no external host).

**Overview actions**
- [ ] **Refresh overview** → modal titled "Refresh overview"; body shows Canonical action `refresh-overview`, a readiness pill, and rows for Authority / Target(s) / Expected effect / Network access / Local & Upstream state changes / Confirmation window. **Confirm & run enabled** (no phrase required). Click it → result shows **"Completed — SESSION7_13_ACTION_COMPLETED"**, counts refresh, modal stays open until **Close**.
- [ ] **Export snapshot** → confirm → result lists **safe relative export filenames** and **Download** links; clicking a link downloads the file. **No absolute path shown.**
- [ ] **Verify system state** → confirm → result shows completion + result id.

**Confirmation action (e.g. Watchlists ▸ Run)**
- [ ] Modal shows the **exact required confirmation phrase**; **Confirm & run is disabled**.
- [ ] Type a **wrong** phrase → Confirm stays disabled, local hint shown, **no network call** (token preserved).
- [ ] Type a phrase with **different case** or a **trailing space** → Confirm stays disabled.
- [ ] Type the **exact** phrase → Confirm enables; **Enter** submits; during execution Cancel+Confirm are disabled and show "Working…"; **a second click does nothing**.
- [ ] After execution the **result/error is visible** (never a false completion); the modal does not close until you press **Close**.

**Dismiss / keyboard / a11y**
- [ ] **Cancel** closes; **Escape** closes *before* execution but is ignored *during* execution; clicking the **dimmed backdrop does not dismiss** a prepared confirmation.
- [ ] After closing, **focus returns to the action button** you clicked.
- [ ] Tab / Shift+Tab **cycle within the modal only** (focus trap). Usable at **1366×768** with **no horizontal overflow**; long content **scrolls inside** the modal.

**Failure paths (where practical)**
- [ ] Simulate an **expired session** (restart server, keep the tab) then click an action → modal shows a **"Session expired…"** reason, **no Confirm & run**, no stack trace.
- [ ] Prepare an action with a missing/invalid target → modal shows a **BLOCKED reason**, not a blank body.

---

*Do not merge. Do not tag a new Phase 7.13 acceptance during implementation. Recommended next step: an
independent hotfix acceptance audit (see final response).*
