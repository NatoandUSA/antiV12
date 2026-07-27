# Phase 7.14 — Pilot Exit Criteria

Work through this at the end of the 14 days. Each criterion is **MET** or **NOT MET**, with the
evidence you recorded. There is no partial credit and no "close enough".

A NOT MET criterion is the next work item. It is never a reason to add new features.

**Pilot rule that applied throughout: FIX DEFECTS ONLY. DO NOT ADD NEW INFRASTRUCTURE.**

---

## Required criteria

| # | Criterion | How it is judged | Result |
|---|-----------|------------------|--------|
| 1 | **Launcher works reliably** | `Start-AMZ-Toolkit` succeeded on the first double-click on at least **13 of 14** days, and every failure had a clear on-screen reason | MET / NOT MET |
| 2 | **No normal daily PowerShell requirement** | Total PowerShell uses for the normal daily routine across 14 days = **0** | MET / NOT MET |
| 3 | **No dead buttons** | Zero recorded issues in category *Dead button / dead link / fake route* | MET / NOT MET |
| 4 | **No blank or unusable modal** | Zero recorded issues in category *Blank or unusable dialog* | MET / NOT MET |
| 5 | **No fake route** | Every link and recommendation led to a page that exists and rendered | MET / NOT MET |
| 6 | **Next-action guidance is accurate** | "Was the next action the right one?" answered **Yes** on at least **12 of 14** days, and never wrong in a way that sent you to the wrong place | MET / NOT MET |
| 7 | **Owner completes at least one full workflow** | At least once: analysis reviewed → decision recorded → Amazon action done by you → manual action recorded | MET / NOT MET |
| 8 | **Owner records at least one decision** | Completed decisions total ≥ 1 | MET / NOT MET |
| 9 | **Owner records at least one manual action** | Completed manual actions total ≥ 1 | MET / NOT MET |
| 10 | **A later report supports one observational follow-up** | At least one follow-up reviewed, based on a later report period, and understood as an observation only — never as proof of cause | MET / NOT MET |
| 11 | **Backup and recovery drill completed** | A snapshot was created, a snapshot was verified, and the isolated recovery drill was completed on a copy | MET / NOT MET |
| 12 | **Seller Central counters remain zero** | Every boundary counter read `0` on all 14 days; the toolkit never asked for an Amazon sign-in and never changed anything in the Amazon account | MET / NOT MET |
| 13 | **No unresolved critical usability defect** | Every CRITICAL and HIGH issue is either fixed and verified by the owner, or explained as not a defect | MET / NOT MET |
| 14 | **Owner confidence meets the threshold** | Average confidence rating **≥ 4.0 / 5**, with no single day below 3 | MET / NOT MET |

## Supporting targets

Not pass/fail on their own, but they explain a NOT MET above and set the next priority.

| Target | Threshold | Actual |
|--------|-----------|--------|
| Median startup time | ≤ 20 seconds | ____ |
| Median time to identify the next action | ≤ 30 seconds | ____ |
| Median owner review time | ≤ 15 minutes | ____ |
| Average owner effort rating | ≥ 4.0 / 5 | ____ |
| Total dead ends | 0 | ____ |
| Total unclear statuses | ≤ 3 across 14 days | ____ |
| Total blocked workflows | 0 | ____ |
| Exports created | ≥ 1 | ____ |
| Backups verified | ≥ 1 | ____ |

## Hard stops

If any of these is true, the pilot has **failed** regardless of every other result:

- the toolkit connected to Amazon Seller Central, or asked for an Amazon sign-in or password;
- any Seller Central boundary counter was non-zero on any day;
- the toolkit changed anything inside the Amazon account;
- recorded history failed to verify and the cause was not identified;
- business data was lost and could not be recovered from a backup.

## Outcome

```
PILOT PERIOD:            YYYY-MM-DD to YYYY-MM-DD
CRITERIA MET:            ____ of 14
HARD STOPS TRIGGERED:    ____ (must be 0)

OVERALL:   [ ] PILOT PASSED — all 14 criteria MET, no hard stop
           [ ] PILOT PASSED WITH FOLLOW-UP — criteria ____ NOT MET, fixes listed below
           [ ] PILOT FAILED — a hard stop was triggered

NEXT WORK ITEMS (defect fixes only, in priority order):
  1. ______________________________________________
  2. ______________________________________________
  3. ______________________________________________

DEFERRED TO AFTER THE PILOT (new capability — explicitly not built during the pilot):
  1. ______________________________________________
  2. ______________________________________________

OWNER SIGN-OFF:          ______________________   DATE: ____________
```
