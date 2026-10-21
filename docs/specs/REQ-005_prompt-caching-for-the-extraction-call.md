# REQ-005 — Prompt caching for the extraction call

**Status:** ✅ RESOLVED — **not implemented**. §1 gate failed; §4 option 1 (do nothing) taken.
**Priority:** 🟡 MEDIUM (cost optimization; no correctness impact)
**Filed:** 2026-07-19, Genealogy workspace (cross-project API prompt-caching sweep)
**Resolved:** 2026-07-19 (Capsule session)

> **Do not add `cache_control` to this call site.** The stable prefix is ~2,700–3,300
> tokens against Opus 4.8's **4,096-token** minimum cacheable size. The parameter
> would be a silent no-op: nothing errors, `cache_creation_input_tokens` just stays 0
> forever. See §1 for how the gate was resolved and §4 for why the alternatives were
> rejected. §2 and §3 are retained as the implementation spec **should the prefix ever
> grow past 4,096 for reasons of extraction quality** — not as pending work.

## Why

Anthropic bills cached prefix reads at **~0.1×** the input-token price. Writes cost
**1.25×** (5-minute TTL), so break-even is two requests against the same prefix.
Capsule's extraction call re-sends a large, byte-identical system prompt on every
capture, which is exactly the shape caching exists for.

The pacing helps: `store/ledger.py:41` sets `DEFAULT_PACE_SECONDS = 45` (cap
`DEFAULT_DAILY_CAP = 300`, line 43). 45 s between records sits comfortably inside
the 5-minute TTL, so while working through a queue of records the cache would stay warm
across consecutive records. `capsule eval` is a tighter back-to-back loop and
would benefit more.

## What the sweep found (all figures ESTIMATED from outside — see §1)

| | |
|---|---|
| Call site | `capsule/pipeline/pipeline.py:115` — `agent.run(prompt.user, system=prompt.system)`, "the single model call" |
| Transport | `capsule/agent/anthropic_transport.py:57` — `messages.create(**request)` |
| Model | `claude-opus-4-8` (`capsule/agent/base.py:33`, `DEFAULT_MODEL`) — **minimum cacheable prefix 4,096 tokens** |
| System prompt | `capsule/pipeline/extract.py:186-333`, frozen module-level literal, returned byte-identical by `build_extraction_prompt()` (`extract.py:356-376`) |
| Size | **9,575 chars ≈ 2,736 tokens** at chars/3.5 — **below the 4,096 floor** (char count re-measured 2026-07-19; unchanged) |
| Tools | none sent (`anthropic_transport.py:49-53`) — nothing renders ahead of `system` |
| Silent invalidators | **none** — no datetime, UUID, session id, conditional sections, or f-string interpolation in the prefix. All volatile data (URL, HTML) is correctly confined to the user message. |

Render order is `tools → system → messages`. With no tools, and with the user
message opening on the volatile URL (`extract.py:371-375`), **the cacheable
region is exactly the system prompt** — nothing before it, nothing after it joins.

**Ceiling, for expectations:** per-call input is dominated by the trimmed HTML,
not the prompt (`trim.py:3` cites a 35.7 KB page at ~60% reduction ≈ 4K tokens).
Even a fully working system-prompt cache addresses only roughly **40% of input
tokens** on a typical capture. This is a modest win, not a step change.

## 1. Measure the real prefix size — RESOLVED: below the floor

**Outcome: the prefix does not clear 4,096. Gate fails. → §4 option 1.**

The `chars / 3.5` heuristic is not reliable within 50% of a hard threshold, which
is why this section existed. But the gate is **binary at 4,096**, and the binary
answer does not require the exact count:

- 9,575 chars would have to tokenize at **2.34 chars/token** to reach 4,096.
- Claude tokenizers run **3.5–4.2 chars/token** on English prose. The JSON schema
  block at the end of the prompt is denser than the prose, but it is a minority of
  the text.
- Worst case, applying the newer Opus 4.7/4.8 tokenizer's documented inflation
  (up to ~1.35× an older baseline) to the chars/3.5 estimate gives **~3,700** —
  still short, and that is the adversarial bound, not the expectation.
- Realistic range: **2,700–3,300 tokens.** Margin to the floor is ~20–35%.

No density any Claude tokenizer produces on this text clears the floor.

**The live count was not run** — this environment has no `.env`, no
`ANTHROPIC_API_KEY`, and no `ant` CLI. There is no valid offline substitute:
`tiktoken` is OpenAI's tokenizer and undercounts Claude by 15–20%+ on prose and
considerably more on structured text. If a key is ever at hand and someone wants
the exact number for the record, this is the correct snippet — note the signature,
which the original draft of this REQ got wrong:

```python
import anthropic
from capsule.pipeline.extract import _SYSTEM_PROMPT

client = anthropic.Anthropic()
# Count `system` against a minimal user message; subtract that message's own
# contribution (a few tokens) to isolate the cacheable prefix.
print(client.messages.count_tokens(
    model="claude-opus-4-8",
    system=_SYSTEM_PROMPT,
    messages=[{"role": "user", "content": "x"}],
).input_tokens)
```

> `build_extraction_prompt` takes **one positional PageSnapshot**
> (`extract.py:356`), reading `.url` and `.html` — not `url=` / `html=` keywords.
> The snippet in the original draft raised `TypeError` before measuring anything.
> Counting `_SYSTEM_PROMPT` directly avoids needing a snapshot at all.

**Decision gate (for reference if the prompt ever changes materially):**

- **≥ 4,096** → caching works as-is. Go to §2.
- **< 4,096** → `cache_control` is inert. Go to §4. Do **not** add the parameter
  "for later" — a marker that silently does nothing is worse than none, because
  the next reader assumes caching is on.

## 2. If it clears the floor — implement

> Retained as spec only. The gate failed (§1); nothing here is pending work.

**One breakpoint, on `system`.** No tools are sent, so there is no tools/system
split to reason about and no second breakpoint worth placing.

**Do not reach for top-level auto-caching here.** `cache_control={"type":
"ephemeral"}` passed to `messages.create()` attaches to the *last cacheable block*
in the request — which on this call is the **user message**, carrying the volatile
URL and trimmed HTML. That keys the cache to content that differs on every capture:
a fresh write each call, never a read. The explicit block-list form on `system`
below is the only correct placement for this request shape.

The transport currently passes `system` as a plain string
(`anthropic_transport.py:54-55`):

```python
if system is not None:
    request["system"] = system
```

`cache_control` attaches to a content block, not a bare string, so this becomes
the block-list form:

```python
if system is not None:
    request["system"] = [
        {"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}
    ]
```

Keep the default 5-minute TTL. `ttl: "1h"` doubles the write premium to 2× and
buys nothing here — 45 s pacing is already well inside 5 minutes.

Suggested shape: gate it behind a transport constructor flag
(`AnthropicTransport(cache_system=True)`) defaulting to on, so §3 can A/B the
same code path and `FakeTransport` parity stays trivial.

## 3. Verification (mandatory — a parameter is not a result)

> Retained as spec only (gate failed), **except the accounting caveat at the end of
> this section, which stands on its own.** `input_tokens` reports the uncached
> remainder regardless of whether Capsule ever enables caching — any future usage
> dashboard or cost log that sums `input_tokens` alone is wrong the moment caching
> appears anywhere. That belongs somewhere more durable than a rejected REQ.

**A `cache_control` key present in the request is not evidence that anything was
cached.** This REQ is not satisfiable by code review.

1. Issue **two** captures with the same system prefix inside the TTL.
2. Assert `cache_read_input_tokens > 0` on the **second**.
3. Record the observed counter values in this file.

Zero on the second call means either the prefix is under the floor (→ §1 was
measured wrong or the prompt changed) or something is invalidating it — diff the
rendered prompt bytes between the two requests to find it.

**The instrumentation already exists.** `_USAGE_FIELDS`
(`anthropic_transport.py:20-25`) already carries `cache_read_input_tokens` and
`cache_creation_input_tokens` through into `TransportResult`. Nothing new is
needed to observe the result — just assert on it.

**Accounting caveat:** `input_tokens` reports only the *uncached remainder*. Once
caching works, any dashboard or log summing `input_tokens` alone will show a
sudden drop that looks like a metrics regression. Total prompt size is
`input_tokens + cache_creation_input_tokens + cache_read_input_tokens`. Fix the
accounting in the same change.

## 4. It does NOT clear the floor — options, ranked → **option 1 taken**

1. **✅ Do nothing — TAKEN.** The win is capped at ~40% of input tokens on a call
   that runs ≤300×/day (`DEFAULT_DAILY_CAP`). Even assuming the prefix *did* clear
   the floor and the cache were always warm, that is ~$5/day at the hard cap and a
   fraction of that at one researcher's actual pace — against a 45s-paced queue
   that realistically runs tens of captures a day, not 300. It does not justify
   contorting the extraction prompt.
2. **Grow the prefix past 4,096 with genuinely useful content** (~+1,400 tokens,
   ~+50%). Legitimate *only* if the added content earns its place on extraction
   quality — e.g. more worked examples, sharper evidence-grading instructions.
   **Padding to clear a billing threshold is not worth it** — but the reason is
   margin, not sign. The arithmetic (Opus 4.8 @ $5/M in, 1.25× write, 0.1× read):

   | | per call |
   |---|---|
   | Baseline, 2,736 tok uncached | $0.0137 |
   | Grown to ~4,136 tok, cache **miss** (write) | $0.0259 |
   | Grown to ~4,136 tok, cache **hit** (read) | $0.0021 |

   Break-even is a **~51% cache-hit rate**. Sustained 45s-paced runs would clear
   that; one-off captures and cold eval starts would not. Above break-even the net
   gain is on the order of **$0.002/call** — noise — while filler in an extraction
   prompt is a live risk to the extraction quality that is the entire point of the
   system. Reject on margin-vs-risk, not on the earlier claim that padding "would
   cost more in uncached tokens than the cache would ever return," which is only
   true below ~51% and was overstated as absolute in the original draft.
3. **Switch the extractor to `claude-sonnet-4-6`** (2,048 floor — the prompt
   clears it as-is). **Not recommended.** This is a model-quality decision about
   evidence-grade extraction being made for a cost discount. Capsule's output
   feeds citations in a tree whose whole value is provenance; a cheaper
   extraction that is subtly worse is a bad trade. If it is ever considered, it
   needs an eval-set comparison, not a caching argument.

## Non-goals / notes

- **Do not add `cache_control` speculatively.** See §1's decision gate — which
  this REQ resolved as FAILED. This is now settled, not open.
- **Do not use top-level auto-caching as a "simpler" substitute.** It attaches to
  the user message on this call shape and would never produce a read. See §2.
- **Do not reorder the user message for this alone.** `extract.py:371-375` puts
  the volatile URL *ahead* of the static instruction line; static-first would
  extend the cacheable region, but it is ~60 chars and only matters if the prefix
  already clears the floor. Fold it into §2 if that happens; otherwise ignore.
- **`capsule eval` would benefit most** if §2 ever landed — a tight fixture loop
  against one frozen prompt is the ideal cache shape. Moot today: the floor is a
  property of the prompt and the model, not of the workload, so a sub-4,096 prefix
  fails to cache in the eval loop exactly as it does in `capture`.
- The sweep found **no** existing `cache_control` anywhere in the project, and no
  other Anthropic call site: `anthropic_transport.py:57` is the only one, reached
  from `backend/app.py:135-149` (`provide_agent`, wired to `POST /capture`),
  `cli/app.py:69-77` (`capsule capture`), and `cli/app.py:487` (`capsule eval`).

## Acceptance — met

- [x] §1 gate resolved in writing. Prefix is **2,700–3,300 tokens** against a
      **4,096** floor; no Claude tokenizer density clears it. Exact live count not
      run (no API key available in this environment) and not required — the gate is
      binary and the margin is 20–35%. Method and the corrected snippet are in §1.
- [x] §4 option chosen and recorded: **option 1, do nothing.** Reasoning in §4.
- [x] **No `cache_control` left in the codebase.** Verified: the sweep found none,
      and none was added — `anthropic_transport.py:54-55` still passes `system` as
      a bare string.
- [x] Carried forward independently of this decision: the **`input_tokens`
      accounting caveat** (§3). It is a live trap for any future usage dashboard
      whether or not caching ever lands, so it should not die with this REQ — see
      the note at the head of §3.

### Re-open this REQ if

- The extraction system prompt grows materially past ~11,500 chars for
  **extraction-quality** reasons (never to chase the threshold — see §4 option 2).
  At that point re-run §1's count; if it clears 4,096, §2 and §3 are the spec.
- The extractor's model changes to one with a lower floor **on its own merits**
  (Sonnet-tier is 2,048) — but see §4 option 3: that is an evidence-quality
  decision needing an eval-set comparison, never a caching argument.

---

<details>
<summary>Original acceptance criteria (superseded)</summary>

- §1 measured, exact token count recorded in this file, decision gate resolved
  in writing.
- If §2 implemented: two consecutive captures show
  `cache_read_input_tokens > 0` on the second, with observed values recorded
  here; usage accounting includes the cache fields; tests cover the block-list
  `system` shape and `FakeTransport` parity.
- If §4 chosen: the option taken is recorded here with its reasoning, and no
  `cache_control` is left in the codebase.

</details>
