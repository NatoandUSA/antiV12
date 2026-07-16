#!/usr/bin/env python3
"""
research.asin_clusters — normalized title clusters and probable variation families.

Fixes ASIN-006. In the observed fixture, Batch 1 spent 6 of 10 Cerebro slots on
3 products: three pairs of near-identical listings (same brand, same title core,
different colour/size child ASINs). Cerebro returns the same keywords for each,
so those slots bought nothing.

Phase 1 only *detects and exposes* redundancy. Phase 2 enforces the limits.
"""
import re, hashlib
from difflib import SequenceMatcher

# Words that vary between children of one parent listing, or carry no product identity.
NOISE = set("""
xs s m l xl xxl xxxl 2xl 3xl 4xl 5xl small medium large plus size sizes
black white grey gray navy blue red pink green purple yellow orange brown beige
cream ivory maroon burgundy teal mint sage lavender heather charcoal olive
color colour colors colours women womens woman women's men mens man men's unisex
ladies girls boys kids adult adults youth
for the and or of to with a an in on your you my our this that is are be it as at by from
gift gifts new hot sale best top quality soft cozy comfy funny cute
""".split())


def normalize_title(title):
    """Strip size/colour/gender/filler so child variations collapse to one core string."""
    t = re.sub(r"[^a-z0-9 ]+", " ", str(title).lower())
    toks = [w for w in t.split() if w and w not in NOISE and len(w) > 1]
    seen, core = set(), []
    for w in toks:                    # de-dup while preserving order
        if w not in seen:
            seen.add(w); core.append(w)
    return " ".join(core)


def title_cluster_key(title):
    """Stable id for a normalized title. Same core wording -> same cluster."""
    core = normalize_title(title)
    return "tc_" + hashlib.sha1(core.encode("utf-8")).hexdigest()[:10] if core else "tc_empty"


def _num(x):
    """Helium 10 ships '377,937', '$12.50', '' and nan in numeric columns."""
    if x is None:
        return None
    s = re.sub(r"[^0-9.\-]", "", str(x))
    if s in ("", "-", ".", "nan"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def variation_family_key(brand, title, bsr=None, reviews=None, rating=None, seller=None):
    """Probable parent/child family.

    Brand + title core is the backbone. Shared BSR / review count / rating are
    strong corroboration because Helium 10 reports parent-level figures on every
    child of the same parent ASIN.
    """
    b = re.sub(r"[^a-z0-9]+", "", str(brand or "").lower())
    core = normalize_title(title)
    parts = [b, core]
    n = _num(bsr)
    if n:     parts.append(f"bsr{int(n)}")
    n = _num(reviews)
    if n:     parts.append(f"rv{int(n)}")
    n = _num(rating)
    if n:     parts.append(f"rt{round(n, 1)}")
    if seller and str(seller).lower() not in ("nan", "none", ""):
        parts.append(re.sub(r"[^a-z0-9]+", "", str(seller).lower()))
    return "vf_" + hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:10]


def title_similarity(a, b):
    """0-1 similarity of two normalized titles (spec flags > 0.88 as redundant)."""
    na, nb = normalize_title(a), normalize_title(b)
    if not na or not nb:
        return 0.0
    return SequenceMatcher(None, na, nb).ratio()


SIM_THRESHOLD = 0.88   # spec: title similarity above 0.88 is redundant


def annotate_clusters(cands, sim_threshold=SIM_THRESHOLD):
    """Attach title_cluster / variation_family to each candidate and count members.

    Clustering is similarity-based, not exact-match: two GODMERCH listings in the
    observed fixture score 0.91 similar but have different title cores, so exact
    keys alone would let both into one batch and buy the same keywords twice.
    Single-linkage keeps it deterministic (input order never changes the result).
    """
    from collections import Counter
    n = len(cands)
    for c in cands:
        c["title_core"] = normalize_title(c.get("title", ""))

    # union-find over titles that are near-identical
    parent = list(range(n))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i, j):
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[max(ri, rj)] = min(ri, rj)   # lowest index wins -> deterministic

    for i in range(n):
        for j in range(i + 1, n):
            a, b = cands[i]["title_core"], cands[j]["title_core"]
            if not a or not b:
                continue
            if a == b or SequenceMatcher(None, a, b).ratio() >= sim_threshold:
                union(i, j)

    # Key each cluster off its lexicographically smallest member, NOT its root
    # index: the root depends on input order, so index-derived keys would change
    # when rows arrive in a different order and break reproducibility.
    canon = {}
    for i, c in enumerate(cands):
        r = find(i)
        core = c["title_core"]
        if r not in canon or core < canon[r]:
            canon[r] = core

    for i, c in enumerate(cands):
        c["title_cluster"] = "tc_" + hashlib.sha1(
            canon[find(i)].encode("utf-8")).hexdigest()[:10]
        # A variation family is one brand inside one title cluster; shared
        # parent-level numbers corroborate but are not required.
        brand = re.sub(r"[^a-z0-9]+", "", str(c.get("brand") or "").lower())
        c["variation_family"] = "vf_" + hashlib.sha1(
            (brand + "|" + c["title_cluster"]).encode("utf-8")).hexdigest()[:10]

    tc = Counter(c["title_cluster"] for c in cands)
    vf = Counter(c["variation_family"] for c in cands)
    for c in cands:
        c["title_cluster_size"] = tc[c["title_cluster"]]
        c["variation_family_size"] = vf[c["variation_family"]]
    return cands
