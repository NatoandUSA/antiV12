#!/usr/bin/env python3
"""production — Phase 6 orchestration authorities that CALL the mature Phase 5 engines.

Phase 6A adds exactly one orchestration authority, ``product_workspace``. It does not
re-implement product-fact normalization, claim evidence, category policy, the keyword
source, the PageAuditor, or any copy engine — it reuses the single Phase 5 authority for
each and assembles a trustworthy product workspace on top of them. Local and offline only;
no Amazon / Seller Central path exists here.
"""
