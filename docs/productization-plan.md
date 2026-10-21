# Capsule Productization Plan — installable by non-technical genealogists

**Status:** PLAN (approved direction: non-technical audience — owner, 2026-07-16)
**Scale:** milestone-level ("M1-productization"), several months, phased so each phase
is independently useful to the owner's own research.
**Supersedes:** the open pivot in the 2026-07-16 distributability discussion. The
audience question is resolved: **non-technical genealogists, double-click install.**

---

## 1. Goal and definition of done

A genealogist with no terminal skills can go from a download link to their first
verified capture, unassisted, in under 15 minutes:

1. Run a signed installer (Windows first). No terminal is ever shown.
2. A first-run page walks them through: getting/entering an Anthropic API key,
   installing the Chrome extension from the Web Store, and pairing happens
   automatically.
3. They open a record page, click the Capsule icon, click Capture, and see the
   result in their local collection index.

**Definition of done:** a real, non-technical genealogist (not the owner) completes
install → first capture on a clean Windows 11 machine with zero assistance, and the
evidence produced is byte-identical in quality to what the dev checkout produces.

## 2. Non-negotiable constraints (carried from the plan / CLAUDE.md)

- **Localhost-only backend stays.** Evidence never leaves the machine. This is the
  product's privacy promise, not a limitation to engineer away.
- **BYO Anthropic key.** No hosted proxy: no server costs, no key custody, no
  liability for other people's captures. The cost of this choice is that key
  acquisition becomes our hardest onboarding step (see §6, Phase 3).
- **Compliance posture unchanged:** user drives, page-in-view only, no background
  requests to providers, no credential handling, no bulk crawling. The installer
  changes *distribution*, never *behavior*.
- **Append-only evidence store; uninstall must never silently destroy captures.**

## 3. Current friction inventory (what we are removing)

| # | Today (dev checkout) | Non-technical replacement |
|---|---------------------|---------------------------|
| 1 | `git clone` + `uv sync` + Python 3.12 | Signed installer; PyInstaller-bundled backend |
| 2 | `ANTHROPIC_API_KEY` env var | Setup wizard → validated key → OS keyring |
| 3 | `capsule serve` in a terminal window | Tray app, auto-starts at login |
| 4 | Token printed → pasted into popup | Detect-and-pair handshake, stable token |
| 5 | Extension loaded unpacked in dev mode | Chrome Web Store listing |
| 6 | Version skew → opaque 422s (`[object Object]`) | Version handshake with plain-language message |
| 7 | Data in `./data` relative to CWD | Per-user app data dir (`platformdirs`) |

Friction is entirely in **install / config / pairing** — the capture core is done and
E2E-verified (v0.0.14). That is why this plan is mostly packaging and onboarding, not
pipeline work.

## 4. Target architecture of the installed product

```
┌─ Chrome ─────────────────────┐      ┌─ Capsule.exe (tray app) ──────────────┐
│ Capsule extension            │      │ tray icon: status / open collection / │
│  (Web Store, auto-updates)   │      │   pause / view log / quit             │
│  - probes /health            │◄────►│ FastAPI backend on 127.0.0.1:8765     │
│  - /pair handshake (once)    │ token│  - /health: version + min-ext-version │
│  - version check every popup │      │  - /pair: user-confirmed pairing      │
└──────────────────────────────┘      │ key: OS keyring (Credential Manager)  │
                                      │ data: %LOCALAPPDATA%\Capsule\         │
                                      │ auto-start: HKCU Run key              │
                                      └───────────────────────────────────────┘
```

No new GUI toolkit: the tray is `pystray` (Pillow is already a dependency), and every
"screen" (first-run wizard, settings, collection index) is a **localhost page served
by the existing FastAPI backend** and opened in the default browser. One UI stack,
already built, already token-gated.

## 5. Phasing overview

Each phase ships something the owner uses on real research the same week. Do not
start a phase until the previous one has passed its acceptance gate.

- **Phase 0 — Decisions & housekeeping** (days): resolve D1–D6, repo hygiene.
- **Phase 1 — Kill config friction** (~1–2 wks): `capsule setup`, keyring, data-dir
  fix, auto-pairing, version handshake. *Works from source; owner benefits now.*
- **Phase 2 — Backend becomes an app** (~2–4 wks): PyInstaller, tray, installer,
  signing.
- **Phase 3 — Extension distribution & onboarding** (~2–4 wks + store review
  latency): Web Store, first-run flow, key-acquisition guide.
- **Phase 4 — Sustain** (ongoing): auto-update, diagnostics, beta program, docs.

---

## Phase 0 — Decisions & housekeeping (prerequisite, days)

**H0.1 Repo hygiene.** `fix/record-transcript-provenance` is unmerged and there is
**no git remote**. Merge to `main`, create a private GitHub repo, push. Releases,
auto-update checks, and the Web Store listing all need a canonical home. (Public vs
private is D2; private is fine to start.)

**H0.2 Version discipline.** `pyproject.toml` says `0.0.0` while the extension is
`0.0.14`. Adopt one release version for the *product* (backend + extension released
together), surfaced in `/health` and the tray "About". Tag releases.

**Decisions the owner must make (D-list):**

- **D1 Name/branding.** Is "Capsule" the shipping name? (Collides with several
  existing products; a quick trademark/Web-Store search before the listing is cheap
  insurance.)
- **D2 License & repo visibility.** Open-source (GPL/MIT) vs freeware binary. Affects
  the update mechanism, the Web Store listing text, and whether `evals/` fixtures
  (real captured pages) must be scrubbed before publishing.
- **D3 Platform order.** Recommendation: **Windows first** (owner's machine, largest
  genealogist install base), macOS as a separate go/no-go after Phase 3.
- **D4 REQ-002 automation in the store build.** The one-call Ancestry image capture
  drives Ancestry's own UI (auto-click). Web Store review may read this as
  "automating a subscription site". Options: (a) keep and disclose plainly, (b) gate
  behind an "advanced" setting, (c) omit from the store build. Decide before the
  listing is written. My recommendation: (a) attempt with honest disclosure — it acts
  only on the user's click, on the page in view — and fall back to (b) if rejected.
- **D5 Budget.** Code signing (Azure Trusted Signing ~$10/mo or an OV cert
  ~$100–400/yr), Chrome Web Store developer fee ($5 once), optional Apple Developer
  ($99/yr, only if D3 adds macOS).
- **D6 Support channel.** Where do non-technical users report problems? (GitHub
  issues are hostile to this audience; an email address + the diagnostic bundle from
  Phase 4 is the minimum.)

---

## Phase 1 — Kill config friction (works from source today; ~1–2 weeks)

Highest leverage per the prior analysis: removes the env-var key and the token paste
before any packaging work, and everything here ships inside the Phase 2 binary
unchanged.

**P1.1 `capsule setup` wizard (CLI first, reused graphically in P2.3).**
- Checks environment, prompts for the Anthropic key, **validates it with a cheap live
  call** (1-token Haiku message), stores it in the **OS keyring** (`keyring` package →
  Windows Credential Manager / macOS Keychain).
- Key resolution order everywhere (`_ensure_api_key`, `AnthropicTransport`):
  explicit env var → keyring → error with the fix spelled out. Env var keeps working
  for dev/CI.
- Mints the stable pairing token (reusing the existing `serve` persistence logic,
  extracted from the CLI into a shared module).

**P1.2 Data directory moves to a per-user path.**
- Default becomes `platformdirs.user_data_dir("Capsule")` (e.g.
  `%LOCALAPPDATA%\Capsule\`) instead of relative `./data` — a tray app has no
  meaningful CWD, so this is a Phase 2 blocker fixed early. `CAPSULE_DATA_DIR` still
  overrides (dev keeps `./data` via env or a repo-local `.env`).
- The token file (`data/.capsule-token`, currently hardcoded relative in
  `cli/app.py:370`) moves with it — resolve it through `capsule/config.py` like the
  DB (this is exactly the BUG-003 shape; one answer to "where is the token").
- One-time migration prompt if a legacy `./data` exists: offer to move or point at it.

**P1.3 Detect-and-pair (replaces the token paste).**
- Extension popup probes `GET /health`. If unpaired, it calls `POST /pair`; the
  backend requires a **human confirmation** on the backend side (Phase 1: console
  prompt / 6-digit code shown by `capsule serve`; Phase 2: tray notification with
  Approve/Deny). On approval the backend returns the stable token; the extension
  stores it. A random web page cannot complete this: CORS stays locked to the
  extension origin, and pairing needs the user's out-of-band confirmation.
- Manual token paste stays as a fallback path in the popup (collapsed under
  "Advanced").

**P1.4 Version handshake.**
- `/health` gains `{backend_version, min_extension_version, extension_known_good}`.
  The popup compares on open and shows a plain-language banner ("Your Capsule app is
  older than the extension — update it here") instead of letting a capture 422.
  Once the extension auto-updates from the Web Store but the backend doesn't, skew
  becomes the *default* failure mode — this is the guard.

**Acceptance gate (P1):** on a fresh machine, from clone: `uv sync` →
`uv run capsule setup` → `uv run capsule serve` → load extension → open popup →
approve pairing → capture succeeds. **No env vars set, no token pasted.** Existing
owner workflow (env var + pasted token) still works. Tests: keyring resolution order,
pairing handshake (approve/deny/replay), version-skew responses, data-dir resolution
+ migration.

---

## Phase 2 — Backend becomes an app (~2–4 weeks)

**P2.1 PyInstaller build.** One-dir bundle → `Capsule.exe`. Known work: uvicorn app
must be passed as an object (the current `"capsule.backend.app:app"` string import in
`serve` breaks under a frozen bundle), pypdfium2/Pillow binary hooks, version stamped
into the exe metadata. CI job (GitHub Actions, Windows runner) builds it on tag.

**P2.2 Tray app.** `pystray` icon wrapping the server lifecycle: status (running /
port busy / key missing), Open my collection (browser → localhost index), Pause
capture, View log, Check for updates (Phase 4), Quit. Auto-start via HKCU `Run` key
(user-level, no admin). Single-instance guard (second launch focuses/notifies rather
than fighting over port 8765).

**P2.3 Graphical first-run.** If no key/keyring entry exists at startup, the tray
opens the browser to a backend-served `/setup` page — the P1.1 wizard with a form:
key entry → live validation with a clear failure message → stored in keyring → "Now
install the extension" step. No terminal, no new UI framework.

**P2.4 Windows installer (Inno Setup).** Installs app + Start Menu entry + autostart;
launches Capsule on finish, which opens first-run. **Uninstaller preserves
`%LOCALAPPDATA%\Capsule\` by default** and asks explicitly before deleting captures —
uninstalling software must not destroy a decade of evidence.

**P2.5 Code signing.** Azure Trusted Signing (recommended: cheap, no hardware token,
good SmartScreen standing) wired into CI. Accept unsigned builds for friendly-tester
betas only; signing is a hard requirement before any stranger downloads it.
Anticipate PyInstaller AV false positives: submit false-positive reports to
Microsoft/major AVs as they appear; one-dir (not one-file) builds reduce heuristic
flags.

**P2.6 macOS go/no-go.** Deferred decision, revisited after Phase 3 with real demand
data. (py2app/briefcase + notarization + $99/yr; tray → menu bar; keyring → Keychain
works as-is.)

**Acceptance gate (P2):** clean Windows 11 VM with no Python: double-click installer
→ tray icon appears → first-run page → key entered → extension (still load-unpacked
at this phase) pairs via tray Approve → capture succeeds → reboot → Capsule is
running again. No terminal at any point.

---

## Phase 3 — Extension distribution & onboarding (~2–4 weeks + review latency)

**P3.1 Web Store readiness audit — the `debugger` permission is the big rock.**
The manifest requests `debugger` (used by `render-pdf.js` for the page-PDF render).
This triggers the scariest install warning Chrome has ("can read and change all your
data") and near-automatic manual review. Options, in order of preference:
1. Replace the debugger-based PDF render with a non-debugger path if one exists that
   preserves fidelity (investigate `chrome.tabs.captureVisibleTab` stitching vs
   print-to-PDF alternatives — likely lossy; fidelity is an evidence property, so
   this needs an explicit quality comparison, not a silent swap).
2. Keep `debugger`, write a meticulous permission justification (it runs only on the
   user's explicit Capture click, only on the active tab), and accept review friction.
3. Make the page-PDF an optional capability so the core listing is clean.
Also: `<all_urls>` content scripts will need justification; consider narrowing or
moving to `activeTab`-triggered injection if capture UX allows.

**P3.2 Resolve D4 (REQ-002 automation) in the listing text.** Disclose exactly what
the auto-click does and that it acts only on user initiation.

**P3.3 Listing assets:** privacy policy page (easy to write honestly: *everything
stays on your machine; the only external call is to Anthropic with your own key*),
screenshots, demo GIF, description. Developer account ($5). Submit, iterate on
review feedback. **Fallback if rejected:** documented load-unpacked becomes the
"advanced install" path and we re-scope (unpacked is genuinely hostile to this
audience — treat rejection as a product-level event, not a docs problem).

**P3.4 End-to-end onboarding flow.** Installer finish → first-run page: Step 1 key,
Step 2 "Add Capsule to Chrome" (Web Store link; page polls `/pair` state so it can
show ✔ the moment pairing completes), Step 3 "Capture your first page" guided
walkthrough against a known-public record page.

**P3.5 Anthropic key acquisition guide.** For this audience, *getting* an API key is
the hardest single step we cannot remove (BYO-key is a constraint). Mitigate hard:
click-by-click illustrated guide (console.anthropic.com → billing → key), a
recommended $5–10 monthly spend limit, and plain per-capture cost expectations
(fractions of a cent to a few cents per capture) shown inside the wizard. Track
where beta users stall; this step will dominate drop-off.

**Acceptance gate (P3) = the product's definition of done (§1):** an unassisted
non-technical tester on a clean machine: download → install → key → store extension →
pair → first capture, under 15 minutes. Run this as a moderated observation with
2–3 real genealogists; every stall point becomes a fix before wider release.

---

## Phase 4 — Sustain (ongoing)

- **P4.1 Backend auto-update.** Tray "Check for updates" + daily check against GitHub
  Releases; download installer, verify signature, run on user confirmation. This
  closes the version-skew loop: the extension auto-updates via the store, so the
  backend must be able to keep up without a terminal.
- **P4.2 Diagnostics.** "Save diagnostic bundle" in the tray: recent log, versions,
  config (never the key, never capture content) → a zip the user can email (D6).
- **P4.3 Beta program.** 3–5 genealogists (e.g. a local genealogical society), a
  feedback loop, and the CLAUDE.md rule applied to productization: the next milestone
  is chosen by observed onboarding/usage pain, not speculation.
- **P4.4 Docs site.** Getting-started with screenshots, FAQ (key costs, "where is my
  data", "is anything uploaded"), troubleshooting. Can be a simple GitHub Pages site.

---

## 6. Risk register

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Web Store rejects `debugger` permission | High | High | P3.1 options; permission justification; optionalize the PDF render |
| Web Store objects to Ancestry automation (REQ-002) | Medium | Medium | D4: disclose → gate → omit ladder |
| API-key step defeats non-technical users | High | High | P3.5 guide; measure drop-off; long-term: revisit hosted/OAuth options only if this fails in practice |
| SmartScreen / AV false-positives on PyInstaller exe | Medium | Medium | Sign early (P2.5), one-dir build, submit FP reports, build reputation with steady release cadence |
| Version skew backend↔extension | Certain (post-store) | Medium | P1.4 handshake + P4.1 auto-update |
| Uninstall/upgrade destroys evidence | Low | Catastrophic | P2.4 preserve-by-default; upgrade never touches the data dir; test in the VM gate |
| Frozen-app breakage (uvicorn string import, binary deps) | Medium | Low | P2.1 known-issues list; CI smoke test boots the frozen exe and hits `/health` |
| Solo-maintainer sustain cost (review cycles, support) | Medium | Medium | Phase gates; beta kept small; D6 keeps support scoped |

## 7. Explicit non-goals (this milestone)

- No hosted service, no accounts, no telemetry (a local, opt-in diagnostic bundle is
  the only support channel).
- No Firefox/Edge port (Edge can sideload Chrome-store extensions; revisit on demand).
- No Gramps write endpoint (separately planned in `docs/gramps-integration.md`;
  deliberately not now, per owner).
- No changes to capture behavior, evidence model, or compliance posture.

## 8. Sequencing note vs. the research roadmap

This track competes for time with M2 (GEDCOM import / Matcher / lineage graph — the
Mayflower goal). Recommended interleave: **Phase 0 + Phase 1 now** (they pay off on
the owner's own daily use immediately), then decide whether Phase 2 precedes or
follows the next research-feature push. Phases 2–3 only make sense executed
back-to-back — a binary without a store extension still isn't installable by the
target audience.
