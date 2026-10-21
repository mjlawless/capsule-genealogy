# BUG-008 — The media grabber over-reaches, and saves a JavaScript file as `application/pdf`

**Status:** ✅ **FIXED — VERIFIED 2026-08-24** (2026-07-14, `419e879`)
> **Both NYC certificates re-captured clean, 51 seconds apart.** `419e879` landed **20:31:41
> UTC**; extension v0.0.10 needed a reload, which is why 20:33 still shows junk:
>
> | time (UTC) | capture | media held |
> |---|---|---|
> | 20:33:22 — pre-reload | `…c6b3ad15` | record.pdf, certificate, **`doris-logo.png`**, **`pdfobject-min.pdf`** |
> | 20:35:58 — after reload | `…4cfca63f` | **record.pdf + the certificate. Nothing else.** |
> | 20:36:49 — after reload | `…40f557bf` | **record.pdf + the certificate. Nothing else.** |
>
> **The over-reach is gone corpus-wide:** no capture after 2026-07-15 grabs a `/static/` asset or
> a `pdfobject` file, and all 22 later NYC captures hold exactly two files.
>
> **The mistyping half, byte-checked 2026-08-24:** every stored media file was re-sniffed against
> its magic bytes — **376 files, 3 mismatches, all three the pre-fix `3-pdfobject-min.pdf`
> artifacts** in captures banked *before* `419e879`. **Zero post-fix mismatches.** The
> inert-storage path is visible too: `bc-v4-min.bin` / `anchor.bin` are stored under `.bin`,
> never under a type they are not. The three stale artifacts are **deliberately left in place**
> — the store is append-only, and they are this bug's own evidence. · **Severity:** 🟡 **MEDIUM — one half is cosmetic, the other is an evidence-integrity defect**

> ## FIXED — 2026-07-14
>
> **You were right that the mistyping is the real defect, and right about why.** But the root
> cause is not the regex — it is that **the backend, which is the trust boundary, believed the
> filename and Content-Type a page handed it.** Those are hints from an untrusted source. The
> magic bytes are the file.
>
> So `_decode_media` now **sniffs the bytes and stores every media object under what it
> actually is.** Unrecognized bytes are stored **inert** (`.bin`, `application/octet-stream`)
> rather than under a type they might not be — declaring `application/pdf` over unknown bytes
> IS the lie. The correction runs in **both directions**: a real PDF mislabelled as a JPEG is
> still archived as a PDF, so a bad label can never lose a record. And a contradiction is
> **reported**, not silently corrected, because a mistyped file usually means the grabber took
> something that is not the record at all — which is exactly what happened here.
>
> **The cosmetic half, with your rule adopted verbatim:** the detector matched `/pdf` anywhere
> in a URL (hence a directory called `pdfobject`) and took any large image. Now the **path**
> must name a PDF, and **a record does not live in a static-asset directory** — the certificate
> is a signed blob on a different host entirely, while the logo and the plugin sit under
> `/static/` on the page's own host. That single rule catches both halves, as you said it would.
>
> Regressions: `test_BUG_008_a_javascript_file_is_never_stored_as_a_pdf` (stored inert, never
> as a PDF, and the contradiction is surfaced) and `test_BUG_008_a_real_pdf_mislabelled_as_an_image_is_stored_as_a_pdf`.
> 588 tests green.
>
> **Re-capture the two NYC certificates** (extension **v0.0.10**, reload it): the folders should
> now hold the certificate and nothing else — no logo, no JavaScript. The existing captures keep
> their (correct) certificate; only the junk was junk.
**Found:** 2026-07-14, immediately after the BUG-007 fix (v0.0.10)
**Captures:** `20260714-a860historicalvitalrecordsnycgov-145cbb38` (birth cert) and `…-<marriage cert>`

---

## The BUG-007 fix works — this is fallout from it

The certificate file is now correctly captured (`media/1-b-k-1887-0011687.pdf`, the SAS-signed blob,
a **2172×1030** scan). **That part is a clean success.** But the grabber also took two things it
should not have:

```
media/2-doris-logo.png        page:embedded   <- the site's LOGO
media/3-pdfobject-min.pdf     page:embedded   <- pdfobject.min.js  (!)
```

## 🔴 The real defect: a `.js` file is stored as a PDF

```
$ file media/3-pdfobject-min.pdf
media/3-pdfobject-min.pdf: JavaScript source, ASCII text
```

but the manifest says:

```
path       : media/3-pdfobject-min.pdf
mime       : application/pdf
source_url : https://a860-historicalvitalrecords.nyc.gov/static/js/plugins/pdfobject/pdfobject.min.js
provenance : page:embedded
```

**The source URL ends in `.js`.** It was fetched, renamed to `.pdf`, and declared
`mime: application/pdf`.

This is the **exact failure class CLAUDE.md §12.4 was written for** — *"a checksum proves integrity,
not identity… an evidence image that is not an image is not evidence."* A media object whose declared
type contradicts its magic bytes will pass every hash check and every "file exists" check, and is
precisely the thing that produced a false PASS in this project once before.

**Fix:** validate **magic bytes against the declared MIME before storing**, and reject the mismatch
(or store it under its true type). A file whose URL ends `.js` should never become
`application/pdf`.

## 🟡 The cosmetic half: the site's own furniture is archived as evidence

`doris-logo.png` is the NYC DORIS **site logo**. It is chrome, not a record. Archiving it is harmless
but it pollutes the capture, and at scale (nearly every NYC follow-up hits this site) it means a logo
in every folder.

**Fix:** exclude obvious site furniture — same-origin `/static/`, `/assets/`, `/img/` paths, and
known logo/sprite names. A record file comes from the **record host** (here, the signed
`dorisarchive.blob.core.windows.net` blob), not from `.../static/img/`.

A cheap, principled rule that would catch both at once:

> **Prefer media whose host differs from the page host, or whose path does not sit under a static
> asset directory.** The certificate is on `dorisarchive.blob.core.windows.net`; the logo and the JS
> plugin are on `a860-historicalvitalrecords.nyc.gov/static/…`.

## Not a blocker

The certificates were captured correctly and are usable evidence today. This is about keeping the
capture folder **honest and clean** — a folder that contains a logo and a mistyped JavaScript file
alongside the record invites exactly the confusion the manifest exists to prevent.


---

# ✅ RE-CAPTURED AND VERIFIED — 2026-07-14. FIXED.

*(First attempt showed no change — `capsule serve` was still running the pre-fix backend. Restarted
and reloaded; the process, not the code, was stale. Worth noting: a running-version mismatch is
invisible from outside — the toast said `verify OK, PDF ✓` both times.)*

Both NYC certificates re-captured. The folders now hold **the certificate and nothing else**:

```
0-record.pdf              553 KB   the page render      (legitimate)
1-b-k-1887-0011687.pdf    264 KB   THE CERTIFICATE      (the evidence)
```

**Gone:** `doris-logo.png`, and `pdfobject-min.pdf` — the JavaScript file that had been stored under
`mime: application/pdf`.

**The deeper fix is the right one.** "The backend, which is the trust boundary, believed the filename
and Content-Type a page handed it" — that is the correct diagnosis, and a better one than mine. I
called it a mistyping bug; it was a *trust* bug. Sniffing the bytes and storing unknown data **inert**
(rather than under a type it might not be) is exactly the §12.4 discipline: **declaring
`application/pdf` over unknown bytes IS the lie.** And correcting in *both* directions — so a real PDF
mislabelled as an image is still archived as a PDF — means a bad label can never lose a record.
