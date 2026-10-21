# BUG-006 — The pixel threshold false-FAILS small documents, and mislabels them "canvas"

**Status:** ✅ **FIXED — VERIFIED 2026-08-24** (2026-07-14, `2268f8e`)
> **The re-mine happened, and the ledger straddles the fix commit.** `2268f8e` landed
> **20:05:11 UTC**. Record `1002:17153430` — the very card this bug was filed about:
>
> | time (UTC) | capture | `image_status` |
> |---|---|---|
> | 19:25:42 — **before the fix** | `…33614a5e` | **`degraded-canvas`** — an `ancestry:download` measuring **1815×1200**, the exact false-fail |
> | 20:27:12 — **after the fix** | `…24750b42` | **`full`** |
>
> Same record, same provenance, opposite verdict, 61 minutes apart across the commit. **The card
> left the queue** — which this bug named as the actual harm — and `capsule query --apid
> 1002:17153430` now returns `ledger: mined`. Corpus-wide, **no `ancestry:download` is classified
> `degraded` in any capture after the fix**. · **Severity:** 🟠 **HIGH — rejects genuinely full-resolution scans and jams them in the queue**

> ## FIXED — 2026-07-14. Your fix, adopted as specified.
>
> **This was my error, and the diagnosis names it exactly.** I over-applied the BUG-001
> lesson. "Measurement beats the label" was right for a blank PDF, where the label carried
> no information — only pixels could tell the truth. Here the label carries the *decisive*
> information, and I threw it away:
>
> - an **`ancestry:download` IS the full resolution** — it is what the provider serves as the
>   original, and there is nothing better to be had, whatever its pixel count;
> - a **`viewer:canvas` fallback is screen-resolution by construction**, and is the only
>   genuinely degraded source we have.
>
> As you put it: a bare pixel count cannot distinguish *small document, fully scanned* from
> *big document, downscaled*. The 445px gap between the draft card (1815) and the canvas
> (1370) has no safe threshold in it, and the next collection would land inside it.
>
> **Implemented exactly as prescribed:**
> - provenance classifies (`full` / `degraded-canvas`); `MIN_FULL_PX` is **gone**;
> - pixels demoted to a **floor** — `MIN_USABLE_PX` 500 → **800**, which clears the 600px
>   record-page thumbnail (now correctly *thumbnail*, not *degraded* — your cosmetic point)
>   and the 96px raw one, and cannot false-FAIL a real scan;
> - the warning is named for what it IS ("a CANVAS capture"), not for its size.
>
> Regressions: `test_BUG_006_a_small_document_downloaded_at_full_resolution_is_full_not_degraded`
> (your named test) and an end-to-end one proving the card now **leaves the queue**, which
> was the actual harm. 582 tests green.
>
> **Re-mine `1002:17153430`** — it should come back `full` and mined. Any other records
> parked as `degraded-canvas` are worth re-running too; they may have been complete all along.
**Found:** 2026-07-14, first production run
**Record:** `1002:17153430` — U.S. WWII Draft Registration Card, 1942

---

## Symptom

```
$ capsule ancestry status
  UNMINED 1002:17153430 — unmined (degraded-canvas)
```

But the image it holds is:

```
media/1-44544-10-00038-00915.jpg   provenance = ancestry:download   1815 x 1200
```

**Two things are wrong:**

1. **It is not a canvas.** `provenance` is `ancestry:download` — it came from Ancestry's own Download
   feature. Calling it `degraded-canvas` is simply false, and it sends the reader hunting for a
   fallback that never happened.
2. **It is not degraded.** It is the **full resolution Ancestry holds for this collection.**

## Proof — I read it

Cropped and rendered the top of the card. Every field is crisp, in colour:

> **REGISTRATION CARD** — (Men born on or after April 28, 1877…)
> **SERIAL NUMBER: [redacted]** · **NAME: [redacted]**
> **PLACE OF RESIDENCE: [address]**
> **MAILING ADDRESS: Same**

A WWII draft card is a **physically small document**. It does not need 4,000 px to be a complete,
archival-quality scan. The gate rejected it only because a *census sheet* happens to be bigger.

## Root cause

`capsule/pipeline/image_check.py`:
```python
MIN_FULL_PX = 2000   # below this it is a screen-resolution rendering
...
return max(self.width, self.height)
```

Using the long edge was right (a WWI card is 3013 x **1496** — a short-edge or both-edges test would
have failed *that* too). **But a bare pixel count cannot distinguish "small document, fully scanned"
from "big document, downscaled."** They are different facts and pixels do not carry the difference.

### Measured, across the run

| record | long edge | reality |
|---|---|---|
| 1930 census | 4354 | full |
| 1900 census | 3960 | full |
| 1920 census | 3900 | full |
| 1910 census | 3768 | full |
| WWI draft card | 3013 | full |
| **WWII draft card** | **1815** | **FULL — and rejected** |
| viewer canvas (the real degraded case) | 1370 | degraded |
| record-page thumbnail | 600 | thumbnail |
| thumbnail fetched raw | 96 | thumbnail |

The gap between a legitimate small scan (1815) and a canvas (1370) is **445 px**. There is no safe
threshold in that gap, and there never will be — the next collection will land inside it.

## The fix — classify by PROVENANCE, not by size

**The download IS the full resolution, by definition.** It is what Ancestry serves as the original;
there is nothing better to be had. So:

```
provenance == 'ancestry:download'  -> full          (this is the authoritative image)
provenance == 'canvas'             -> degraded-canvas   (opt-in fallback; the ONLY degraded case)
no image, index-only               -> index-only-no-image
image expected, none arrived       -> failed
```

Keep pixels only as a **sanity floor**, to catch a download that came back as a thumbnail:
`< ~800 px long edge -> thumbnail -> failed`. That still catches the 600 px record-page thumbnail and
the 96 px raw one, and it cannot false-FAIL a real scan.

**Also: raise `MIN_USABLE_PX` 500 -> 800.** The record-page thumbnail is 600 px, so at 500 it is
currently labelled *degraded* rather than *thumbnail*. Both are non-mining, so it is cosmetic — but
the message should be true.

## Why this matters beyond one card

The requirements said the ledger's job is to make constraint 4 true: *we must never believe we hold a
record we do not hold.* This bug is the **mirror image** — we **do** hold the record, in full, and the
tool says we do not. It leaves a completed record on the queue forever, exactly the way the SSDI used
to (BUG-004, path A), and an operator will re-mine it endlessly to no effect.

Small documents are not the edge case: draft cards, index cards, passenger-list fragments, headstone
photos and obituary clippings are a large share of a typical tree.

## Regression test

```python
def test_BUG_006_a_small_document_downloaded_at_full_resolution_is_full_not_degraded():
    """Regression: a 1815x1200 ancestry:download (a WWII draft card) is FULL.

    It was classified 'degraded-canvas' - wrong twice: it is not a canvas, and it is
    not degraded. It is the full resolution Ancestry holds. Provenance decides;
    pixels are only a floor.
    """
```


---

# ✅ RE-TESTED BY THE REPORTER — 2026-07-14. FIXED.

Re-captured the WWII draft card (`1002:17153430`) after the fix.

```
BEFORE:  UNMINED 1002:17153430 - unmined (degraded-canvas)     <- FALSE FAIL
AFTER :  19 mined / 19 known                                   <- the whole ancestor is complete
         image_status = full   (1815x1200, provenance ancestry:download)
```

**The same image. The same pixels.** What changed is that the tool now believes the *provenance*
rather than the *pixel count* — the download **is** the full resolution, by definition, and a
physically small document does not owe us 4,000 px.

**And the census sheets still classify `full`, the canvas still classifies degraded.** The fix did not
loosen the gate; it stopped the gate lying about small documents.

**Consequence for the run:** the pilot person is now **19 mined / 19** — the first person
with every record captured, images and all.
