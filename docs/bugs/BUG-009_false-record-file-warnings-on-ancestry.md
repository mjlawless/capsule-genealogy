# BUG-009 — False "RECORD FILE NOT CAPTURED" warnings on Ancestry (the sidebar thumbnails)

**Status:** ✅ **FIXED — VERIFIED 2026-08-24** (2026-07-14, `bf42fc0`)
> **Verified 2026-08-24 by sweeping every Ancestry capture in the collection.** The sidebar sweep
> — the same 8 files (`4639160-00470.jpg`, `not-thumbnail.png`, `32849-b094199-00448.jpg`…,
> byte-for-byte identical across unrelated records, i.e. other people's "Suggested records") —
> appears in **exactly 4 captures, all inside a 7-minute window on 2026-07-14 (23:19–23:25)**,
> before the v0.0.11 reload this bug asked for.
>
> **It never recurs. The 92 Ancestry captures banked after 23:25:51 — through 2026-08-07 — hold
> 1 or 2 media items each, and not one has more than two `page:embedded` files.** The
> nine-files-where-one-is-real mess is gone, and the RECORD FILE NOT CAPTURED warning is
> trustworthy again. · **Severity:** 🟡→🟠 **the escalation is right: it WRITES junk, not just warns**

> ## FIXED — 2026-07-14. Your ideal fix, adopted verbatim.
>
> **When a capture is a known Ancestry record (an `apid` is present), the generic in-page
> media sweep is skipped entirely** (`extension/content.js`). The scan comes from the armed
> Download; the DOM sweep can only add the record-page thumbnail, the "Suggested records"
> sidebar (other people's records), and spacer PNGs — exactly the nine-files-where-one-is-real
> mess you measured. None of it is written now, and the false "RECORD FILE NOT CAPTURED"
> alarms are gone with it.
>
> This keeps that warning trustworthy, which was the real stakes: it is the one that catches
> "we hold a render, not the record" (BUG-007), and a warning that cries wolf is worse than
> no warning.
>
> Ordinary pages that genuinely EMBED their record (a NYC certificate blob) are untouched —
> they carry no `apid`, so the sweep still runs for them.
>
> No JS test harness exists in the repo, so this is verified by inspection + your re-capture.
> **Re-capture an image-backed Ancestry record (v0.0.11, reload):** the folder should hold
> `0-record.pdf` and the one real scan, and nothing else. And the toast on an index-only
> record should carry no "RECORD FILE NOT CAPTURED" line.
**Found:** 2026-07-14, first record of the second pilot run
**Introduced by:** the BUG-007 media detector

---

## Symptom

Capturing `61778:2091482` — *New York, New York, Index to Death Certificates* — an **index-only**
record with **no record image at all**. The toast:

```
Captured death (index to death certificates) — 24 assertion(s), verify OK,
PDF ✗ RECORD FILE NOT CAPTURED — this capture holds a page render, not the record:
     4639160_00470.jpg — Capsule has no access to https://www.ancestry.com …
     m-t0627-02687-00254.jpg — Capsule has no access …
     4313899-00824.jpg — Capsule has no access …
     32849_b094199-00448.jpg — Capsule has no access …
```

**Four "RECORD FILE NOT CAPTURED" alarms on a record that has no record file.**

## What those files actually are

They are the thumbnails in Ancestry's **"Suggested records"** sidebar — *other people's records*,
offered as hints. They are not this record. They are not any record we asked for.

(You can tell at a glance: `4639160_00470` is the 1930 census image, `m-t0627-02687-00254` is 1940,
`32849_b094199-00448` is the NY 1925 state census — all from the first pilot person's *other* records, which
Ancestry is helpfully recommending.)

## The data is CORRECT — only the toast is wrong

Verified on both an index-only record and an image-backed one:

| | index-only (`61778:2091482`) | image-backed (`7602:48906067`) |
|---|---|---|
| ledger `image_status` | ✅ `index-only-no-image` | ✅ `full` |
| follow-up recorded | ✅ NYC Dept of Records | — |
| manifest `media_warnings` | ✅ **0** | ✅ 0 |
| media written | ✅ `0-record.pdf` only | ✅ `0-record.pdf` + the real 1.9 MB census scan — **no sidebar junk** |

**Nothing bad was written.** The classification, the ledger and the media are all right. It is purely
the extension-side toast that fabricates the alarm.

## Why this still matters

**"RECORD FILE NOT CAPTURED" is the most important warning Capsule has.** It exists to catch exactly
the failure that BUG-007 was — *we hold a render, not the record*. It is the warning an operator must
never learn to ignore.

Firing it four times, in red, on a record that correctly has no image **teaches the operator to
ignore it**. The next time it is real, it will be dismissed as noise. A warning that cries wolf is
worse than no warning.

And it will fire on **every** Ancestry record page, because every one has a Suggested-records sidebar.

## Fix

1. **The detector must not treat sidebar/suggested thumbnails as candidate record files.** On
   Ancestry the record image is reached through the **image viewer** and arrives via
   `ancestry:download` — the detector does not need to hunt the DOM for it at all. Consider: when an
   `apid` is present (i.e. this is a known Ancestry record capture), **skip the generic media
   detector entirely** and rely on the download path, which already works.
2. **Do not warn about a file we never wanted.** A "record file not captured" warning should be
   emitted only for a media candidate we actually intended to keep — not for every image on the page
   we failed to fetch.
3. The host-permission message (*"Capsule has no access to https://www.ancestry.com. Open the popup
   and capture once to grant it"*) is **correct and useful** — but it should not be attached to a
   false record-file alarm. Right now the two are welded together.

## Note

The `started` ledger event from the BUG-005 fix is working — an in-flight capture is now visible.
Good.

---

## UPDATE 2026-07-14 — ESCALATE: it does not just WARN, it WRITES the junk to disk

Severity was framed as "the data is correct; only the toast is wrong." That was true for an
index-only record. On an **image-backed** record with the host grant in place, the detector actually
**writes every sidebar thumbnail into the capture folder as media.** For `7884:119680425`
(`20260714-ancestrycom-8c0ccfe8`), `media/` held **9 files**:

| file | dims | what it is |
|---|---|---|
| `9-4449171-00576-1.jpg` | 3914×2748 | ✅ the real full-res record scan (Ancestry Download) |
| `1-4449171-00576.jpg` | 600×421 | a 600px thumbnail of the SAME record (page preview) |
| `2/3/4/6/7/8-*.jpg` | ~600px | six "Suggested records" sidebar thumbnails (other records) |
| `5-not-thumbnail.png` | 600×600 | a placeholder/spacer PNG |

So the real scan is 1 of 9 files, distinguishable only by being the sole full-resolution one. The
downstream Genealogy applier must pick "the largest image" to attach — fragile. **The detector must
not treat the page thumbnail, the sidebar suggestions, or spacer PNGs as record media** when an
`apid` capture already has its scan from the armed Download. Ideal: when the armed Download supplies
the record image, write ONLY that; skip the generic in-page media sweep entirely on Ancestry.
