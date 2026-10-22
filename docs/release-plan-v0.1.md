# Release plan — v0.1.0, the first public open-source release

**Status:** ✅ **RELEASED 2026-10-21** — https://github.com/mjlawless/capsule-genealogy (public, tag `v0.1.0`). Written the same day. The owner handed over direction the same day
("take control, move the project forward, I defer to your recommendations") with the goal of an
end-user-installable open-source project on GitHub, shareable on LinkedIn and genealogy forums.

**Relates to:** [`productization-plan.md`](productization-plan.md) (the long road: installer, tray
app, Web Store), [`design/public-distribution.md`](design/public-distribution.md) (why the backend
stays local).

## What v0.1.0 is, and is not

v0.1.0 is an **early-access, install-from-source** release for genealogists comfortable following a
step-by-step guide. It is honest about that in the README:

- Install Python tooling (`uv`), download the repo, run `capsule setup`, then `capsule serve`.
- Load the Chrome extension **unpacked** (no Web Store listing yet).
- **Bring your own Anthropic API key**, with the expected cost per capture stated plainly.
- The extension uses Chrome's `debugger` permission (for the page PDF render) and, on Ancestry, can
  click the site's own Download button **only when you click Capture**. Both are disclosed.

Not in v0.1.0: a signed installer, a tray app, a Chrome Web Store listing, auto-update. Those are
productization-plan Phases 2–4, and they need money (code signing, the Web Store fee) and an explicit
owner yes.

## Decisions taken under the owner's deference (2026-10-21)

Each is **decided 2026-10-21 under the owner's blanket deference, and reversible.** Three things are
NOT covered by the deference and wait for an explicit yes at the publish checkpoint: making anything
public, spending money, and the final public name.

| # | Decision | Choice | Why |
|---|---|---|---|
| D1 | Name | **Keep "Capsule"** for the package and CLI; repo name **TBD at the publish checkpoint** after a collision search (candidate: `capsule-genealogy`). | A rename touches every import; the repo name is what people search for. Confirmed by the owner before announcing, since a public rename is effectively permanent. |
| D2 | License and visibility | **MIT**, public. | Maximum reuse in a hobbyist community; GPL-compatible, so the planned in-process Gramps adapter (GPL-2.0-or-later) stays possible. |
| D3 | Platforms | **macOS and Windows from source** for v0.1 (both work with `uv`). Installers later, Windows first per the productization plan. | The owner now runs macOS; the from-source path is the same on both. |
| D4 | Ancestry auto-click (REQ-002) | **Keep and disclose** in the README. | It acts only on the user's click, on the page in view (the compliance posture). |
| D5 | Budget | **$0 for v0.1.** No signing, no Web Store. | Nothing in v0.1 needs it. |
| D6 | Support channel | **GitHub Issues + Discussions** for v0.1, with a plain-language "how to report a problem" section. | Early adopters from source can use GitHub; an email route comes with the installer. |
| — | REQ-011 shape | **Lifecycle event**, one new append-only table, `superseded_by` derived in the index. | Re-confirmed under the deference after the 2026-10-21 premise correction. |
| — | REQ-010 ledger | Decided when REQ-010 is built; recorded in its spec. | |
| — | Repo strategy | **New public repo with fresh history** built from the cleaned tree; the private repo stays as the archive of the full development history. Public commits use the GitHub noreply address. | 152 commits of evolving docs: one missed personal detail in history would be permanent. |

## Waves

1. **Bugs and requests** (one code change at a time; tests, Codex adversarial review, then commit):
   BUG-021 (real versioning, prerequisite for releases) → BUG-020 → BUG-019 → REQ-010 → REQ-011 →
   BUG-023 (docs).
2. **Minimum installability**: a per-user data directory via `platformdirs` with a `CAPSULE_DATA_DIR`
   override that keeps the owner's existing `./data` working; `capsule setup` storing the API key in
   the OS keyring; confirm the existing extension↔backend version handshake; token paste kept, with
   clear docs. Must not strand the owner's launchd service or its 475 captures (BUG-003's shape).
3. **Release prep**: act on the pre-publication audit (living people, gated content, owner
   identifiers, secrets); LICENSE, end-user README and install guide, CONTRIBUTING, SECURITY,
   CHANGELOG, issue templates; CI that really runs the node tests (no silent `needs_node` skips);
   an extension zip; a public-facing CLAUDE.md, with a scrub-before-filing rule for inbox triage.
4. **Publish checkpoint (owner yes required)**: name and repo, visibility, then push, tag v0.1.0 and
   create the GitHub release.

## Progress (2026-10-21)

- **Wave 1 — done.** BUG-021, BUG-020, BUG-019, REQ-010, REQ-011 and BUG-023, each tested,
  mutation-checked and Codex-reviewed.
- **Wave 2 — done.** Per-user data folder (an existing `./data` is kept), `capsule setup`
  with the OS keyring, the token beside the database.
- **Wave 3 — done.** Public README, LICENSE, CONTRIBUTING, SECURITY, CHANGELOG, issue
  templates, cross-platform CI, a privacy scrub of the shipped files (behaviour-neutral:
  identical Python ASTs apart from docstrings), and `release/export-public.sh`. The exported
  tree installs from scratch and passes 995 tests (5 corpus-dependent skips).
- **Wave 4 — done.** Owner rulings: publish public now; repo `capsule-genealogy`; the Ancestry
  work queue ships OFF (`CAPSULE_ENABLE_WORK_QUEUE=1` enables it; set in the owner's local
  `.env`); the 1903/1905 NYC eval fixtures stay; license holder "the Capsule authors". Public
  repo created with one fresh commit (GitHub noreply author), tag `v0.1.0`, release with
  `capsule-extension-0.1.0.zip`, Discussions and private vulnerability reporting on. First CI:
  Linux, macOS and Windows all green (Windows 999 passed, 4 corpus skips).

## Releasing again

The private repo stays the development repo. For the next release:

1. Bump the product version in `pyproject.toml` and `extension/manifest.json` together (a test
   enforces they match), add a `CHANGELOG.md` entry, commit, Codex-review.
2. `git clone https://github.com/mjlawless/capsule-genealogy.git <dir>`, then replace its
   tracked contents with a fresh `release/export-public.sh <empty dir>` tree (copy over, keep
   `.git`), so the public history grows one commit per release rather than restarting.
3. Commit as the GitHub noreply address, tag `vX.Y.Z`, push, `gh release create` with the
   extension zip **and the double-click installers** (since 0.2.1; the popup's first-run screen
   and the README link to them as `releases/latest/download/<name>`, so every release must
   carry them):
   `installers/package-installers.sh <dir>` builds `Install-Capsule.cmd` and
   `Install-Capsule-mac.zip`; attach both. Then check both links answer 200:
   `curl -fsSIL https://github.com/mjlawless/capsule-genealogy/releases/latest/download/Install-Capsule.cmd`
   (and the same for `Install-Capsule-mac.zip`).
4. Follow-up owed: make the Windows CI job blocking (drop `continue-on-error`), since it passed.


## Owner-only live checks, collected

These need the owner's browser and logins, so they are batched here rather than scattered:

- [ ] **BUG-022:** reload the extension (v0.0.23+), make one armed capture on a Newspapers.com page
      image; expect `image_status: full`.
- [ ] **REQ-009:** one live run on Ancestry 62073:223848 image 153 (a record's second image).
- [ ] **v0.1.0 extension:** reload the extension once (it is now 0.1.0 and sends its version and
      the reason for a missing image), make one ordinary capture, and check the manifest has
      `extension_version`.
