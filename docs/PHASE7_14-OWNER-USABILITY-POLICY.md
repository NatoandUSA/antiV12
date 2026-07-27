# Phase 7.14 — Owner Usability Policy

This policy governs the owner-facing surface of the toolkit: the launcher, the next-action guidance
and the console pages. It is a usability and presentation policy. It creates no business authority,
no analysis, no recommendation algorithm and no Amazon-side capability.

---

## 1. Permanent Amazon boundary

Unchanged, permanent, and first. Nothing in Phase 7.14 connects to Amazon Seller Central, uses a
seller sign-in, seller credentials, seller cookies, a seller session or a seller token, calls a
seller API or an advertising API, downloads a seller report, mutates a campaign, bid, budget,
keyword, target, negative, listing or inventory, performs a bulk upload, drives a seller browser,
messages a buyer or requests a review.

**The owner is the only manual bridge to Amazon.** The user interface must never create or imply an
automatic Amazon-side action. Every seller-account counter is a constant zero and no code path
increments one.

## 2. What Phase 7.14 may and may not do

| May | May not |
|-----|---------|
| Present state an accepted authority already decided | Decide anything itself |
| Rank existing conditions in a fixed, documented order | Invent a metric, score or risk level |
| Restate an upstream count verbatim | Recompute, estimate or interpolate a count |
| Say what was observed | Say what caused what |
| Link to an existing page or subsection | Create a page or action so a link has somewhere to go |
| Start / stop / open ONE local console | Register a service, scheduler or startup entry |

## 3. Next-action guidance

* One recommendation at a time, chosen by a **fixed total priority order** (1 highest, 14 lowest).
  The first matching rule wins, so the same state always produces the same recommendation.
* Every recommendation answers five questions: what needs attention, why it matters, what to do,
  where to go, and what result to expect.
* Every recommendation records the accepted authority it came from and, where one exists, the
  upstream record id.
* `generated_at` is operational metadata and is excluded from the guidance identity, so an unchanged
  situation keeps an unchanged identity.

### Priority order

| # | Rule | Meaning |
|---|------|---------|
| 1 | integrity-or-audit-corruption | recorded history or a module did not verify |
| 2 | security-or-network-policy-block | the Amazon boundary did not report as fully refused |
| 3 | required-module-unavailable | the report-analysis workspace does not exist |
| 4 | required-data-missing | no accepted analysis is ready for review |
| 5 | stale-critical-data | the report analysis is older than 24 hours |
| 6 | pending-owner-decision | an accepted analysis has undecided items |
| 7 | pending-manual-action | a recorded manual action is still open |
| 8 | due-outcome-followup | a later report period exists for a recorded action |
| 9 | open-escalated-alert | an open alert is CRITICAL or IMPORTANT |
| 10 | due-watchlist | a watchlist has passed its scheduled time |
| 11 | notification-unknown-requires-review | a delivery finished UNKNOWN |
| 12 | backup-missing-or-stale | there is no recent backup |
| 13 | update-available | a newer toolkit version was found |
| 14 | no-urgent-action | nothing is waiting — **not an error** |

### Refused wording

Guidance text may never contain PPC or advertising vocabulary (PPC has not been built — that is
Phase 8), seller-account vocabulary, or causal vocabulary. The engine refuses to emit a
recommendation containing a refused phrase; it fails loudly rather than shipping the wording.

`READY_EMPTY` and "no urgent action" are normal, healthy states. They are never presented as errors.

## 4. Destinations

Every recommendation and every control must do exactly one of:

1. navigate to an existing console page;
2. navigate to an existing subsection of one;
3. copy an already-supported local command from a fixed allowlist;
4. show owner instructions;
5. be visibly unavailable **with a stated reason**.

There is no import page and no advertising page. Guidance must not name one. Wording for an absent
analysis is **"No current report analysis found"**, never "no advertising analysis".

## 5. Button contract

Every clickable control executes a real supported action, navigates to a real page, copies a
verified value, or is disabled with a visible reason.

Forbidden: dead buttons, blank modals, silent failures, infinite spinners, fake routes, fake actions, false success, double submission, and an unavailable action presented as enabled.

## 6. States the owner sees

`READY` · `READY — NO ITEMS` · `ACTION REQUIRED` · `BLOCKED` · `STALE` · `UNKNOWN` · `NEEDS SETUP`

The whole toolkit also reports one overall usability state, derived only from the guidance above:

| State | Owner label | When |
|-------|-------------|------|
| `SESSION7_14_USABILITY_BLOCKED` | BLOCKED | priority 1-2 — resolve before using |
| `SESSION7_14_USABILITY_REQUIRED` | NEEDS SETUP | priority 3-5 — works, nothing to work on yet |
| `SESSION7_14_USABILITY_READY_PARTIAL` | ACTION REQUIRED | priority 6-13 — usable, something is waiting |
| `SESSION7_14_USABILITY_READY` | READY | priority 14 — usable, nothing waiting |

The launcher reports its own states (`SESSION7_14_LAUNCHER_*`, including `…_STARTING` while it waits
for health), and reports whether this working copy is pilot-ready (`SESSION7_14_PILOT_READY` /
`SESSION7_14_PILOT_REQUIRED`) based on the six launcher scripts and six pilot documents being present.

Each carries a **word, a glyph and a colour**. Colour is never the only carrier of meaning. An
unmapped upstream value shows `UNKNOWN` — an honest answer, never a guess.

Feedback is always one of: loading, ready, no change, completed, blocked, failed, session expired,
stale. A serious failure is shown in a persistent panel or modal — never only in a toast that
disappears.

## 7. Empty states

Every empty state states what is missing, whether it is an error, why it matters, and the next valid
step. `No data` on its own is never acceptable.

## 8. Progressive disclosure

Default views show owner-facing status. Canonical readiness tokens, schemas, hashes, lineage,
record ids, timestamps and technical policy results live behind **View details** and are closed by
default. The owner never needs an internal id for ordinary operation.

## 9. Launcher policy

* One fixed command may be spawned: the accepted console, on `127.0.0.1:8780`, `serve`.
* A shell is never invoked; no argument is assembled from owner input.
* The port is fixed. It is **never** selected at random — Start, Stop and Open must always agree
  about where the console is. If port 8780 is taken by another program the launcher says
  `PORT 8780 IS ALREADY IN USE` and stops.
* A healthy console is never duplicated; an exclusive lock makes a second double-click safe.
* Stop signals a process only after its recorded PID **and** its recorded process-start token both
  still match. No process-tree kill utility, no process-name matching, and never "stop every
  interpreter on this machine".
* The browser opens only after `/api/v1/health` reports the accepted console ready, and only ever at
  the validated loopback console URL.
* Launcher logs are bounded and secret-free: no environment value, cookie, CSRF token, confirmation
  token, Authorization value or absolute path.

## 10. Front-end constraints

No external framework, CDN, font, image, analytics, WebSocket, service worker or browser storage.
No inline script, no `innerHTML`, no `eval`. Every value is rendered as text, so upstream data can
never inject markup. The CSRF token lives only in a closure variable and is never written to the DOM
or to storage.

## 11. Accessibility

Semantic landmarks, a skip link, keyboard navigation, visible focus, `aria-live` regions, an
accessible modal with focus trap and focus return, labelled controls, screen-reader labels on icon
controls, correct heading hierarchy, minimum useful target sizes, reduced-motion support, and no
colour-only meaning. Usable at 1366x768; optimized for 1920x1080; no horizontal page overflow (wide
tables scroll inside their own container).

## 12. Change rule during the pilot

**FIX DEFECTS ONLY. DO NOT ADD NEW INFRASTRUCTURE.**
