# AMZ FBM Toolkit v2.3.4-RC1
Decision-support toolkit for a personalized-apparel Amazon US **FBM** seller.
It analyses manually exported reports and produces gated, owner-approved listing
decisions. **It never connects to Seller Central. The owner is the only publish bridge.**

## Install
    pip install -r requirements.txt   # pandas, openpyxl, pyyaml, pillow

## Run the pipeline
    python pipeline.py runs/<niche> --init-project --decoration-method "machine embroidery"
    python pipeline.py runs/<niche> --scaffold-gate-files   # blank gate files for staff to fill
    python pipeline.py runs/<niche> "<Project Name>" --seed "custom nurse sweatshirt" --anchor nurse
    python pipeline.py runs/<niche> --status      # one clear next action
    python pipeline.py runs/<niche> --next
    # owner approval chain (each is hash-bound and auto-invalidates if its files change):
    python pipeline.py runs/<niche> --approve-main-image --asset main.png --by owner
    python pipeline.py runs/<niche> --approve-creative --by owner
    python pipeline.py runs/<niche> --approve-final --by owner   # refuses until creative approval passes

## What v2 adds over v1
An **enforcement core**: central statuses + exit codes, a hard-gate engine, an atomic
PROJECT-MANIFEST.json, config-driven business rules, and version-bound owner approval.
A project is **PUBLICATION LOCKED** until every hard gate passes AND the owner approves —
a failed gate or missing approval can never be bypassed.

## Tests
    python -m unittest discover -s tests          # 107/107

## Status
Backend enforcement + creative planning + actual-asset validation are hardened and tested. In
v2.3.4-RC the alternative evidence paths that undermined v2.3.3 are closed: supplier proof needs
its own hash-bound review, tiny images cannot pass, image-spec hashes must match real files, and
there is now a supported owner approval chain (main-image → creative → final), each hash-bound and
self-invalidating. Creative Edge reports only a CREATIVE PACKAGE status; project publication is
decided solely by `pipeline --status`. See CAPABILITIES.json for the maturity source of truth,
archive/misc/AUDIT-RESPONSE-v2_3_4.md for what changed and what is deferred, and docs/ARCHITECTURE.md for the
map. Executable stage orchestration (--run-stage) and the web dashboard/database are intentionally
NOT built yet — next after one real end-to-end pilot.

## Safety
No Seller Central connection, no credentials, no automation of Amazon actions.
IP library and category rules are risk-reduction, not legal clearance — verify manually.
