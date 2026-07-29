# Session 7.14 — Next-Action Navigation Hotfix — Independent Acceptance Audit

**Decision:** `PHASE7_14_NEXT_ACTION_NAVIGATION_HOTFIX_ACCEPTED_WITH_DOCUMENTATION_FIX`

| | |
|---|---|
| Branch audited | `hotfix-phase7-14-next-action-navigation` |
| Feature HEAD (proof commit) | `5fcbf6fa3d7c2b2d04c740964f6e55f4ec99ed0a` |
| Implementation commit | `af443b55c2a218204ba3d99d9b4734bc3037ed21` |
| Accepted, merged baseline | `b5324f83f326664660bb9084f1696aefb28c151c` |
| Checkpoint tag | `phase7-14-next-action-navigation-hotfix-checkpoint-b5324f8` |
| Merged to `main` | **No** — `main` and `origin/main` remain `b5324f8` |
| Pilot | **Paused at Day 0** — not resumed by this audit |
| Phase 8 | **Not started** |
| Auditor posture | Nothing in the implementation report, proof JSON, test totals, browser-QA claims, root-cause claims or fresh-worktree claims was taken on trust. Every material claim was reproduced from repository bytes and new, independently written fixtures. |

**Audit tooling.** The implementation's own harnesses were *also* run, but every load-bearing
conclusion below rests on an independently written Chrome DevTools Protocol driver
(`audit_cdp.js`, `audit_rootcause.js`, `audit_inventory.js`, `audit_failclosed.js`,
`audit_exec.js`, `audit_a11y.js`, `audit_edge_types.js`) authored for this audit and run against
real Microsoft Edge and real Google Chrome. No production code was modified for acceptance.

---

## A. Git provenance

**1. Branch.** `git rev-parse --abbrev-ref HEAD` = `hotfix-phase7-14-next-action-navigation`. **PASS**

**2. Working tree clean.** `git status --porcelain` empty before and after the audit. The audit ran
its suites in temporary worktrees and its browsers against copied workspaces; `git clean` was never
run in the primary workspace. **PASS**

**3. Local HEAD.** `5fcbf6fa3d7c2b2d04c740964f6e55f4ec99ed0a`, matching the expected proof commit
exactly. **PASS**

**4. Remote hotfix HEAD.** `origin/hotfix-phase7-14-next-action-navigation` =
`5fcbf6fa3d7c2b2d04c740964f6e55f4ec99ed0a` — identical to local. **PASS**

**5. Implementation commit.** `git rev-parse af443b5` =
`af443b55c2a218204ba3d99d9b4734bc3037ed21`, the parent of HEAD, titled
`fix(phase7.14): restore next-action navigation contract`. **PASS**

**6. Descent from the accepted baseline.** `git merge-base --is-ancestor b5324f8 HEAD` returns
true. The branch is exactly two commits ahead of `b5324f8` (implementation, then proof) with no
merge commits. **PASS**

**7. Checkpoint tag.** `phase7-14-next-action-navigation-hotfix-checkpoint-b5324f8` resolves to
`b5324f83f326664660bb9084f1696aefb28c151c` — exactly the baseline. It is a lightweight tag
(`git cat-file -t` = `commit`), consistent with prior checkpoint tags in this repository. **PASS**

**8. `main` and `origin/main` unmoved.** Both = `b5324f83f326664660bb9084f1696aefb28c151c`. The
hotfix is **not** merged. **PASS**

**9. No navigation-hotfix acceptance tag pre-existed.** `git tag -l 'phase7-14*'` returned five
tags, none of them an acceptance tag for this hotfix; the only navigation-hotfix tag was the
checkpoint. The implementation correctly did not self-accept. **PASS**

**10. Accepted tags intact.** `phase7-14-owner-usability-pilot-readiness-accepted-b3e357e`
(annotated, object `a629cd70…`, commit `b3e357e2…`) and
`phase7-14-stop-owner-message-hotfix-accepted-b5324f8` (annotated, object `cecb5771…`, commit
`b5324f83…`) are unchanged and unmoved. The proof JSON records the annotated **tag-object** IDs,
which is correct — an initial mismatch I flagged was my own dereferencing error, not a
documentation defect. **PASS**

**11. No pilot runtime files committed.** `git ls-files` matches nothing under `runs/`, no `.pid`,
no session or console-runtime artefacts. `.gitignore:5` ignores `runs/`. **PASS**

**12. No Phase 8 work.** `git ls-files` matches no `phase8`/`phase_8` path; no Phase 8 branch or tag
exists. **PASS**

---

## B. Diff scope

**13. Changed-file set is exactly as expected.** `git diff --name-status b5324f8 HEAD`:

```
A  SESSION7_14-NEXT-ACTION-NAVIGATION-HOTFIX-PROOF.json
A  SESSION7_14-NEXT-ACTION-NAVIGATION-HOTFIX-REPORT.md
M  production/phase7_unified_owner_console_static/app.js
M  production/phase7_unified_owner_console_static/index.html
M  production/phase7_unified_owner_console_static/styles.css
M  tests/phase7_14_browser_qa.js
M  tests/phase7_14_console_dom_harness.js
M  tests/test_phase7_14_owner_usability_pilot_readiness.py
```

Eight paths, no more, no fewer. **PASS**

**14. Line counts match the report.** `--numstat`: `app.js` 106/11, `index.html` 4/1, `styles.css`
10/0, `phase7_14_browser_qa.js` 252/1, `phase7_14_console_dom_harness.js` 396/8,
`test_phase7_14_owner_usability_pilot_readiness.py` 341/2. **PASS**

**15. No backend authority changed.** SHA-256 of `production/phase7_unified_owner_console.py`,
`production/phase7_owner_launcher.py`, `production/phase7_owner_next_action.py`,
`production/phase7_owner_notification_delivery.py`, `core/network_policy.py`,
`core/runtime_policy.py` are **byte-identical** between `b5324f8` and `af443b5`. **PASS**

**16. No launcher, no `core/`, no docs, no other production Python changed.** Filtering the diff to
exclude the two new documents, the static directory and `tests/` leaves an empty set. **PASS**

**17. `styles.css` change is genuinely required and minimal.** The entire change is one declaration
plus a 9-line comment: `[hidden] { display: none !important; }`. No colour, spacing, layout,
typography or component rule was touched. Finding 24 proves this single declaration is exactly what
repairs the defect; finding 33 proves it is load-bearing. This is not an unrelated redesign. **PASS**

**18. `index.html` change is minimal and fail-closed.** One attribute (`disabled` on
`#modal-execute`) plus a 3-line comment. **PASS**

**19. No undocumented production change.** Every hunk in the three static files is accounted for by
sections 4.1–4.3 of the implementation report. I read every changed byte. **PASS**

**20. Test changes are additive.** Only one line was removed from the Python test file that carries
assertion force: `assertGreaterEqual(int(total[0].split()[1]), 100)` became `… 190` — a
**strengthening**, not a weakening. No test method, class or assertion was deleted; the remaining
removed lines are internal refactors of the DOM harness. **PASS**

---

## C. Root cause

**21. The reported root cause is a CSS cascade fault, and it is correct.** At `b5324f8`,
`styles.css:233` declares `#modal-backdrop { … display: flex; … z-index: 80; position: fixed;
inset: 0 }` as a normal **author** declaration, and the baseline stylesheet contains **no**
`[hidden]` rule at all (verified against the git blob and against the bytes the server actually
served). `app.js` hides the dialog *only* via `element.hidden = true` (15 occurrences; zero uses of
`style.display` or `classList`). An author-origin normal declaration outranks the user-agent
`[hidden] { display: none }` rule regardless of specificity, so the confirmation dialog was never
hidden. **CONFIRMED**

**22. Verified in the browser, not merely by reading CSS.** In real Edge against a clean
`b5324f8` worktree: `#modal-backdrop` had the `hidden` attribute (`true`) while
`getComputedStyle().display` was `flex`, `pointer-events: auto`, occupying a full-viewport
`1327 × 629` rectangle at `z-index: 80`. Chrome: identical, `1335 × 617`. **CONFIRMED**

**23. Rule attribution, dumped from the live cascade.** On baseline the only `display` rule matching
`#modal-backdrop` was `{selector: "#modal-backdrop", display: "flex", priority: ""}` from
`/styles.css`, and the set of author rules whose selector contains `[hidden]` was **empty**. On the
implementation both rules match and `[hidden]` wins with `priority: "important"`. **CONFIRMED**

> *Audit self-correction:* an early probe of mine reported an author `[hidden]` rule on baseline.
> That was an artefact — a regex written inside a JS template literal collapsed `\[hidden\]` into a
> character class. The probe was rewritten to dump matching rules verbatim; the corrected result is
> above and agrees with the repository bytes.

**24. The click never reached the CTA — decisive hit-test evidence.**
`document.elementFromPoint()` at the CTA's exact centre returned `DIV#modal-backdrop`
(`isTheCTA: false`) in both browsers. A real CDP-dispatched mouse press/release at those
coordinates landed on `#modal-backdrop`, whose text content begins `"Confirm action"`. **CONFIRMED**

**25. Keyboard activation *did* navigate on the defective baseline.** Pressing Enter on the focused
CTA produced `hash = "#analysis"`, heading `Analysis & Decisions`, updated sidebar and breadcrumb —
on the *unfixed* console. This is the decisive discriminator: routing, classification and the
anchor were all already correct; only pointer hit-testing was blocked by the overlay. **CONFIRMED**

**26. The alternative hypotheses in the audit brief are refuted, individually.**
*Navigation CTA misclassified as an action* — refuted: the CTA markup is **byte-identical** between
baseline and implementation (`<a class="btn primary na-cta" href="#analysis"
data-act="nav:analysis">`), and `nextActionPanel()` is unchanged by the hotfix.
*Overly broad delegated handling* / *action handler before classification* — refuted: no delegated
document/body click handler exists, and zero `/api/v1/actions/prepare` requests were issued.
*Missing/incorrect data destination* — refuted: `href="#analysis"`, `data-act="nav:analysis"`,
destination `{page: "analysis", page_label: "Analysis & Decisions", type: "existing_console_page"}`.
*Malformed preparation opening the modal* — refuted: the modal was never opened by any code path;
it had never been hidden. **CONFIRMED**

**27. The proximate consumer of the click is identified.** Baseline `app.js:1405-1406` registers
`mousedown` and `click` handlers on the backdrop that call `preventDefault()` when
`e.target === backdrop` — a deliberate guard so a stray click cannot discard a single-use token.
With the backdrop covering the CTA, that guard consumed the owner's click. **CONFIRMED**

**28. Second instance of the same cause, confirmed.** `.btn { display: inline-flex }` likewise
outranked `hidden` on the dialog buttons, so `openModalBlocked()`'s `exec.hidden = true` had no
visual effect at baseline. Reproduced: on baseline the Confirm button remained visible in states
where the code had hidden it. **CONFIRMED**

**29. The blast radius is far larger than the single CTA.** Measured with one identical
scroll-normalised inventory script against both consoles across all 11 owner pages:

| | Baseline `b5324f8` | Implementation `af443b5` |
|---|---|---|
| Controls found | 259 | 263 |
| Visible | 259 | 241 |
| Pointer-reachable | **22** | **230** |
| **Unreachable** | **226** | **0** |

Blockers on baseline: `DIV#modal-backdrop` (211), `DIV#modal` (12), `H2#modal-title` (2), other (1).
The accepted baseline console was almost entirely pointer-dead; "Go to Analysis & Decisions" was
simply the control the owner happened to click first. **CONFIRMED**

**30. Cache and stale assets are ruled out with byte-level evidence — not asserted.** For the
baseline console, SHA-256 of the served `/`, `/app.js` and `/styles.css` equalled the SHA-256 of the
corresponding `b5324f8` git blobs exactly (`app.js` =
`97ed2180c47f99114e1b469cca274f701542fe7bab29371da83b7ab9b3df5f99`). Every browser run used a
freshly created, throw-away profile; the a11y/refresh runs additionally set
`Network.setCacheDisabled = true` and performed `Page.reload{ignoreCache: true}`. The defect
reproduced identically under all of these. **Cache verdict: NOT a contributing factor.** **CONFIRMED**

**31. Root-cause verdict.** The defect is an **author-origin `display` declaration defeating the
`hidden` attribute**, making the confirmation dialog permanently painted and click-swallowing from
first paint. The implementation report's root-cause section is accurate in mechanism, location and
consequence. **CONFIRMED**

---

## D. Navigation contract (implementation)

**32. The CTA is classified only as navigation.** `data-act="nav:analysis"`; `controlKind()` maps
the `nav` head to `navigation`; `startAction()` refuses `isNavigationAct(action) || viewById(action)`
before contacting any endpoint; navigation anchors carry no click listener. **PASS**

**33. The `[hidden]` guard is load-bearing and cannot be outranked.** On the implementation the
backdrop computes to `display: none` with a `0 × 0` rect while retaining `display: flex` in the
matching-rule list — i.e. the guard is what wins, and the dialog still lays out when genuinely
opened (verified in finding 43). **PASS**

**34. Clicking navigates.** Real mouse click → `hash = "#analysis"`, `h1 = "Analysis & Decisions"`.
Edge **and** Chrome. **PASS**

**35. Zero prepare requests.** Across the click and the keyboard paths, requests matching
`actions/prepare` = `[]`. The complete request set for a full session was: `/`, `/styles.css`,
`/app.js`, `/api/v1/session`, `/api/v1/overview`, `/favicon.svg`, `/api/v1/activity`,
`/api/v1/analysis`. **PASS**

**36. No modal opens.** After the click the backdrop remains `display: none`, `hidden` attribute
present. **PASS**

**37. No preparation token is created or retained.** No prepare call is made, so no token exists;
`modalState.token` is `null` and `closeModal()` additionally nulls token, phrase and action.
`executeModal()` returns early when `!modalState.token`. **PASS**

**38. Hash and page state update.** `#analysis`, view heading `Analysis & Decisions`. **PASS**

**39. Active sidebar state updates.** Active nav entry becomes `Analysis & Decisions`. **PASS**

**40. Breadcrumb updates.** `['Console', 'Operations', 'Analysis & Decisions']`. **PASS**

**41. Refresh retains the destination.** Normal reload **and** `Page.reload{ignoreCache:true}` with
the HTTP cache disabled both return to `#analysis` / `Analysis & Decisions`, modal `display: none`,
Confirm disabled. Edge 1366×768 and Chrome 1920×1080. **PASS**

**42. Back and forward are correct.** From `#analysis`, navigating to `#overview` then `history.back()`
returns to `#analysis` with heading, sidebar and breadcrumb restored; `history.forward()` returns to
`#overview` with its own state. **PASS**

**43. Keyboard activation behaves like mouse activation.** Enter on the focused CTA produces the
identical end state (`#analysis`, heading, sidebar, breadcrumb) and zero prepare requests. **PASS**

**44. Accessible name and visible focus.** Accessible name `Go to Analysis & Decisions`;
`tabIndex 0`; reachable by Tab; when focused by keyboard it matches `:focus-visible` and paints
`outline: solid 3px rgb(11, 98, 214)`. **PASS**

**45. Overview control inventory.** All Overview controls are reachable and each carries exactly one
`data-act` kind, except the static skip link (finding 47). **PASS**

**46. Sidebar control inventory.** 11 sidebar routes, each an `<a href="#route">` with
`data-act="nav:<route>"`; all reachable; each resolves to a declared view. **PASS**

**47. Exactly-one classification — with one documented nuance.** Across 11 pages, 263 control
instances resolve to exactly one declared kind: `nav` 136, `copy` 52, `modal` 22, `table` 15,
`toggle` 11, `action` 5, `disclose` 4, `download` 3. **No unknown kinds.** The remaining 11 are the
one static skip link (`<a class="skip-link" href="#content">`) rendered once per page. It carries no
`data-act`, but it is unambiguously **real navigation**: it targets `<main id="content" tabindex="-1">`,
has the accessible name "Skip to main content", is off-screen at `left:-999px` until focused and
reveals itself at `top:8px, left:8px` on focus — the standard pattern. It is not dead, not ambiguous
and not multiply classified, so it satisfies the audit's classification requirement. It does,
however, make the `app.js` comment "Every clickable control declares exactly one kind in `data-act`"
an overstatement. Recorded as a non-blocking observation. **PASS (with observation)**

**48. Dead-button scan.** Zero visible, enabled, pointer-unreachable controls on the implementation
across all 11 pages (0 of 230). No inline `onclick` anywhere. No control lacks an accessible name.
**PASS**

**49. Disabled controls state a visible reason.** e.g. `table:prev` → "You are on the first page.";
`table:next` → "You are on the last page." The only disabled control without an adjacent reason is
`#modal-execute`, which at rest is inside a hidden dialog and therefore never presented to the owner
in that state. **PASS**

---

## E. Modal fail-closed contract

Twenty-two responses were injected for `/api/v1/actions/prepare` via CDP `Fetch` interception, and
`/api/v1/actions/execute` was intercepted throughout so that any execution attempt would be
**recorded and blocked** rather than performed. Each case was driven by a real click on a real
action control (`action:create-backup-snapshot`) after a genuine full-document reload.

**50. Missing canonical action** → blocked dialog, Confirm hidden **and** disabled, 0 execute. **PASS**

**51. Unknown action (HTTP 400)** → blocked dialog with owner-facing reason, 0 execute. **PASS**

**52. Missing title.** The accepted server contract carries no title field; the dialog title is
derived client-side from the triggering control's label with `humanize(canonical_action)` as
fallback. In every one of the 22 cases the modal title was non-empty. **PASS**

**53. Missing readiness** → blocked dialog, Confirm hidden+disabled, 0 execute. **PASS**

**54. Missing authority** (`expected_authority`) → blocked, 0 execute. **PASS**

**55. Missing target** (`target_ids` absent) and **empty target** (`[]`) → blocked, 0 execute. **PASS**

**56. Missing expected effect** → blocked, 0 execute. **PASS**

**57. Missing token** (`action_token` absent, and separately empty-string) → blocked, 0 execute. **PASS**

**58. Malformed JSON** (truncated body) → blocked with owner-facing reason, 0 execute. **PASS**

**59. HTTP failure (500)** and **network failure** (request failed outright) → blocked, 0 execute. **PASS**

**60. BLOCKED response (403 with `SESSION7_13_ACTION_BLOCKED`)** → persistent blocked dialog naming
the reason, 0 execute. **PASS**

**61. Session expiry (401)** → blocked dialog, 0 execute. **PASS**

**62. CSRF rejection (403 `CSRF_REJECTED`)** → blocked dialog, 0 execute. **PASS**

**63. Stale preparation state.** `openModalBlocked()` sets `modalState.token = null` and
`modalState.phrase = null`; `closeModal()` nulls token, phrase and action. Empirically: after
closing a fully valid prepared modal, force-enabling `#modal-execute` in the DOM and clicking it
produced **0** execute requests. The stale token is genuinely cleared, not merely hidden. **PASS**

**64. No blank modal is possible.** In all 22 injected cases `modal-desc` had ≥ 1 child and non-empty
text. `openBackdrop()` additionally fails closed structurally: if `modal-desc` is empty at reveal
time it drops the token, hides and disables Confirm, and inserts a bounded owner-facing explanation
*before* un-hiding the backdrop. **PASS**

**65. Confirm disabled by default.** `index.html` ships `#modal-execute` with `disabled`;
`resetModal()` sets `exec.disabled = true`. Measured at rest on every one of the 11 pages:
`execDisabled = true`. **PASS**

**66. No false success and no execution request.** Total execute attempts across all 22 fail-closed
cases and all 8 type-confusion cases: **0**. **PASS**

**67. Type-confusion robustness gap — non-blocking, and strictly safer than baseline.** Eight
additional malformed-*type* responses were probed. Six behave correctly. Two —
`target_ids` as a **string**, and as an **array-like object** — make
`missingPreparationFields()` throw `TypeError: prep.target_ids.join is not a function`
(`app.js:1251`) inside the `.then` fulfilment handler. The result is an uncaught promise rejection
and **no modal at all**: the owner clicks and nothing visible happens.

*Safety impact: none.* Confirm stays `disabled`, no token is held, and **0** execute requests are
issued. *Reachability: none with the accepted authority* — every return path of
`_resolve_target()` in `production/phase7_unified_owner_console.py` returns a Python `list`, so
`target_ids` is always a JSON array. *Regression: none — the opposite.* The same eight inputs
against the **baseline** console produce `execDisabled = false` with an empty body in **all eight**
cases; the implementation disables Confirm in all eight. This is a robustness gap in new hardening
code, strictly better than what it replaced, and it is the basis of the documentation fix in
section J. **NON-BLOCKING**

---

## F. Phase 7.13 modal regression

**68. Valid action modal contains complete content.** The prepared dialog renders Accepted
authority, Target(s), Expected effect, Network access, Local state changes, Upstream state changes
and Confirmation window (244 characters of structured content), plus the canonical action line and
the readiness badge. **PASS**

**69. Confirm disabled initially on a valid preparation.** `execDisabled = true` at open, with focus
placed on the phrase input. **PASS**

**70. Exact phrase gating is intact and space-sensitive.** With the exact phrase
`BACKUP:snap-…` (true = Confirm disabled): empty `true`, wrong phrase `true`, all-lowercase `true`,
all-uppercase `true`, leading space `true`, trailing space `true`, interior extra space `true`,
**exact `false`**, reverted to wrong `true`, exact again `false`. No `trim`, `toLowerCase` or
`normalize` is applied. **PASS**

**71. Duplicate execution blocked.** Three rapid programmatic Confirm clicks on a no-confirmation
action produced exactly **one** `/api/v1/actions/execute` request; the button switched to
"Working…" and disabled. **PASS**

**72. Failed preparation remains visible.** Blocked dialogs persist with a bounded owner-facing
notice ("This action cannot be prepared right now.", the missing-field list, and "Nothing was
changed and nothing was sent."); Cancel becomes "Close". **PASS**

**73. Focus trap and focus return.** `#modal` has `role="dialog"`, `aria-modal="true"`,
`aria-labelledby="modal-title"`, `aria-describedby="modal-desc"`; focus is inside the dialog while
open; Escape closes it and returns focus to the exact triggering control
(`BUTTON[data-act="action:create-backup-snapshot"]`). **PASS**

**74. Escape and Cancel are safe before execution.** Escape produced **0** execute requests and left
the backdrop hidden. **PASS**

**75. Export result remains visible, with safe relative downloads.** Executing `export-overview`
produced a visible `ok` result ("Completed — SESSION7_13_ACTION_COMPLETED", result id, accepted
authority, the three written paths) and three download links, all same-origin and relative:
`/api/v1/exports/overview?format=json|tsv|md` with plain filenames. Scanning every `<a href>` on
every page found **no** absolute, protocol-relative, parent-traversing, drive-letter or UNC href.
**PASS**

**76. No confirmation token leaks into DOM, logs or exports.** The injected sentinel token never
appeared in `document.documentElement.outerHTML` in any case. On disk, the console workspace written
during the audit contains **zero** occurrences of `action_token`, `confirmation_phrase`, `csrf` or
the sentinel; the only long tokens in the audit log are content hashes (`event_hash`, `param_hash`,
`aggregate_hash`). **PASS**

---

## G. Real Edge and Chrome QA

**77. Edge — navigation CTA.** Overview loaded; real click; no modal (`display: none`); destination
content `Analysis & Decisions`; hash `#analysis`; active sidebar `Analysis & Decisions`; breadcrumb
`Console / Operations / Analysis & Decisions`; **zero** prepare requests. **PASS**

**78. Chrome — navigation CTA.** Identical results. **PASS**

**79. Edge console.** 0 errors, 0 uncaught exceptions across root-cause, inventory, a11y and QA runs.
**PASS**

**80. Chrome console.** 0 errors, 0 uncaught exceptions. **PASS**

**81. Browser network boundary.** Every request in every run went to `http://127.0.0.1:<port>`.
Off-origin requests: **0**. No CDN, font, telemetry or external asset request was observed in either
browser. **PASS**

**82. Asset-hash consistency — three-way.** For the implementation, repo blob = served bytes =
bytes fetched by the page (`crypto.subtle.digest` inside the browser, `cache: "no-store"`):
`app.js bb002ceb…`, `styles.css dbf5b062…`, `index.html b69ff897…`, in **both** browsers. No stale
asset anywhere in the chain. **PASS**

**83. Real state-changing action in-browser.** `create-backup-snapshot` produced a complete modal
with Confirm disabled, correct phrase gating under real key events, and was cancelled safely.
`refresh-overview` and `export-overview` executed exactly once each and rendered visible results.
**PASS**

**84. Failed and BLOCKED preparation in-browser.** Covered exhaustively by section E against real
injected HTTP responses in a real browser — persistent owner-facing failure, Confirm disabled, no
stale token, no execution. **PASS**

**85. Viewport 1366×768.** No horizontal overflow (`scrollWidth 1327` ≤ `innerWidth 1342`); CTA
on-screen and reachable. **PASS**

**86. Viewport 1920×1080.** No horizontal overflow (`scrollWidth 1889` ≤ `innerWidth 1904`);
navigation, refresh retention and modal state all correct. **PASS**

**87. Accessibility.** `lang="en"`; single `<h1>`; `main#content` landmark present; skip link
targets it and reveals on focus; 2 `<nav>` landmarks; 6 `aria-live` regions; 0 `<img>` without
`alt`; 0 `svg.icon` without `aria-hidden`; keyboard-visible focus ring on the CTA. **PASS**

---

## H. Security and boundaries

**88. No new unsafe construct.** Counts of `innerHTML`, `outerHTML`, `eval(`, `new Function`,
`Function(`, `document.write`, `insertAdjacentHTML`, `srcdoc`, `javascript:`, `localStorage`,
`sessionStorage`, `indexedDB`, `document.cookie`, `XMLHttpRequest`, `WebSocket`, `EventSource`,
`importScripts`, `//cdn`, `https://`, `http://` in `app.js` are **identical** between `b5324f8` and
`af443b5` — every one of them zero, with `fetch(` unchanged at 2. `index.html` and `styles.css`
show no delta for `<script`, `onclick`, `onerror`, `onload`, `@import`, `url(`, `integrity`,
`crossorigin`, `font-face` or `cdn`. **PASS**

**89. No external destination, arbitrary URL, route or command added.** The only network-adjacent
added lines are a comment describing a same-origin export link and a comment about the user-agent
`[hidden]` rule. **PASS**

**90. CSP and security headers unchanged.** The served
`Content-Security-Policy: default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'
data:; connect-src 'self'; font-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors
'none'; form-action 'self'` plus `X-Content-Type-Options`, `Referrer-Policy`, `X-Frame-Options` and
`Permissions-Policy` are **byte-identical** between the baseline and implementation servers. **PASS**

**91. Phase 7.12 notification double gate intact.** `production/phase7_owner_notification_delivery.py`
is byte-identical to baseline; `_approval_gate()` and `_live_gate(…, confirm_send=…)` are unchanged;
the Phase 7.12 suite passes 234/234. **PASS**

**92. Amazon boundary unchanged.** No Seller Central connection, seller authentication, SP-API,
Amazon Ads API, automatic Amazon mutation, buyer messaging, review request or CAPTCHA-bypass
construct is added; the changed static files contain none of these terms (the only apparent hit,
"LWA", was the substring in "always"). **PASS**

**93. Seller Central counters all zero.** `/api/v1/overview`, `/next-action`, `/system`, `/activity`,
`/analysis`, `/session` each report all ten counters at 0 —
`seller_central_connections`, `seller_api_calls`, `advertising_api_calls`,
`seller_account_mutations`, `seller_browser_automation_actions`, `seller_bulk_uploads`,
`seller_report_downloads`, `seller_credential_store_count`, `buyer_messages_sent`,
`review_requests_sent`. **PASS**

**94. Prohibited-integration and boundary scanners green.** `test_connectivity_policy`,
`test_connectivity_surface`, `test_network_policy`, `test_amazon_boundary`,
`test_connected_services`, `test_runtime_policy` — **101 tests, OK, exit 0**. **PASS**

---

## I. Test reproduction

All commands run with `C:\Users\Admin\AppData\Local\Programs\Python\Python312\python.exe`
(CPython **3.12.10**) and Node **v24.18.0**.

**95. New navigation/hidden hotfix tests.**
`python -m unittest tests.test_phase7_14_owner_usability_pilot_readiness.TestHiddenActuallyHides
tests.…TestNextActionNavigationContract -v` → **26 ran, OK**, exit 0. **PASS**

**96. The new tests are genuinely defect-specific.** The same 26 tests, copied unmodified into a
clean `b5324f8` worktree and run against **baseline** production files → **15 failures + 2 errors**
(17 non-passing). The two that legitimately pass on both (`test_301`, a bare-`<span hidden>` case
with no competing author rule; `test_303`, the strip-the-guard negative control) were inspected and
are correct on both sides. These tests are not tautological. **PASS**

**97. Phase 7.14 focused suite.** `python -m unittest
tests.test_phase7_14_owner_usability_pilot_readiness` → **465 ran, FAILED (failures=1)**. The single
failure is `test_199e_no_acceptance_tag_yet` (finding 105). **PASS (accounted for)**

**98. Phase 7.14 DOM render harness.** `TestDomRenderContract.test_200_to_220_dom_render_contract`
→ **ok**. It executes the real `app.js` in the Node DOM harness and asserts `FAILED 0` with a
minimum of 190 checks. **PASS**

**99. Phase 7.14 browser QA, implementation.**
`node tests/phase7_14_browser_qa.js --browser edge|chrome --base http://127.0.0.1:8792` →
Edge **70/70**, Chrome **70/70**, 0 failures, 0 console errors, 0 exceptions, sole origin
`http://127.0.0.1:8792`. **PASS**

**100. Phase 7.14 browser QA is defect-specific.** The **same** HEAD harness run against a
**baseline** console scored **57/70 — 13 failures**, precisely on the computed-style, hit-test,
click-outcome and refresh-retention checks. The new gate genuinely catches the defect. **PASS**

**101. The accepted gate really was blind — independently confirmed.** The **accepted `b5324f8`**
browser-QA harness, run against the **defective baseline** console, scored **44/44 PASS** — on a
console where 226 of 259 controls were unclickable. This corroborates the implementation report's
central explanation for how the defect reached a pilot. **CONFIRMED**

**102. Phase 7.13 focused suite.** `python -m unittest
tests.test_phase7_13_unified_owner_console` → **269 ran, OK**, exit 0. **PASS**

**103. Phase 7.13 modal DOM harness.** Driven by the 7.13 suite (`MODAL_HARNESS` via subprocess);
included in the 269 passing tests. **PASS**

**104. Phase 7.12 regression.** `python -m unittest
tests.test_phase7_12_owner_notification_delivery` → **234 ran, OK**, exit 0. **PASS**

**105. The stale acceptance-tag guard was preserved.** `test_199e_no_acceptance_tag_yet` asserts no
Phase 7.14 acceptance tag exists; two legitimately do. It fails identically on the untouched
baseline (present in both fresh-worktree node sets), so it is **baseline-equivalent**. It was **not**
deleted, skipped, weakened or concealed by the implementation or by this audit. **PASS**

**106. Full in-place suite.** `python -m unittest discover -s tests -v` at HEAD `5fcbf6f` →
**4630 ran, 1 failure, 0 errors, 4 skipped**, 879.9 s, exit 1. The single failure is `test_199e`.
This is one node *better* than the implementation proof recorded (it logged an additional
`WinError 10053` loopback flake that did not recur here). **PASS**

**107. `compileall`.** `python -m compileall -q production core tests` → exit **0**, no output, on
all three legs (baseline worktree, implementation worktree, in-place). **PASS**

---

## J. Fresh-worktree differential

Two clean detached worktrees, identical Python executable, identical dependencies and environment,
**no `runs/` and no `__pycache__` in either**, identical command (`python -m unittest discover -s
tests -v`), identical timeout, run **sequentially** so neither perturbed the other.

> *Audit note:* a first attempt was **discarded** because `runs/` had leaked into the baseline
> worktree from an earlier console start, breaking the "absence of `runs/`" symmetry the brief
> requires. Both worktrees were destroyed and recreated, verified to differ in exactly the six
> expected files and nothing else, and the differential was re-run from scratch. The numbers below
> are from the clean re-run.

**108. Fresh baseline `b5324f8`.** Ran **4602**, failures **2**, errors **15**, skipped **329**,
473.4 s, unittest exit 1, `compileall` exit 0. **RECORDED**

**109. Fresh implementation `af443b5`.** Ran **4628** (+26, exactly the new tests), failures **2**,
errors **15**, skipped **329**, 475.4 s, unittest exit 1, `compileall` exit 0. **RECORDED**

**110. Differential comparison.** Failure, error and skip **counts are identical**. Non-passing node
sets are **17 nodes each**, of which **16 are identical**. The single differing node is a swap
between two loopback HTTP tests:

| | Node |
|---|---|
| Baseline only | `test_phase7_4_owner_dashboard.HttpSecurity.test_post_to_unknown_endpoint_rejected` |
| Implementation only | `test_phase7_13_unified_owner_console.TestBody.test_52_request_size_bounded` |

Both fail with the **same signature**, `ConnectionAbortedError: [WinError 10053]`; both **pass in
isolation** on their own worktree (21/21 and 7/7 respectively); and `git diff b5324f8 af443b5` is
**0 bytes** for `tests/test_phase7_4_owner_dashboard.py`,
`tests/test_phase7_13_unified_owner_console.py` and
`production/phase7_unified_owner_console.py`. This is the documented Windows loopback flake landing
on a different victim per run, in code the hotfix does not touch — not a regression. **PASS**

**111. All new navigation tests pass on the implementation.** None of the 26 new tests appears in
the implementation's non-passing set. **PASS**

**112. Defect-specific tests fail on baseline.** Established directly in finding 96 (17 of 26
non-passing against baseline production files) and in finding 100 (13 browser-QA failures).
Note that the 26 tests are absent from the baseline commit itself, so they cannot appear in the
baseline worktree run; the copy-forward method in finding 96 is the correct way to establish this.
**PASS**

**113. No lost baseline passes, no new failures, no broadened skips.** Skips are 329 on both sides;
no test that passed on baseline fails on the implementation other than the flake swap of finding
110; shared test verdicts are otherwise equivalent. **PASS**

**114. Verdict.** `FRESH_WORKTREE_FULL_SUITE_BASELINE_EQUIVALENT_NONZERO` — **independently
proven**, and judged **relatively, not absolutely**. Both sides are nonzero because `runs/T2` is
gitignored, so T2-data-dependent tests cannot find inputs in a bare checkout. **A nonzero suite is
not being called green.** **PASS**

**115. Audit worktrees removed.** Both worktrees were deleted and `git worktree prune` run; only the
primary workspace remains. `git clean` was never run in the primary workspace, and
`runs/T2/phase7` there is intact (12 phase directories). **PASS**

---

## K. Source immutability and documentation

**116. Source immutability.** `git diff b5324f8 af443b5` is empty for all `production/*.py`,
all `core/`, all `docs/`, all launcher wrappers, and specifically
`production/phase7_unified_owner_console.py`. The accepted Phase 7.3–7.13 authorities are unchanged.
**PASS**

**117. Proof-JSON source hashes verified.** All six `source_sha256` entries match the SHA-256 of the
working-tree bytes at HEAD exactly. **PASS**

**118. Proof-JSON git claims verified.** `baseline_commit`, `fix_commit`, `main_still_at`,
`checkpoint_tag_object`, `prior_acceptance_tag_object`, `baseline_tag_object`, `acceptance_claimed:
false`, `acceptance_tag: null`, `merged_to_main: false`, `pilot_started: false`,
`phase_8_started: false`, `accepted_tags_modified_or_moved: false` — all correct. **PASS**

**119. Documentation accuracy — root cause, cache, files, classification, prepare count, Edge/Chrome
evidence, boundary.** Every one of these is accurately described. The reported mechanism, the
cache exclusion, the changed-file set and counts, the navigation classification, the zero-prepare
result, the both-browser evidence and the Amazon boundary statements all reproduce. **PASS**

**120. Documentation accuracy — the fresh-worktree numbers differ slightly from mine, and that is
honest.** The proof records baseline 4602/2F/14E/329S and implementation 4628/2F/14E/329S with
16 identical nodes; I measured 15 errors and 17 nodes on both sides, the extra node being the
loopback flake of finding 110. Same run-to-run flake class, same conclusion, same verdict string.
Not a documentation defect. **PASS**

**121. Documentation defect — one overstated sentence.** Report §4.3 item 3 states that anything
"missing, malformed, non-200 or blocked routes to `openModalBlocked()` — never to a confirmable
dialog." The second clause is true in every case I tested. The first is not exhaustive: for the two
type-confusion shapes of finding 67, the validator throws and the flow reaches **neither**
`openModal()` nor `openModalBlocked()` — no dialog appears at all. The report's Known-limitations
section does not record this path. This is the sole basis for
`ACCEPTED_WITH_DOCUMENTATION_FIX`. **DOCUMENTATION FIX APPLIED**

**122. Known limitations are otherwise accurate and honestly disclosed.** The bounded CSS evaluator,
the permanently stale `test_199e`, the Python 3.14 caveat, the pre-existing `--no-browser` message,
the audit-chain tail-truncation limitation and the manual-invocation nature of browser QA are all
correctly stated. **PASS**

**123. A production defect is not being accepted as documentation-only.** The Day-0 pilot defect is
genuinely **fixed in production code** (`styles.css` guard + `index.html` fail-closed attribute +
`app.js` hardening) and independently verified fixed in two real browsers. The documentation fix
covers only the narrow, unreachable-with-the-accepted-backend robustness path of finding 67, which
is itself strictly safer than the accepted baseline. **PASS**

**124. Pilot status.** The pilot remains **paused at Day 0**. This audit did **not** resume it,
did **not** merge to `main`, and did **not** begin Phase 8. **PASS**

---

## L. Decision

`PHASE7_14_NEXT_ACTION_NAVIGATION_HOTFIX_ACCEPTED_WITH_DOCUMENTATION_FIX`

No rejection criterion is met: the CTA no longer opens a modal, never calls prepare, updates hash,
page, sidebar and breadcrumb, survives normal and hard refresh, and behaves identically under
keyboard and mouse in both browsers; no dead or multiply classified control remains; malformed
preparation can never enable Confirm; a blank executable modal is structurally impossible; phrase
gating is unchanged and space-sensitive; browser QA is 70/70 in both browsers; there are no
off-origin requests; CSP and security headers are byte-identical; the Phase 7.12 double gate and the
Amazon boundary are untouched; the full suite does not regress; and the fresh implementation is not
worse than the fresh baseline on any measure.

The documentation fix is the smallest genuine correction: one Known-limitations entry plus a
precision edit to a single sentence in §4.3. **No production code was modified by this audit.**

**Exact next action:** merge `hotfix-phase7-14-next-action-navigation` into `main`, restart the
launcher from accepted `main`, verify the CTA in a real browser once, then restart the 14-day owner
pilot at Day 0. Separately and independently of the pilot, retire or re-scope
`test_199e_no_acceptance_tag_yet`, and close the finding-67 robustness gap by making
`missingPreparationFields()` type-check `target_ids` before calling `.join()`.
