# Design note: `field` vs `normalized_field` — a Contract 1 change

Status: **shipped 2026-07-09.** All eight scope items below landed. Spec addendum:
`docs/specs/capture-folder-v1.md` §4c. One item was deliberately **not** built —
see "What is still only prompt-enforced" at the end.

## How this was found

The first ever live run of `capsule eval` failed:

```
[FAIL] nyc-death-1903-mary-lawless  matched=6 missing=1 extra=1
  MISSING    Mary Lawless::death_place: expected 'Queens' [stated]
  EXTRA      Mary Lawless::borough: got 'Queens' [stated]
```

The value is identical. The model and the gold disagree about what to *call* the
field. Two problems fell out of that.

### 1. The gold fixture asserts a fact the source never states

The NYC page prints `Borough: Queens`. The string `death_place` appears nowhere on
it. The hand-labeled gold recorded `death_place: 'Queens' [stated]` — asserting, as
a **stated** fact, an inference: that the borough where a certificate was *filed* is
where the person *died*. Usually true for NYC vitals. Not stated by the record.

That is the exact failure mode this project exists to prevent, and it was sitting in
our only piece of ground truth. The model was more faithful to the record than the
label was.

### 2. Field names are unconstrained, and the model is inconsistent *within one call*

In the same response, on the same page, the model turned `Type` into `record_type`
and `Date` into `death_date` — normalizing — while copying `Borough` straight
through. Two naming policies, one call. `field` is free-text `str`, so nothing
prevents it.

This breaks more than the eval:

- **Eval scoring** keys on `persona::field`, so any synonym is a false failure.
- **The M2 matcher** will compare assertions across sources by field name.
- **The phase-2 tree push** needs canonical names to map onto GEDCOM.

And the contract is asymmetric. `Assertion` has `verbatim_value` **and**
`normalized_value`. It has **no** `normalized_field`. Values get the
verbatim/normalized split CLAUDE.md demands; field names get nothing, so the model
improvises.

### A control run, and what it did and did not show

One eval with the payload trim disabled (`trim_for_extraction` bypassed, everything
else identical):

| | trim ON | trim OFF |
|---|---|---|
| name, age, record_type, death_date, certificate_number | matched | matched |
| Borough / `Queens` | called it `borough` | called it `death_place` |
| **soundex `L420`** | **extracted** | **omitted entirely** |

Both scored 6/7, on different fields. The untrimmed run's failure is the worse one:
it silently dropped a value that is on the page. **No evidence the trim harms
extraction**; if anything the shorter prompt was more complete.

But n=1 each, and Opus 4.8 rejects `temperature` (sampling parameters were removed),
so runs cannot be made deterministic. Two runs differing on two fields is consistent
with ordinary variance. **Do not attribute either difference to the trim.** The
defect that *is* proven is the within-call inconsistency above, which needs no second
run.

Do not buy more control runs to characterise noise we are about to eliminate. Fix the
field naming first — it removes the degree of freedom — then measure stability.

## The change

Apply the project's own rule to field names, exactly as it already applies to values.

```
field:            "borough"       <- the label the record prints
normalized_field: null            <- our canonical term, when it is a synonym
verbatim_value:   "Queens"
normalized_value: null
evidence_type:    "stated"        <- the page DOES state the borough
```

- **`field`** is the label as printed, mechanically snake_cased by our code (so
  `Date of Marriage` and `date_of_marriage` converge). The model copies; it does not
  invent.
- **`normalized_field`** is optional and carries the canonical genealogical term.

### The rule that keeps `normalized_field` honest

**`normalized_field` is for synonyms, not inferences.**

`Type` → `record_type` and `Date` → `death_date` name the same fact in different
words. `Borough` → `death_place` asserts *more* than the label does. `normalized_value`
is safe because `"46 y"` → `"46"` adds no meaning; `normalized_field` **can** add
meaning, and that asymmetry needs the rule.

> If the canonical field would assert more than the printed label does, leave
> `normalized_field` null. If you want that claim, make it a separate assertion
> typed `calculated`, with an annotation explaining the derivation.

Without this rule we rebuild the same bug one field over.

## Scope

1. `capsule/contracts/evidence.py` — `Assertion.normalized_field: Optional[str]`.
2. `capsule/store/schema.sql` — `normalized_field TEXT` on `assertion`.
3. `capsule/store/repository.py` — **an additive `ALTER TABLE assertion ADD COLUMN`
   guarded by a `PRAGMA table_info` check.** This is a real blocker, not a detail:
   `schema.sql` uses `CREATE TABLE IF NOT EXISTS`, so adding a column there does
   **not** alter an existing `capsule.db`. SQLite silently skips the create, the
   column never appears, and the first insert fails with `no such column`. The
   `reindex-from-folder` utility that would let us just rebuild the DB does not exist
   (tracked in the internal backlog). `ADD COLUMN` touches no rows, so the append-only triggers are
   unaffected.
4. `capsule/pipeline/extract.py` — `RawAssertion.normalized_field`; prompt states the
   field rule, the synonym-not-inference rule, and a **completeness** instruction
   (the control run silently dropped `soundex`).
5. `capsule/pipeline/build.py` — pass through; snake_case `field` mechanically.
6. `docs/specs/capture-folder-v1.md` — additive addendum, as for `media[]` and
   `external_refs[]`. Omitted when null, so existing captures keep their
   `content_hash`.
7. `evals/fixtures/nyc-death-1903-mary-lawless/expected.json` — rewrite all seven
   assertions to page labels: `name, soundex, age, type, borough, date, certificate`.
   `death_place` is **dropped**, not relabelled — the fixture should encode what the
   page says; inference belongs to the matcher.
8. `capsule/evals/harness.py` — add a **value-level** metric. The current score keys
   on `persona::field`, so the trimmed run (which captured every value on the page)
   scored the same 6/7 as the run that lost one. "Did we get all seven values,
   whatever they are called?" is the metric that reflects evidence quality. Offline,
   costs nothing.

## What is still only prompt-enforced

**The eval does not score `normalized_field`.** If the model emits
`field: "borough", normalized_field: "death_place"`, nothing in the harness objects.
The synonym-not-inference rule is enforced by the system prompt, by the pydantic
contract's docstring, and by a human reading the pair — not by a test.

That is a deliberate omission, not an oversight. Scoring it means the gold must
enumerate every synonym the model is permitted to propose, and an omitted-but-
optional key becomes a failure. With **one** fixture, **one** live run of evidence
about what this model actually does, and no `temperature` control on Opus 4.8, that
would bake a flake into the only instrument we have. Score it once several runs have
shown which synonyms the model reliably proposes.

Two guards did land, and both are cheap:

- `test_committed_gold_asserts_only_labels_the_page_prints` — every gold `field`
  must appear inside its own verbatim `snippet`. This is exactly what would have
  caught the original `death_place` fixture. It is a guard, not a proof: a short
  label could coincidentally occur in a snippet it does not name.
- The **value-level metric** (`values=N/M`), blind to field names. Verified against
  the two real runs: relabelling `Borough` scores `fields 6/7, values 7/7`; dropping
  `soundex` scores `fields 6/7, values 6/7`. Before this, both read as 6/7.

## After it lands

Re-run the eval two or three times on the fixed prompt. That is the run worth paying
for: it measures whether field naming is now stable, on a prompt that *can* be stable.
Watch `values=N/M` rather than the field count — the prompt now also carries a
completeness instruction, which is the thing the dropped `soundex` was evidence for.
