# Audit Response — v2.3.4-RC1 (Alpha-Blocker Patch)

The FINAL ALPHA READINESS review verified the RC's safety fixes but returned **NO-GO for staff
alpha**: a real user could not complete all gates without editing PROJECT-MANIFEST.json by hand,
plus several approval-integrity gaps. It prescribed a bounded RC1 patch — not a rebuild. That is
exactly what this is. Every P0 was reproduced against the code before fixing.

## The acceptance test the review demanded — now passing
> "A real person can move one real project from raw evidence to a correctly locked or approved
> decision without editing internal state and without receiving a false green status."

Verified end-to-end (and captured as `tests/test_alpha_rc1.py::EndToEndShadow`): init → scaffold gate
files → fill decisions → real demand/economics → IP-clean listing → real main+macro+specs with
hash-bound reviews → creative_edge → approve-main-image → approve-creative → approve-final →
**project unlocks**. Then editing a bound evidence file **re-locks** it. No manifest editing anywhere.

## P0 findings → fixes
| # | Finding | Fix |
|---|---|---|
| P0.1 | Feasibility/fulfillment/catalog/personalization/claims/relevance files never set their gates → had to edit the manifest | Deterministic file→gate readers (explicit `decision`, never inferred from filename) + `--scaffold-gate-files` templates |
| P0.2 | Completed THUMBNAIL-REVIEW.json ignored; scoring read the embedded object | Scoring now reads the completed file as source of truth; embedded object is a deprecated fallback; a rerun never overwrites a completed review |
| P0.3 | Owner could approve a different image than the one reviewed | `--approve-main-image` requires the asset to BE the compliance-record image and its current hash to match. Root cause also fixed: the validator now gets the project dir + accuracy fields, so a real main image can actually reach COMPLIANT |
| P0.4 | Creative approval could bind an empty bundle (zero hashes) | Refuses unless every required creative evidence file and referenced asset exists |
| P0.5 | Final approval didn't require evidence for every passing gate | Bundle derived from each passing required gate's declared evidence; a passing gate with no current evidence file is a hard refusal |
| P0.6 | JSON-only listings left IP_SAFETY = NOT_RUN | IP screen runs on listing.json content (title/bullets/description/backend/personalization/A+) and sets IP_SAFETY (Disney/Mickey → BLOCKED) |
| P0.7 | `python3` literals break on Windows | All child processes use `sys.executable`; tests too; docs use `python` |
| P0.8 | Staff QUICKSTART had the removed `--approve "<keyword>"`, wrong test counts/status | Docs regenerated to the real approval chain; doc-regression tests fail if a removed command reappears |
| P0.9 | Pipeline footer recommended a different next action than `--next` | Footer uses `stages.compute()` — one next-action engine |

Non-blocking also addressed: **P1.2** `requests` added to requirements + tested-version note; **P1.6**
kw_expand TOS wording softened to "public endpoint; verify current permitted use."

107/107 tests pass (14 new alpha-blocker tests, including the end-to-end shadow run and P0.3 identity
checks).

## Honestly still deferred (matches the review's "do not build yet")
- **Role/authentication (P1.1).** Any local user can pass `--by owner`. For alpha: run on the owner's
  trusted machine; keep approval commands with the owner. Real auth waits for the dashboard phase.
- **Executable stage orchestration** (`--run-stage`/`--run-all-ready`). Staff still run each tool;
  `--status`/`--next` navigate. This is the next build after one clean shadow project.
- **P1.3** rename `PUBLICATION_READY` (file-validator status) to avoid confusion with project
  readiness — terminology only; deferred to avoid churn.
- **P1.4** richer main-image category-review schema (hash/timestamp/rule reference). Owner approval
  already binds the exact image, so this is lower priority now.
- **P1.5** test ResourceWarnings — cosmetic; clean before beta.
- **Dashboard / database / import center.** Not until the gate contracts are frozen after alpha.

## Recommended alpha posture (from the review, and sound)
Owner-only **shadow alpha** on one trusted machine, one nurse project, using copied/manual exports.
**Do not publish or edit a live Amazon listing in the first cycle.** Record every unexpected result;
stop immediately if a project ever unlocks with a missing gate, an approval has no hashes, a changed
file fails to re-lock, or an IP-blocked phrase passes. The next real step is still physical: shoot one
production main image and one embroidery macro and walk them through the chain.
