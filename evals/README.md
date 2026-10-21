# Extraction evals

Real captured pages + hand-labeled expected extractions. The guardrail that a
prompt or model change never silently regresses extraction on the owner's real
records.

## Layout

Each fixture is one folder under `fixtures/<name>/`:

| File            | Committed? | What it is |
|-----------------|------------|------------|
| `expected.json` | yes        | Hand-labeled gold — the `RawExtraction` a correct run must produce (`record_type`, `personas`, `assertions`). |
| `meta.json`     | yes        | Provenance: `{ "url", "gating": "public"\|"gated", "record_type"?, "notes"? }`. |
| `snapshot.html` | **no** (gitignored) | The captured page. Raw pages may be gated/copyright/PII (plan §11), so they stay local — re-capture to seed one. |

Because `snapshot.html` is local-only, evals **run where the owner holds the
captures**, not in CI. CI still validates that every committed `expected.json` /
`meta.json` is schema-valid (`tests/test_evals.py`).

## Run

```bash
ANTHROPIC_API_KEY=... uv run capsule eval            # all fixtures
ANTHROPIC_API_KEY=... uv run capsule eval --only nyc-death-1903-mary-lawless
```

Each fixture makes the single extraction call the pipeline makes on its page and
scores the model's output against the gold, field by field (matched / missing /
extra / mismatched). `value` and `evidence_type` are compared exactly (verbatim
transcription is sacred); `snippet` / `normalized_value` are recorded but not yet
scored. The command exits non-zero if any runnable fixture regresses, so it can
gate a prompt change.

## Grow the set

When a capture is corrected in the accept/correct UI (M1), promote it here:
copy its `snapshot.html`, write the corrected extraction as `expected.json`, and
add `meta.json`. Each correction becomes a permanent regression guard.
