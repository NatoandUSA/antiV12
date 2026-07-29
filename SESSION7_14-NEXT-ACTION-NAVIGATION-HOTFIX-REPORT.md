# Session 7.14 — Next-Action Navigation / Blank Modal Hotfix

**Readiness:** `PHASE7_14_NEXT_ACTION_NAVIGATION_HOTFIX_READY_FOR_INDEPENDENT_ACCEPTANCE_AUDIT`

| | |
|---|---|
| Branch | `hotfix-phase7-14-next-action-navigation` |
| Accepted, merged baseline | `b5324f83f326664660bb9084f1696aefb28c151c` |
| Checkpoint tag | `phase7-14-next-action-navigation-hotfix-checkpoint-b5324f8` |
| Acceptance claimed | **No** |
| Merged to main | **No** |
| Pilot started | **No** — paused, Day 0 |
| Phase 8 started | **No** |

---

## 1. Pilot record

**Classification: BLOCKING OWNER NAVIGATION DEFECT — Day 0 pilot defect.**

Reported: at `http://127.0.0.1:8780/`, the Overview next-action CTA **“Go to Analysis &
Decisions”** did not navigate. Instead the owner saw an unusable confirmation dialog — title
“Confirm action”, empty body, a Cancel button, an **enabled** “Confirm & run”, no confirmation
phrase and no action details.

The 14-day pilot **has not started successfully** and remains paused.

---

## 2. Root cause

The reproduction (section 3) shows the defect is **not** in the navigation classification, and
**not** in the action pipeline. Both were already correct. The cause is a CSS cascade fault:

`production/phase7_unified_owner_console_static/styles.css:233` (baseline `b5324f8`) declared

```css
#modal-backdrop { position: fixed; inset: 0; background: rgba(0,0,0,.45); display: flex;
  align-items: center; justify-content: center; z-index: 80; padding: 1rem; }
```

`app.js` hides the confirmation dialog **only** by setting `element.hidden = true`. Hiding via the
`hidden` attribute relies on the user-agent stylesheet rule `[hidden] { display: none }`. That rule
lives in the **user-agent origin**, and a plain author declaration outranks any user-agent
declaration regardless of specificity. `#modal-backdrop { display: flex }` therefore **overrode**
the only mechanism the console had for hiding the dialog.

Consequences, all confirmed in real Edge and real Chrome:

1. `#modal-backdrop` carried `hidden` (attribute **and** property both `true` — correctly set by
   `app.js`) while computing to `display: flex`, `visibility: visible`, and occupying a
   **1585 × 1000** rectangle.
2. Because it is `position: fixed; inset: 0; z-index: 80`, the empty dialog covered the whole
   viewport **from first paint** — before any click.
3. It intercepted every pointer event. Hit-testing every control on Overview: **13 of 13
   unreachable**, all resolving to `DIV#modal-backdrop`.
4. `app.js:1406` (baseline `b5324f8` line numbers throughout this section) deliberately calls `e.preventDefault()` for a click whose target *is* the backdrop
   (so an accidental click cannot discard a single-use token). With the backdrop over the CTA, that
   handler consumed the owner’s click. The anchor never activated: no hash change, no navigation.
5. `#modal-execute` in `index.html` had no `disabled` attribute, so the pristine dialog presented an
   **enabled** “Confirm & run” over an empty body.

So the owner’s “the CTA opens a modal” is the visible symptom of a modal that was **always** on
screen and never went away; the click itself did nothing at all.

**Second instance of the same root cause.** `.btn { display: inline-flex }` (styles.css) overrode
`hidden` on the dialog buttons too. `openModalBlocked()` sets `exec.hidden = true` to remove Confirm
on a blocked preparation, and `finishExecute()` sets it to hide a consumed token. Neither took
visual effect. Confirm stayed **visible** in both states. It was still `disabled`, so nothing could
be executed — but the owner-facing contract (“no Confirm on a block”) was not being delivered.

### Why every prior gate missed it

| Gate | Why it could not catch this |
|---|---|
| `tests/phase7_13_modal_dom_harness.js` | Dependency-free DOM shim. **No CSS engine at all.** |
| `tests/phase7_14_console_dom_harness.js` | Same shim, same blind spot. |
| `tests/phase7_14_browser_qa.js` | Real browser, but asserted `backdrop.hidden` — which was always, correctly, `true`. It never read `getComputedStyle()` and never hit-tested a control. |

The defect lived exactly in the gap between “the property is set” and “the browser hides it”.

---

## 3. Reproduction, before any edit

Driven by a Chrome DevTools Protocol harness against the **real** launcher-started console
(`Start-AMZ-Toolkit` → `production.phase7_owner_launcher … start`, pid 15596, port 8780), in real
Microsoft Edge and real Google Chrome.

| Evidence item | Recorded value |
|---|---|
| 1. Clicked element | Overview next-action CTA, text `Go to Analysis & Decisions` |
| 2. Tag | `A` |
| 3. Id | *(none)* |
| 4. Classes | `btn primary na-cta` |
| 5. `data-action` | **absent** |
| 6. `data-page` | **absent** |
| 7. `data-destination` | **absent** |
| 8. `href` / hash | `#analysis` (full attribute set: `class`, `href`, `data-act="nav:analysis"`) |
| 9. Listeners | **none on the CTA.** No delegated document/body click handler exists in `app.js`. The click was consumed by the `#modal-backdrop` click listener (`app.js:1406`) |
| 10. `/api/v1/actions/prepare` called | **No — zero calls** |
| 11. Network after click | **zero requests of any kind** |
| 12. Console errors | **none**; zero uncaught exceptions |
| 13. Modal DOM after click | byte-for-byte identical to before the click: title `Confirm action`, `modal-desc` empty (0 children, empty text), `modal-confirm-wrap` hidden, no phrase |
| 14. `execute.disabled` | **`false`** (enabled) |
| 15. Hash before → after | `#overview` → `#overview` (unchanged) |
| 16. Served `app.js` SHA-256 | `97ed2180c47f99114e1b469cca274f701542fe7bab29371da83b7ab9b3df5f99` |
| 17. Local `app.js` SHA-256 | `97ed2180c47f99114e1b469cca274f701542fe7bab29371da83b7ab9b3df5f99` |
| 18. Persistence | Reproduced identically after Stop + restart of the launcher, with `Network.setCacheDisabled`, and after a cache-bypassing reload |

Additional decisive readings:

* `getComputedStyle(#modal-backdrop).display` = `flex` while `hidden` was `true`.
* `elementFromPoint(CTA centre)` = `DIV#modal-backdrop`.
* Hit test: 13 / 13 Overview controls unreachable, all blocked by the backdrop.
* `Cache-Control: no-cache` on the served asset; `index.html`, `app.js` and `styles.css` served
  bytes were **identical** to local bytes in every run.

### Cache / stale server: ruled out, with proof

**Neither cache nor a stale server contributed.** All three served assets hashed identically to the
working-tree files in every run, in both browsers, with cache disabled, after a hard reload and
after a full launcher Stop/restart. The defect was in the committed bytes.

### Where the brief’s hypotheses stood

Every listed candidate was checked and **excluded**: the CTA does not share a class with
state-changing buttons (`na-cta` vs `action`); there is no delegated handler with a broad selector;
`data-action` is absent; destination controls are not routed to `startAction()`; no
`preventDefault()` runs before classification in the navigation path; `startAction()` was never
reached; no static asset mismatch. The one accurate item on the list was *“modal state is opened
before prepare validation”* — not as a code path, but structurally: the modal was **never closed**,
and `index.html` shipped Confirm enabled.

---

## 4. The fix

Three front-end files. No backend authority touched — independent reproduction proved the backend
was never involved (zero requests).

### 4.1 `styles.css` (+10 / −0) — the root-cause fix

```css
[hidden] { display: none !important; }
```

Added in the base layer with a comment recording why. This makes `hidden` authoritative for **every**
element, so nothing the code hides can be seen, focused or clicked — and no future `display` rule can
reintroduce the same class of defect. `#modal-backdrop { display: flex }` is retained unchanged and
still lays the dialog out when it is genuinely open.

This single declaration fixes both instances: the viewport-blocking backdrop and the
never-actually-hidden Confirm button.

### 4.2 `index.html` (+4 / −1) — fail-closed served markup

`#modal-execute` now ships with `disabled`. The un-prepared dialog cannot present a runnable Confirm
**before any script runs**.

### 4.3 `app.js` (+106 / −11) — classification + fail-closed modal

1. **Explicit control classification.** `CONTROL_KINDS` / `controlKind()` / `isNavigationAct()`.
   Every clickable control declares exactly one kind in `data-act`: `nav` (navigation), `action`
   (state-changing), `copy`, `download`, `disabled`, `modal`, `table`, `toggle`, `disclose`.
   Navigation controls are plain anchors with a hash `href` and **no click listener**, so they
   cannot reach the prepare/execute pipeline even by accident.
2. **`startAction()` refuses navigation** — `isNavigationAct(action) || viewById(action)` returns a
   blocked dialog *before* the endpoint is contacted. (None of the 15 allowlisted canonical actions
   collides with any of the 11 view ids, verified against `UC.ACTIONS`.)
3. **Preparation completeness validation.** `REQUIRED_PREPARATION_FIELDS` +
   `missingPreparationFields()` validate `action_token`, `canonical_action`, `readiness`,
   `expected_authority`, `expected_effect`, a non-empty bounded `target_ids`, an internally
   consistent `requires_confirmation` / `confirmation_phrase` pair, and a positive finite
   `expires_in_seconds`. Anything missing, non-200 or blocked, and every malformed shape except the
   two `target_ids` type-confusion cases in Known limitations item 7, routes to
   `openModalBlocked()`. **No** input reaches a confirmable dialog. Validated against the real server contract
   (`UC.prepare_action`) by `test_316`, so it can never be stricter than what the authority returns.
   *On the owner-facing title:* the accepted server contract does not carry one — the dialog title is
   derived client-side from the triggering control’s label, falling back to
   `humanize(canonical_action)`. Requiring `canonical_action` therefore guarantees a title exists;
   harness checks `149` and `173` assert the title and canonical line are non-empty even on a refused
   preparation. No new backend field was invented for this.
4. **`resetModal()` fails closed** — Confirm is `disabled` in the reset state. `openModal()` enables
   it only after validation passes, and only for an action that requires no phrase.
5. **`openModalBlocked()`** clears the token *and* the phrase, names the missing details (bounded to
   the fixed required-field set, max 12), and states “Nothing was changed and nothing was sent.”
6. **`openBackdrop()` structural guard.** The dialog cannot be revealed with an empty body: if
   `modal-desc` is empty, the token is dropped, Confirm is removed and disabled, and a bounded
   owner-facing explanation is inserted — *before* the backdrop is un-hidden. A blank modal with a
   runnable Confirm is structurally impossible whatever a future caller does.

### 4.4 Not weakened

Session protection, CSRF, the action allowlist, preparation-token validation, exact-phrase gating
(no `trim` / `toLowerCase` / `normalize` — asserted by `test_321`), single-use execution, the
duplicate-execution lock, the Phase 7.12 live-send double gate, the audit chain, CSP, Host
validation, loopback-only policy, Amazon boundaries and the zero Seller-Central counters are all
untouched. Every change is strictly *more* closed than the accepted baseline.

### 4.5 Not added

No `innerHTML`, `outerHTML`, `eval`, `Function` constructor, `insertAdjacentHTML`,
`document.write`, external library, CDN, external browser request, browser storage, arbitrary route,
arbitrary command or arbitrary URL. Asserted by `test_323`, `test_324`, `test_325`.

---

## 5. Files

**Modified (6).** No backend authority, no launcher script, no doc, no `core/`.

| File | + | − |
|---|---|---|
| `production/phase7_unified_owner_console_static/app.js` | 106 | 11 |
| `production/phase7_unified_owner_console_static/index.html` | 4 | 1 |
| `production/phase7_unified_owner_console_static/styles.css` | 10 | 0 |
| `tests/phase7_14_console_dom_harness.js` | 396 | 8 |
| `tests/phase7_14_browser_qa.js` | 252 | 1 |
| `tests/test_phase7_14_owner_usability_pilot_readiness.py` | 341 | 2 |

**Created (2):** `SESSION7_14-NEXT-ACTION-NAVIGATION-HOTFIX-REPORT.md`,
`SESSION7_14-NEXT-ACTION-NAVIGATION-HOTFIX-PROOF.json`.

---

## 6. Regression tests

**+92 DOM-harness checks** (103 → 195), **+26 Python tests** (439 → 465), **+25 browser-QA check
sites** (50 → 75; 70 checks actually execute per browser — some sites are inside conditionals or
loops). The DOM-harness checks fire real events through the real listener graph and observe the real
`fetch` calls; they are not source-string searches.

| # | Requirement | Proof |
|---|---|---|
| 1 | CTA classified as navigation | harness `109` |
| 2 | Clicking it navigates to Analysis & Decisions | browser `cta_navigates_to_analysis`, `cta_destination_content_visible`; harness `118` |
| 3 | Never calls `/actions/prepare` | harness `114`, `128`, `131`; browser `cta_issues_zero_prepare_requests` |
| 4 | Never opens the modal | harness `116`, `123`; browser `cta_opens_no_modal` |
| 5 | Hash / page state updates | browser `cta_navigates_to_analysis`; harness `118` |
| 6 | Active sidebar state updates | harness `119`; browser `cta_updates_active_nav` |
| 7 | Breadcrumb updates | harness `120`; browser `cta_updates_breadcrumb` |
| 8 | Destination survives refresh | browser `destination_survives_normal_refresh`, `…_hard_refresh`; harness `122` |
| 9 | Every Overview control has exactly one classification | harness `104`, `105`, `106` |
| 10 | Every sidebar control has exactly one classification | harness `107` |
| 11 | Navigation controls cannot enter `startAction()` | harness `112`, `114`, `115`, `128`–`131`; `test_313` |
| 12 | Action controls cannot fall through to navigation | harness `124`–`127` |
| 13 | Missing `data-action` cannot create an executable modal | harness `148`, `149` |
| 14 | Unknown `data-action` cannot create an executable modal | harness `146`; browser `unknown_action_refused_by_server` |
| 15 | Malformed prepare cannot create an executable modal | harness `141`–`144` |
| 16 | Incomplete prepare cannot create an executable modal | harness `132`–`140` |
| 17 | Failed prepare shows a persistent owner-facing error | harness `150`–`155` |
| 18 | BLOCKED prepare shows a persistent explanation | harness `146`, `151`–`153` |
| 19 | Empty modal body is impossible | harness `157`–`160`; `test_319` |
| 20 | Confirm disabled before valid preparation | harness `60b`, `156`, `161`; `test_317`, `test_318`; browser `confirm_starts_disabled` |
| 21 | Exact phrase gating intact | harness `169`; browser `exact_phrase_enables_confirm`; `test_321` |
| 22 | Wrong phrase remains disabled | harness `165`, `166`; browser `wrong_phrase_keeps_confirm_disabled` |
| 23 | Trailing-space phrase remains disabled | harness `167`, `168`; browser `trailing_space_phrase_keeps_confirm_disabled`, `leading_space…` |
| 24 | Successful actions still show complete modal content | harness `170`–`175`; browser `prepared_modal_states_authority_target_effect` |
| 25 | Export still shows a visible result and safe download | harness `186`–`191`; browser `export_download_ok` |
| 26 | Phase 7.12 double gate intact | Phase 7.12 suite 234/234 |
| 27 | Phase 7.13 modal DOM harness green | `TestActionModalDom` OK |
| 28 | Phase 7.13 focused suite green | 269/269 OK |
| 29 | Phase 7.14 next-action tests green | 465 ran, 1 known-stale failure |
| 30 | Edge real-browser click navigates | Edge 70/70 |
| 31 | Chrome real-browser click navigates | Chrome 70/70 |
| 32 | No console errors | `no_browser_console_error`, `no_uncaught_exception` |
| 33 | All browser requests loopback / same-origin | `all_requests_same_origin_loopback`, 0 off-origin |
| 34 | No dead buttons on any owner page | harness `192`, `193` across all 11 pages |

### 6.1 Root-cause coverage, offline

`TestHiddenActuallyHides` computes the **real CSS cascade** — author `!important` > author normal >
user-agent normal — for every element `app.js` hides, and asserts each computes to `display: none`.
`test_303` deliberately strips the guard from the parsed rules and asserts the backdrop returns to
`flex` and Confirm to `inline-flex`, so the test cannot silently become decorative.

### 6.2 Mutation testing

Eight mutations applied in place, one at a time, originals restored from a byte-verified backup
(sources confirmed byte-identical afterwards). Control run passes; **all eight caught; zero
survivors.**

| Mutation | Caught by |
|---|---|
| M1 CTA routed into `startAction()` | `109`, `110`, `111`, `112`, `12`, `23`, `test_311` |
| M2 `[hidden]` guard removed | `test_302`, `test_304`, `test_308` |
| M3 guard weakened to non-`!important` | `test_302`, `test_304`, `test_308` |
| M4 prepare validation removed | `133`–`140`, `test_314` |
| M5 `resetModal` re-enables Confirm | `156`, `test_318` |
| M6 blank-modal guard removed | `157`, `158`, `160`, `test_319` |
| M7 navigation refusal removed | `128`–`131`, `test_313` |
| M8 served Confirm re-enabled | `test_317` |

M1, M4, M5, M6 and M7 are caught by **behavioural** checks that dispatch real events and inspect
real `fetch` traffic, not by source-string assertions.

### 6.3 Browser-level defect-catching proof

`styles.css` and `index.html` were reverted in place to the accepted-baseline blobs, the real console
restarted so it served those bytes, and both harnesses run in real Edge. Both files were then restored
and verified byte-identical to the hotfix version.

| Harness | Against the accepted-baseline (defective) console |
|---|---|
| **Accepted Phase 7.14 browser QA** (`b5324f8`) | **44 / 44 PASS** |
| **Rebuilt browser QA** (this hotfix) | **57 / 70 — 13 failures** |

The 13 failures are exactly `confirm_starts_disabled`, `hidden_modal_computes_to_display_none`,
`hidden_modal_occupies_no_space`, `page_centre_is_not_the_modal`, `every_control_is_clickable`,
`cta_receives_the_owner_click`, `cta_navigates_to_analysis`, `cta_destination_content_visible`,
`cta_updates_active_nav`, `cta_updates_breadcrumb`, `cta_opens_no_modal`,
`destination_survives_normal_refresh`, `destination_survives_hard_refresh`.

This is the clearest statement of why the defect reached a pilot: **the accepted gate was fully green
on the broken console.** It asserted `backdrop.hidden` — always, correctly, `true` — and never the
computed style, the hit-testability of a control, or the outcome of a real click.

---

## 6.4 Test gates

Interpreter: **CPython 3.12.10** — the project baseline, matching the committed `__pycache__`.

| # | Gate | Result |
|---|---|---|
| 1 | New focused navigation / modal tests | 26 ran, **OK** |
| 2 | Phase 7.14 focused suite | 465 ran, 1 failure (`test_199e`, known stale) — baseline was 439/1 |
| 3 | Phase 7.14 DOM harness | **195 checks, 0 failed** (baseline 103) |
| 4 | Phase 7.14 browser QA | Edge **70/70**, Chrome **70/70** |
| 5 | Phase 7.13 focused suite | 269 ran, **OK** |
| 6 | Phase 7.13 modal DOM harness | 3 ran, **OK** (≥40 modal checks green) |
| 7 | Phase 7.12 regression | 234 ran, **OK** |
| 8 | Connectivity scanners | 53 ran, **OK** |
| 9 | Network-policy scanners | 5 ran, **OK** |
| 10 | Prohibited-integration / Amazon boundary | 26 ran, **OK** |
| 10b | Connected-phase network suites (7.9 / 7.10 / 7.11) | 519 ran, **OK** (2 skipped) |
| 11 | Full in-place suite | **4630 ran**, 1 failure + 1 error, 4 skipped, 950s |
| 12 | `compileall production core tests` | exit 0 |
| 13–15 | Fresh-worktree differential | see below |

### Gate 11 — the two non-passing nodes, both accounted for

Accepted record from the Stop-message hotfix proof: 4604 ran, 1 failure, 1 error, 4 skipped. This
run: 4630 ran (+26), **the same 1 failure, the same 1 error, the same 4 skipped**.

1. `test_199e_no_acceptance_tag_yet` — the known-stale pre-acceptance self-guard (see Known limitations, item 2).
2. `test_phase7_13_unified_owner_console.TestBody.test_52_request_size_bounded` —
   `ConnectionAbortedError: [WinError 10053]`. `git diff b5324f8 HEAD` is **empty** for both that test
   file and `production/phase7_unified_owner_console.py`; the test passes **10/10** in isolation and
   passed in **both** fresh-worktree full runs. Same signature the Phase 7.14 audit and the
   Stop-message hotfix proof recorded as a Windows loopback environment flake.

### Gates 13–15 — fresh-worktree differential

Two fresh worktrees, run **concurrently with each other and nothing else**, full discovery, same
interpreter.

| | Baseline `b5324f8` | Implementation `af443b5` |
|---|---|---|
| Ran | 4602 | **4628** (+26) |
| Failures | 2 | **2** |
| Errors | 14 | **14** |
| Skipped | 329 | **329** |
| Seconds | 540.1 | 540.1 |

**Non-passing node sets are byte-identical (16 nodes each), and none of the 26 new hotfix tests
appears in either set.**

The counts are **nonzero on both sides**, and that is expected, not green: `runs/T2` is gitignored,
so 14 errors plus one failure are T2-data-dependent tests that cannot find their inputs in a bare
checkout; the 16th node is the known-stale `test_199e`. **The differential is judged relatively, not
absolutely.**

Result: `FRESH_WORKTREE_FULL_SUITE_BASELINE_EQUIVALENT_NONZERO`.

### Source immutability

`git diff --stat b5324f8 HEAD` reports exactly 6 files, 3 of them tests. Empty diffs for
`production/*.py`, `core/`, `docs/`, the launcher `.ps1`/`.bat` wrappers, and
`production/phase7_unified_owner_console.py` specifically. No accepted authority changed.

---

## 7. Real-browser QA

Both browsers, headless-new, against the launcher-started console. **Edge 70/70, Chrome 70/70,
0 console errors, 0 uncaught exceptions, 0 off-origin requests.**

**A — Overview next-action CTA.** No modal (`hidden` true, computed `display: none`);
Analysis & Decisions content visible (`h1 = "Analysis & Decisions"`); URL `#analysis`; active nav
`#analysis`; breadcrumb `Console / Operations / Analysis & Decisions`; **zero** prepare requests.
The click is dispatched as a real mouse event at the element’s real coordinates, after
`scrollIntoView` and after hit-testing confirms the click reaches the CTA.

**B — Real state-changing action.** `create-backup-snapshot`: complete modal (Accepted authority,
Target(s), Expected effect, Confirmation window, canonical action, readiness); Confirm **disabled**
initially; phrase typed with real key events — wrong / case-folded / trailing-space / leading-space
all keep Confirm disabled, exact phrase enables it; cancelled safely with Escape.

**C — Failed preparation.** An action outside the allowlist is refused by the server with no token
and no phrase. This request is issued from Node with a real session + CSRF token rather than from
inside the page: an in-page raw `fetch` is (correctly) rejected by CSRF, and the resulting 403
appears as a browser console error that would mask real console failures. The front end’s handling
of failed / blocked / malformed / incomplete preparations is proven by the committed DOM harness
(checks `132`–`155`), which drives the real `app.js` code path against those exact responses.

**D — Refresh and cache.** Normal refresh and cache-bypassing hard refresh both keep `#analysis`
and Analysis & Decisions, with the modal `display: none`. Served asset bytes identical to local in
every run.

---

## 8. Known limitations

1. **The offline root-cause test is a bounded CSS evaluator, not a browser engine.** `_css_rules` /
   `_matches` / `_winning_display` implement only what this flat, hand-written stylesheet uses (tag,
   `#id`, `.class`, `[attr]`, descendant, comma lists) and deliberately skip `@media` / `@print`
   bodies, on the grounds that a conditional rule may never be a hide mechanism. It is not a general
   CSS implementation. The authoritative check remains the real-browser computed-style assertion,
   which now runs in both Edge and Chrome.
2. <a id="stale"></a>**`test_199e_no_acceptance_tag_yet` is permanently stale** — it asserts no Phase 7.14 acceptance
   tag exists, and the independent audit legitimately created one. Per instruction it was **not**
   removed, skipped or weakened here. It fails identically on the untouched baseline, so it is
   classified **baseline-equivalent**. It should be retired or re-scoped in a separate change.
3. **Python 3.14 is not the project baseline interpreter.** Under 3.14,
   `test_040b_workspace_root_escape_refused` fails; under 3.12 (the project baseline, matching the
   committed `__pycache__`) it passes. Not caused by this hotfix — it touches no launcher code — and
   reproduced on the untouched baseline worktree. All recorded gate results use 3.12.
4. **`start --no-browser` still prints “Your browser should now be open on the console.”** Observed
   while restarting the console during this hotfix. Pre-existing, already recorded as backlog in the
   Stop-message hotfix proof; out of scope here.
5. **The audit chain still does not auto-detect clean tail-truncation** — inherent, disclosed at
   Phase 7.13 acceptance, unchanged.
6. Real-browser QA is a manually invoked harness: it needs a real browser binary and a running
   console, so it is not part of `unittest discover`.
7. **`missingPreparationFields()` does not type-check `target_ids` before calling `.join()`.**
   Found by the independent acceptance audit. If a prepare response returned `target_ids` as a
   *string* or an array-*like* object, `prep.target_ids.join is not a function` is thrown
   (`app.js:1251`) inside the `.then` fulfilment handler; the result is an uncaught promise
   rejection and **no dialog at all**, rather than the intended `openModalBlocked()` explanation.
   Confirm stays disabled, no token is held and **zero** execution requests are issued, so nothing
   unsafe follows — but the owner gets no feedback for that one shape. It is **unreachable through
   the accepted authority**: every return path of `_resolve_target()` in
   `production/phase7_unified_owner_console.py` returns a list, so `target_ids` is always a JSON
   array. It is also strictly safer than the accepted baseline, which for the same eight
   malformed-type inputs presented an **enabled** Confirm over an empty body in all eight cases.
   Fix separately: type-check `target_ids` before `.join()`.

---

## 9. Status

* **Acceptance: NOT claimed.** No acceptance tag created. No accepted tag moved.
* **Merge: NOT performed.** `main` remains at `b5324f8`.
* **Pilot: PAUSED at Day 0.** The 14-day pilot has **not** started successfully.
* **Phase 8: NOT started.**

**Exact next action:** independent hotfix acceptance audit of this branch. Suggested focus — that
the `[hidden]` guard is the whole root-cause fix and cannot be outranked; that the navigation
contract is enforced behaviourally rather than by string matching; that the preparation validator is
never stricter than `UC.prepare_action`; and that the two accounted-for non-passing nodes really are
baseline-equivalent. After acceptance: merge to `main`, restart the launcher from accepted `main`,
then restart the pilot at Day 0.
