# Phase 7.14 — Pilot Issue Template

One file (or one entry) per issue. Copy the block below. Keep these in your pilot records folder,
suggested `runs/T2/phase7/7.14/pilot/`. Do not commit them.

**Pilot rule: FIX DEFECTS ONLY. DO NOT ADD NEW INFRASTRUCTURE.**
If the fix would need a new module, provider, report, integration or page, it is not a pilot fix.
Record it under *Deferred (post-pilot)* instead.

---

```
ISSUE ID:        P714-___          (sequential: P714-001, P714-002, ...)
DATE:            YYYY-MM-DD
PILOT DAY:       ___ of 14

SEVERITY:        [ ] CRITICAL  — I could not complete the daily review at all
                 [ ] HIGH      — I completed it, but only by working around the problem
                 [ ] MEDIUM    — slowed me down or made me unsure
                 [ ] LOW       — cosmetic or a small annoyance

CATEGORY:        [ ] Launcher (start / stop / open)
                 [ ] Next-action guidance (wrong, unclear, or pointed nowhere useful)
                 [ ] Navigation (could not find where to go)
                 [ ] Dead button / dead link / fake route
                 [ ] Blank or unusable dialog
                 [ ] Empty screen with no explanation or no next step
                 [ ] Status I could not understand
                 [ ] Failure I could not read, or that vanished too fast
                 [ ] Spinner that never finished
                 [ ] Needed PowerShell for a normal daily task
                 [ ] Data looked wrong or out of date
                 [ ] Accessibility (keyboard, focus, contrast, text size)
                 [ ] Other: ______________________

WHERE:           Page / screen: ______________________
                 Control or text: ______________________

WHAT I EXPECTED:
                 ______________________________________________

WHAT HAPPENED:
                 ______________________________________________

STEPS TO REPRODUCE:
                 1. ______________________________________________
                 2. ______________________________________________
                 3. ______________________________________________

HOW OFTEN:       [ ] Every time   [ ] Sometimes   [ ] Saw it once

DID IT BLOCK MY DAILY REVIEW?        [ ] Yes   [ ] No
DID I HAVE TO USE POWERSHELL?        [ ] Yes   [ ] No
TIME LOST (minutes):                 ______

AMAZON SAFETY CHECK (must all stay true):
                 [ ] The toolkit did not ask me for an Amazon sign-in or password
                 [ ] The toolkit did not change anything in my Amazon account
                 [ ] System Health boundary counters still all read 0
                 If any box above is unticked, mark this issue CRITICAL immediately.

EVIDENCE:        Screenshot / exported file / log line (no passwords, no tokens):
                 ______________________________________________

--- filled in when the issue is dealt with ---

RESOLUTION:      [ ] Fixed (defect)
                 [ ] Not a defect — explained: ______________________
                 [ ] Deferred (post-pilot) — needs new infrastructure, so out of scope
FIXED ON:        YYYY-MM-DD
VERIFIED BY OWNER: [ ] Yes — I reproduced the original steps and the problem is gone
NOTES:           ______________________________________________
```

---

## Deferred (post-pilot) list

Anything that would need new infrastructure. Write it down so it is not lost, and do **not** build it
during the pilot.

| # | Idea | Why it is out of pilot scope |
|---|------|------------------------------|
| 1 |      |                              |
| 2 |      |                              |
