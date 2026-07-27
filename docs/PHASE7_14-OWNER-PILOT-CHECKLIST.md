# Phase 7.14 — Owner Pilot Checklist

Copy this file into your pilot records folder (suggested: `runs/T2/phase7/7.14/pilot/`) and tick as
you go. Do not commit your completed copy.

**Pilot rule: FIX DEFECTS ONLY. DO NOT ADD NEW INFRASTRUCTURE.**

---

## Day 0 — setup

- [ ] **Launcher verification** — `Start-AMZ-Toolkit.bat` double-clicked; toolkit started
- [ ] Browser opened **by itself**, only after the toolkit was ready
- [ ] Address is `http://127.0.0.1:8780` and the page shows *Overview*
- [ ] Startup time recorded: ________ seconds
- [ ] **Browser verification** — works in Microsoft Edge
- [ ] **Browser verification** — works in Google Chrome
- [ ] `Open-AMZ-Toolkit.bat` opens the browser when the toolkit is already running
- [ ] `Open-AMZ-Toolkit.bat` explains what to do when the toolkit is **not** running
- [ ] `Stop-AMZ-Toolkit.bat` stops the toolkit and says so
- [ ] Starting twice does **not** start a second copy
- [ ] **Data backup** — `runs/` folder copied somewhere safe
- [ ] **Report availability** — I can download my report from Amazon myself, and I know where I save it
- [ ] **Owner orientation** — I have read the Overview page once, top to bottom
- [ ] I can find my next step without scrolling
- [ ] **Issue-recording method** chosen; issue + daily-log templates copied there
- [ ] Pilot records folder is **not** committed to the repository

## Every day (repeat 14 times)

- [ ] Started the toolkit by double-click
- [ ] Read the next-action panel and knew what to do
- [ ] Inspected the current analysis
- [ ] Recorded an owner decision (if one was waiting)
- [ ] Did any Amazon work myself, outside the toolkit
- [ ] Recorded the manual action afterwards (if applicable)
- [ ] Reviewed alerts
- [ ] Reviewed follow-ups
- [ ] Stopped the toolkit
- [ ] Recorded time spent and any usability issue
- [ ] **PowerShell uses today: ________** (target: 0)

## At least once during the pilot

- [ ] **Export exercise** — created and opened an export
- [ ] **Backup exercise** — created a backup snapshot
- [ ] **Backup verified** — ran Verify on a snapshot and got a clear result
- [ ] **Research exercise** — a research run was created
- [ ] **Watchlist exercise** — a watchlist appears with a schedule and next-due time
- [ ] **Alert exercise** — reviewed an alert and acknowledged or dismissed it
- [ ] **Notification review** — reviewed the notification outbox and could tell sent from unconfirmed
- [ ] **Recovery drill** — completed on an isolated copy, never on live data
- [ ] **Seller Central counter verification** — every boundary counter on System Health reads `0`
- [ ] Confirmed the toolkit never asked me for an Amazon password or sign-in

## Quality gates to watch for every day

- [ ] No dead button (every control did something, navigated, copied, or said why it was disabled)
- [ ] No blank or unusable dialog
- [ ] No link that led to a page that does not exist
- [ ] No status I could not understand
- [ ] No spinner that never finished
- [ ] No failure that vanished before I could read it
- [ ] Every empty screen told me what was missing and what to do next

## End of pilot

- [ ] Daily logs complete for all 14 days
- [ ] All issues recorded using the issue template
- [ ] Exit criteria worked through in `PHASE7_14-PILOT-EXIT-CRITERIA.md`
- [ ] Unmet criteria written up as the next work item
- [ ] No new infrastructure was added during the pilot
