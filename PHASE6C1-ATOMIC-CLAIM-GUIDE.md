# Phase 6C.1 — Atomic Claims, in Plain English

This session fixed one specific safety defect in how the toolkit records **claims** about your product,
and re-ran Phase 6A → 6B → 6C to prove the fix. Your listing is still a **safe draft** — nothing was
published, and nothing touched your Amazon or Seller Central account.

## The defect we found

The toolkit stores every claim it might make about a product with an *evidence state* — VERIFIED means
"there is proof, this may appear in copy"; anything else is held back until you supply proof.

One claim was recorded like this:

> **"A personalized gift for nurses."**  → state: **VERIFIED**

But only **one** part of that sentence was actually proven: that the audience is **nurses** (your paid
keyword research established that). The other two ideas rode along for free:

- **"personalized"** — you never verified the product can be personalized.
- **"gift"** — you never verified it is sold or packaged as a gift.

So a single verified idea (nurses) was silently vouching for two unproven ones. Phase 6C already stopped
that sentence from reaching your visible copy, but the underlying **claim record** was still labelled
VERIFIED — a future step could have trusted the whole sentence. That is the hole this session closed.

## Why "for nurses" is different from "a personalized gift for nurses"

- **"For nurses"** is a *market identity* — who the product is for. Your keyword research proves nurses
  are the audience, so this is safe to say.
- **"personalized"** and **"gift"** are *product capabilities/claims* — they promise something about the
  physical product or how it is sold. Those need **your** proof, not a keyword.

**One verified concept can never verify a different concept.** Being *for* nurses does not make something
*personalized*, and it does not make it a *gift*.

## How atomic claims work now

Every claim is now broken into its smallest honest pieces ("atomic" = one idea per claim):

| Claim | Says only | State for your T2 sweatshirt |
|-------|-----------|------------------------------|
| Recipient | "For nurses." | ✅ VERIFIED (safe to publish) |
| Personalization | "Can be personalized" | ⛔ blocked — needs your proof |
| Gift | "Suitable as a gift" | ⛔ blocked — needs your proof |
| Decoration | "Embroidered / printed …" | ⛔ blocked — needs your proof |
| Material, color, size, fit, care … | physical facts | ⛔ blocked — needs your proof |

A claim can only be VERIFIED if **every idea inside it** is independently proven. If a sentence mixes a
proven idea with an unproven one, the whole sentence is blocked (`MIXED_EVIDENCE_BLOCKED`) and can never
be published. The system now refuses to be tricked by wording.

## How you unlock the blocked claims

Give the toolkit **real evidence** (a spec sheet, a photo of the real garment, the personalization
options you actually offer). For example:

- Confirm the fabric ("80% cotton, 20% polyester") → unlocks the material claim.
- Confirm you offer name/monogram personalization → unlocks the personalization claim.
- Confirm gift packaging (e.g. a real gift box) → unlocks the gift claim.
- Provide the embroidery/print method → unlocks the decoration claim.

Until then, those ideas stay out of your copy — which keeps you compliant.

## Why AI mockups do not count as proof

A rendered image or an AI-generated mockup is **not** evidence that the physical product has that
feature. The toolkit will not accept AI output, keyword text, the product title, competitor copy, or the
audience name as proof of a physical claim. Only a real fact or a real photo of the real product counts.

## Why your listing is still a safe draft

With zero verified physical facts, the only things provably true are the product identity (a sweatshirt)
and the audience (nurses). So the safe draft is intentionally minimal:

- **Title:** `Nurse Sweatshirt`
- **Description:** `A nurse sweatshirt. For nurses.`
- **Bullets / item highlights:** all deferred, each with the exact fact it is waiting for.

The one visible change from before is that the description now ends with **"For nurses."** — that is the
*atomic recipient claim* publishing safely on its own, now that it no longer drags "personalized gift"
along with it.

## Why backend search terms do not fill every byte

Amazon allows up to 249 bytes of hidden search terms. The toolkit used 246 and stopped — **not** because
it hit the limit, but because it ran out of terms that add real search value. Filling the last few bytes
with near-duplicates would not help you rank, so it left them empty on purpose. Every backend phrase we
kept (e.g. "icu nurse", "med surg nurse", "labor and delivery nurse") targets a distinct nurse specialty
— a genuinely different search — even though the word "nurse" repeats. We keep each phrase whole rather
than chopping it into fragments.

## What was regenerated

Phase 6A (claim evidence), Phase 6B (keyword allocation) and Phase 6C (the product detail page draft)
were all re-run from the corrected claim authority. The **paid keyword source was not touched**
(same file, same checksum), the tier counts are unchanged (74 / 378), and the run is byte-for-byte
repeatable.

## Why Phase 6D stays blocked

Phase 6D (A+ content and image planning) is **not** authorized yet. It should only start after you review
this safe draft and decide which owner facts to supply. Creative layers must sit on top of verified
claims — never invent them.
