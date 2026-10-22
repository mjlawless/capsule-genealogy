# BUG-024 — every Irish census record was one dedup identity (the record id is in the `#`)

**Status:** ✅ **FIXED 2026-10-22**, before any census record was captured. · **Severity:**
🟠 **MEDIUM** (latent): the second census capture ever made would have been refused.

**Found by:** the free-sites research for the supported-sites catalog, 2026-10-21. It was
confirmed against the live site on 2026-10-22.

## What happens

The National Archives of Ireland census search (1821–1851 fragments, 1901, 1911) links every
result to the record page with the record id in the URL **fragment**:

```
https://nationalarchives.ie/collections/search-the-census/census-record/#id=<record>&c20_year=1911
```

The page's own script reads `location.hash` to load the record. The page's "Link to record"
gives the same record with a query string instead: `census-record/?id=<record>&c20_year=1911`.

Capsule's dedup identity (`capsule/pipeline/dedup.py`, `_normalize_url`) keeps the query but
drops the fragment, which is right everywhere else: RootsIreland and IrishGenealogy carry the
record id in the query, and fragments are usually page anchors or viewer state. On this site
it reduced every record reached from search to `census-record/`. One census capture would
then block every later one as "already captured". A false block loses evidence, which
REQ-004 ranks as the worse failure.

## Fix

On `nationalarchives.ie` (with or without `www.`), at the census-record path, the identity
folds the query and fragment parameters into one query of `id` and `c20_year`. The search
link (`#id=`) and the "Link to record" form (`?id=`) are therefore one record, and two
different ids are two records. Fragments everywhere else are still dropped. The cited URL is
unchanged: only the dedup identity is affected.

## Verification

`tests/test_req_004_dedup.py`, under "BUG-024":
- two records reached from search are two identities;
- both forms of one record are one identity, including `www.`, extra parameters and an anchor;
- a stored census capture blocks its own record but not the next one;
- fragments on other pages of the site are still dropped.

There are no census captures in the corpus, so no stored data is affected.
