# Phase 7.14 — Owner Pilot Guide (14 days)

A real 14-day pilot run by the owner, on the owner's own computer, with the owner's own data.

**The pilot rule:**

> **FIX DEFECTS ONLY. DO NOT ADD NEW INFRASTRUCTURE.**

If something is broken, confusing or missing a next step, record it and fix the defect. Do not add a
new feature, a new report, a new integration, a new provider or a new page during the pilot. Ideas
for new capability go on a list for after the pilot.

**Nothing in this pilot touches Amazon.** The toolkit never signs in to Seller Central, never calls a
seller or advertising API, never downloads a report for you and never changes a campaign, listing or
inventory. You remain the only person who acts inside Amazon. Every Amazon-side action you take is
something you do yourself, and then record here.

---

## Day 0 — setup (about 30 minutes, once)

Do these in order. Tick each in `PHASE7_14-OWNER-PILOT-CHECKLIST.md`.

1. **Launcher verification.** Double-click `Start-AMZ-Toolkit.bat`. Wait. Confirm the browser opens
   by itself on `http://127.0.0.1:8780` and the page shows *Overview*. Note how long it took.
2. **Browser verification.** Confirm the page works in the browser that opened. Try it once in a
   second browser (Edge and Chrome) by running `Open-AMZ-Toolkit.bat` after setting that browser as
   default, or by pasting the address.
3. **Stop verification.** Double-click `Stop-AMZ-Toolkit.bat`. Confirm it reports the toolkit
   stopped, and that reloading the page then fails. Start it again.
4. **Data backup.** Before anything else, make a copy of your `runs/` folder somewhere safe. This is
   your business data and it is not stored anywhere else.
5. **Report availability.** Confirm you can download the report you normally use from Amazon
   yourself, and that you know where you save it. The toolkit will not fetch it for you.
6. **Owner orientation.** Read the *Overview* page top to bottom once. The single panel under the
   page title is your next step. Everything below it is detail.
7. **Issue-recording method.** Decide where you will record issues, and put a copy of
   `PHASE7_14-PILOT-ISSUE-TEMPLATE.md` and `PHASE7_14-PILOT-DAILY-LOG-TEMPLATE.md` there. A plain
   folder with one file per day is enough. Do **not** commit these records to the repository.

Suggested location for your pilot records: `runs/T2/phase7/7.14/pilot/`

---

## Daily routine (target: under 15 minutes)

1. **Start the toolkit.** Double-click `Start-AMZ-Toolkit.bat`. Note whether it worked first time
   and roughly how long it took.
2. **Review the next action.** Read the panel under the page title. It tells you what needs
   attention, why it matters, what to do, where to go and what to expect. Note how long it took you
   to understand what to do.
3. **Inspect the current analysis.** Open *Analysis & Decisions*. If it says *No current report
   analysis found*, that is not an error — it means there is nothing new to decide on.
4. **Record an owner decision** where one is waiting. Read the evidence first.
5. **Do the Amazon work yourself**, outside the toolkit, if a decision calls for it. Then come back
   and **record the manual action** on *Manual Actions*. The toolkit only knows what you tell it.
6. **Review alerts.** Open *Alerts*. Acknowledge or dismiss anything you have dealt with.
7. **Review follow-ups.** Open *Follow-ups*. Remember: a before/after comparison is an observation of
   two periods. It is never evidence of why the numbers moved.
8. **Stop the toolkit.** Double-click `Stop-AMZ-Toolkit.bat` when you are finished for the day.
9. **Record time spent and any usability issue** in your daily log. Anything that made you pause,
   guess, or open PowerShell counts as an issue.

**If you had to use PowerShell for any part of the normal daily routine, that is a defect.** Record
it. The daily routine must be completable with double-clicks and the browser only.

---

## At least once during the 14 days

Spread these out; one per day is plenty.

| # | Exercise | Where | What good looks like |
|---|----------|-------|----------------------|
| 1 | **Create an export** | Overview → Technical details → Download | A file downloads and opens |
| 2 | **Create a backup snapshot** | Backup & Recovery → Create a backup now | A snapshot appears in the list |
| 3 | **Verify a backup** | Backup & Recovery → Verify | A clear pass/fail result |
| 4 | **Create a research run** | Watchlists → Run now | A run appears under Research |
| 5 | **Create a watchlist** | (configured outside the console) | It appears on Watchlists with a schedule |
| 6 | **Review an alert** | Alerts → Acknowledge or Dismiss | The status changes and is recorded |
| 7 | **Review the notification outbox** | Notifications | You can tell sent from unconfirmed |
| 8 | **Recovery drill (isolated)** | see below | You recover a copy without touching live data |
| 9 | **Confirm Seller Central counters are zero** | System Health → boundary counters | Every counter reads 0 |

### The recovery drill

Do this **on a copy, never on your live data**.

1. Copy your whole toolkit folder to a scratch location.
2. In the copy, delete the `runs/T2/phase7/7.5` folder to simulate loss.
3. Use *Backup & Recovery* to create a recovery plan for your most recent snapshot.
4. Follow the plan by hand in the copy and confirm the missing records come back.
5. Delete the scratch copy.

The console never runs a destructive restore for you. The plan is read-only advice; you carry it out.

---

## What to record every day

Use `PHASE7_14-PILOT-DAILY-LOG-TEMPLATE.md`. It covers all the pilot metrics:

* startup success (did it work first time?) and startup time in seconds;
* time to understand Overview; time to identify the next action; time to complete your review;
* number of PowerShell uses (target: **zero**);
* number of dead ends, unclear statuses, UI defects and blocked workflows;
* owner confidence rating 1-5 and owner effort rating 1-5;
* completed decisions, completed manual actions, completed follow-ups;
* exports created and backups verified.

**These metrics describe your experience of the toolkit. They never describe your business results,
and no comparison in this toolkit establishes business causation.**

---

## If something goes wrong

| What you see | What it means | What to do |
|--------------|---------------|------------|
| `PORT 8780 IS ALREADY IN USE` | another program has the address | close that program, run Start again |
| "A supported Python was not found" | Python 3.9+ is missing | install Python 3.9+, tick *Add Python to PATH* |
| "The toolkit is already starting" | you double-clicked twice | wait a few seconds |
| "did not become ready in time" | it started but never answered | run Stop, then Start once more |
| "Your local session expired" | the page sat open too long | reload the page — nothing was changed |
| Integrity error on System Health | recorded history did not verify | stop and record it as a critical issue |

In every one of these cases, nothing on your Amazon account has been touched.

---

## End of pilot

Work through `PHASE7_14-PILOT-EXIT-CRITERIA.md`. Every criterion is either met or not met, with the
evidence you recorded. Unmet criteria become the next work item — not a reason to add new features.
