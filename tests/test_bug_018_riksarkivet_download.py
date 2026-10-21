"""BUG-018 — Riksarkivet: the armed capture never received the viewer's own download.

On `sok.riksarkivet.se/bildvisning/...` the researcher armed, clicked the viewer's own
"Full image 6256 x 4992px (jpg)", and Chrome saved the file. The capture still reported
"no download arrived within 20s". The viewer's config (in the saved snapshot of
`20260929-sokriksarkivetse-8bcfc652`) serves every download from `lbiiif.riksarkivet.se`,
a SIBLING of the armed `sok.riksarkivet.se`. The dotted-suffix correlation rule
(REQ-007 §2) accepts parent/child hosts only, and `SIBLING_HOSTS` had an entry for Ancestry
alone. So the download was refused.

It was refused SILENTLY, which is the second defect. The listener returned without a
trace, and the 20-second message then blamed the site ("did the site's own Download
actually start?") for a download that had started, finished, and been turned away by us.

These tests run the shipped `download-intercept.js` in node against a fake `chrome` API and
a fake clock, so the real listener → stage → take path is exercised, not a restatement. Downloads land
before the take here; the in-flight interleaving is not simulated.
The live E2E (one armed capture on a `bildvisning` page returning `image_status: full`) is
the owner's.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

_EXT = Path(__file__).resolve().parent.parent / "extension"
_INTERCEPT = _EXT / "download-intercept.js"

needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")

# A fake `chrome` (storage.session, downloads, extension) and a fake clock: `setTimeout`
# fires at once and advances `Date.now`, so the 20s stage wait costs nothing. Each scenario
# gets a fresh module and a fresh store.
_HARNESS = r"""
const path = process.argv[1];
const scenario = process.argv[2];

let now = 1_800_000_000_000;
Date.now = () => now;
// Events scheduled at a clock time fire (and are awaited) when a poll's sleep crosses it,
// so a download can land IN THE MIDDLE of takeStagedDownload's wait.
const scheduled = [];
function at(t, fn) { scheduled.push({ t, fn }); }
global.setTimeout = (fn, ms) => {
  now += ms || 0;
  const due = scheduled.filter((e) => e.t <= now);
  for (const e of due) scheduled.splice(scheduled.indexOf(e), 1);
  due.reduce((p, e) => p.then(e.fn), Promise.resolve()).then(fn);
  return 0;
};

const store = {};
const items = {};
let listener = null;
let fetched = 0;
global.chrome = {
  storage: { session: {
    get: async (k) => ({ [k]: store[k] === undefined ? undefined : JSON.parse(JSON.stringify(store[k])) }),
    set: async (o) => { for (const [k, v] of Object.entries(o)) store[k] = JSON.parse(JSON.stringify(v)); },
    remove: async (k) => { for (const key of [].concat(k)) delete store[key]; },
  } },
  downloads: {
    onChanged: { addListener: (fn) => { listener = fn; } },
    search: async ({ id }) => (items[id] ? [items[id]] : []),
  },
  extension: { isAllowedFileSchemeAccess: async () => true },
};
// `deferFetch` holds each file read until the scenario releases it, so two downloads can be
// mid-read at once (the staging race).
let deferFetch = false;
const pendingReads = [];
global.fetch = async () => {
  fetched += 1;
  const resp = { arrayBuffer: async () => new Uint8Array([255, 216, 255]).buffer };
  if (!deferFetch) return resp;
  return new Promise((resolve) => pendingReads.push(() => resolve(resp)));
};
const flush = async () => { for (let i = 0; i < 50; i++) await new Promise((r) => setImmediate(r)); };

console.warn = () => {};
const m = require(path);
m.watchDownloads();

function landAsync(id, item) {
  items[id] = {
    id, filename: "/Users/x/Downloads/" + id + ".jpg", bytesReceived: 3,
    startTime: new Date(now).toISOString(), ...item,
  };
  return listener({ id, state: { current: "complete" } });
}

async function land(id, item) {
  items[id] = {
    id, filename: "/Users/x/Downloads/" + id + ".jpg", bytesReceived: 3,
    startTime: new Date(now).toISOString(), ...item,
  };
  await listener({ id, state: { current: "complete" } });
}

let armHost = "";
async function arm(host, apid) {
  armHost = host;
  return m.armDownload({ key: "K", tabId: 1, host, profile: m.armProfileFor({ apid, host }) });
}
// Collect as the arming tab, from the armed page, unless a scenario says otherwise.
function take(key = "K", tabId = 1, host = armHost) {
  return m.takeStagedDownload(key, tabId, host);
}

const scenarios = {
  // The bug itself: armed on sok., the export comes from lbiiif.
  async riksarkivet() {
    const a = await arm("sok.riksarkivet.se");
    await land(1, { url: "https://lbiiif.riksarkivet.se/arkis!C0021166_00210/full/max/0/default.jpg", mime: "image/jpeg" });
    const t = await take();
    return { armed: a, provenance: t.media && t.media.provenance, skipped: t.skipped || null, fetched };
  },
  // A stranger host during the armed window: refused and never read. What the PAGE is
  // told must not reveal that it happened (compare with nothingLanded).
  async stranger() {
    await arm("www.archion.de");
    await land(1, { url: "https://cdn.example.org/unrelated.pdf", mime: "application/pdf" });
    const t = await take();
    return { media: !!t.media, skipped: t.skipped || null, fetched };
  },
  async nothingLanded() {
    await arm("www.archion.de");
    const t = await take();
    return { media: !!t.media, skipped: t.skipped || null, fetched };
  },
  // A page OFF Ancestry that names an apid must not get the ancestry profile, so the
  // researcher's own Ancestry download is neither claimed, nor read, nor deleted.
  async hostileApid() {
    const a = await arm("evil.example", "1,7602::56510272");
    await land(1, { url: "https://mediasvc.ancestrycdn.com/v2/image/x.jpg", mime: "image/jpeg" });
    await land(2, { url: "https://www.ancestry.com/imageviewer/api/media/x.jpg", mime: "image/jpeg" });
    const t = await take();
    return { armed: a, media: !!t.media, fetched };
  },
  // A refusal must not block the real download that lands after it.
  async refusedThenAccepted() {
    await arm("www.archion.de");
    await land(1, { url: "https://cdn.example.org/unrelated.pdf", mime: "application/pdf" });
    await land(2, { url: "https://www.archion.de/export/scan.pdf", mime: "application/pdf" });
    const t = await take();
    return { media: !!t.media, provenance: t.media && t.media.provenance, skipped: t.skipped || null, fetched };
  },
  // The armed site, but not an image or PDF: refused, and said so.
  async wrongMime() {
    await arm("www.archion.de");
    await land(1, { url: "https://www.archion.de/viewer/page.html", mime: "text/html" });
    const t = await take();
    return { media: !!t.media, skipped: t.skipped || null, fetched };
  },
  // A refusal belongs to ITS armed window. One from an expired arm must not be reported
  // against a later arm of the same key.
  async staleRefusal() {
    await arm("www.archion.de");
    await land(1, { url: "https://www.archion.de/viewer/page.html", mime: "text/html" });
    now += 61_000; // past ARM_TIMEOUT_MS: the first arm has expired
    const again = await arm("www.archion.de");
    const t = await take();
    return { armedAgain: again.ok, skipped: t.skipped || null };
  },
  // A download the researcher started BEFORE the arm, finishing inside the window, is theirs.
  async preStarted() {
    const started = new Date(now - 120_000).toISOString();
    await arm("sok.riksarkivet.se");
    await land(1, { url: "https://lbiiif.riksarkivet.se/arkis!X/full/max/0/default.jpg", mime: "image/jpeg", startTime: started });
    const t = await take();
    return { media: !!t.media, downloadId: t.downloadId ?? null, fetched };
  },
  // Central authorization: no caller may arm a profile off that profile's own domains.
  async profileOffSite() {
    const bad = await m.armDownload({ key: "K", tabId: 1, host: "evil.example", profile: "ancestry" });
    const noHost = await m.armDownload({ key: "K2", tabId: 1, host: "", profile: "ancestry" });
    const good = await m.armDownload({ key: "K3", tabId: 1, host: "www.ancestry.com", profile: "ancestry" });
    return { bad: bad.ok, noHost: noHost.ok, good: good.ok };
  },
  // Expiry must not make the page-visible outcome depend on unrelated downloads: capture
  // after the arm expired, with and without some other download in between...
  async expiredWith() {
    await arm("evil.example");
    now += 61_000;
    await land(1, { url: "https://www.example.net/mine.pdf", mime: "application/pdf" });
    const t = await take();
    return { skipped: t.skipped || null };
  },
  async expiredWithout() {
    await arm("evil.example");
    now += 61_000;
    const t = await take();
    return { skipped: t.skipped || null };
  },
  // ...and with the arm expiring DURING the capture's wait, the other download landing after.
  async midPollWith() {
    await arm("evil.example");
    const t0 = now;
    now += 50_000;
    at(t0 + 62_000, () => land(1, { url: "https://www.example.net/mine.pdf", mime: "application/pdf" }));
    const t = await take();
    return { skipped: t.skipped || null, elapsed: now - t0 };
  },
  async midPollWithout() {
    await arm("evil.example");
    const t0 = now;
    now += 50_000;
    const t = await take();
    return { skipped: t.skipped || null, elapsed: now - t0 };
  },
  // The real export landing mid-wait is still received (the poll path, not just land-first).
  async landsMidPoll() {
    await arm("sok.riksarkivet.se");
    const t0 = now;
    at(t0 + 3_000, () => land(1, { url: "https://lbiiif.riksarkivet.se/arkis!X/full/max/0/default.jpg", mime: "image/jpeg", startTime: new Date(t0 + 1_000).toISOString() }));
    const t = await take();
    return { media: !!t.media, skipped: t.skipped || null };
  },
  // Another tab naming the same key must not collect (or disturb) this tab's download.
  async crossTab() {
    await arm("sok.riksarkivet.se"); // tab 1
    await land(1, { url: "https://lbiiif.riksarkivet.se/arkis!X/full/max/0/default.jpg", mime: "image/jpeg" });
    const thief = await take("K", 2);
    const noTab = await take("K", null);
    const owner = await take("K", 1);
    return { thief: thief.skipped || "GOT MEDIA", noTab: noTab.skipped || "GOT MEDIA", owner: !!owner.media };
  },
  // The arming tab navigates to an attacker page, which then asks for the staged download.
  async sameTabNavigated() {
    await arm("www.ancestry.com", "1,7602::56510272");
    await land(1, { url: "https://mediasvc.ancestrycdn.com/v2/image/x.jpg", mime: "image/jpeg" });
    const attacker = await take("K", 1, "evil.example");
    const owner = await take("K", 1, "www.ancestry.com");
    return { attacker: attacker.skipped || "GOT MEDIA", owner: !!owner.media };
  },
  // The old Ancestry flow: arm in the viewer, capture from the record page. Same tab, same
  // site, different document. It must keep working.
  async ancestryViewerThenRecord() {
    await arm("www.ancestry.com", "1,7602::56510272");
    await land(1, { url: "https://mediasvc.ancestrycdn.com/v2/image/x.jpg", mime: "image/jpeg" });
    const t = await take("K", 1, "www.ancestry.com");
    return { media: !!t.media, provenance: t.media && t.media.provenance };
  },
  // An attacker page arms for ITS OWN host, then starts a download at its own address
  // that redirects to a provider file. The final host decides, so it is not claimed.
  async redirectOut() {
    await arm("evil.example");
    await land(1, { url: "https://evil.example/go", finalUrl: "https://mediasvc.ancestrycdn.com/v2/image/x.jpg", mime: "image/jpeg" });
    const t = await take();
    return { media: !!t.media, downloadId: t.downloadId ?? null, fetched };
  },
  // Two exports complete together and are both mid-read. The first is collected; the
  // second must not resurrect the consumed arm, nor land in a NEW arm made meanwhile.
  async stagingRace() {
    const riks = { url: "https://lbiiif.riksarkivet.se/arkis!X/full/max/0/default.jpg", mime: "image/jpeg" };
    await arm("sok.riksarkivet.se");
    deferFetch = true;
    const p1 = landAsync(1, riks);
    const p2 = landAsync(2, riks);
    await flush();
    pendingReads.shift()();
    await p1;
    const first = await take();
    // Re-arm for a different capture before the second read finishes.
    const rearm = await m.armDownload({ key: "K2", tabId: 1, host: "sok.riksarkivet.se", profile: "riksarkivet" });
    pendingReads.shift()();
    await p2;
    const afterFirst = await take();
    const second = await take("K2");
    return {
      first: first.downloadId ?? null,
      rearm: rearm.ok,
      afterFirst: afterFirst.skipped || "GOT MEDIA",
      second: second.media ? second.downloadId : null,
    };
  },
  // Same race without a re-arm: the stale copy must not bring the consumed arm back.
  async stagingRaceNoRearm() {
    const riks = { url: "https://lbiiif.riksarkivet.se/arkis!X/full/max/0/default.jpg", mime: "image/jpeg" };
    await arm("sok.riksarkivet.se");
    deferFetch = true;
    const p1 = landAsync(1, riks);
    const p2 = landAsync(2, riks);
    await flush();
    pendingReads.shift()();
    await p1;
    const first = await take();
    pendingReads.shift()();
    await p2;
    const again = await take();
    return { first: first.downloadId ?? null, again: again.skipped || ("GOT MEDIA " + again.downloadId) };
  },
  // Two captures from the arming tab collect at the same instant: exactly one gets it.
  async doubleTake() {
    await arm("sok.riksarkivet.se");
    await land(1, { url: "https://lbiiif.riksarkivet.se/arkis!X/full/max/0/default.jpg", mime: "image/jpeg" });
    const [a, b] = await Promise.all([take(), take()]);
    return { got: [a, b].filter((x) => x.media).length, other: (a.media ? b : a).skipped || null };
  },
  // Nothing armed: the researcher's own downloads leave no trace at all.
  async unarmed() {
    await land(1, { url: "https://cdn.example.org/mine.pdf", mime: "application/pdf" });
    return { keys: Object.keys(store), fetched };
  },
};

scenarios[scenario]().then(
  (r) => console.log(JSON.stringify(r)),
  (e) => { console.error(e && e.stack || e); process.exit(1); },
);
"""


def _run(scenario: str) -> dict:
    proc = subprocess.run(
        ["node", "-e", _HARNESS, str(_INTERCEPT), scenario],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip())


@needs_node
def test_BUG_018_a_riksarkivet_export_from_lbiiif_is_received():
    """The reported case: armed on sok.riksarkivet.se, the file comes from lbiiif."""
    r = _run("riksarkivet")
    assert r["armed"] == {"ok": True}
    assert r["skipped"] is None, r["skipped"]
    assert r["provenance"] == "site:official-download"
    assert r["fetched"] == 1


@needs_node
def test_BUG_018_a_download_from_another_site_is_invisible_to_the_page():
    """Refused, never read, and not even ACKNOWLEDGED to the page.

    The capture's reason reaches the page, and any page can arm. So the page-visible
    outcome must be identical whether or not the researcher downloaded something from
    elsewhere during the window (security review + Codex adversarial review, 2026-09-29).
    The host goes to the service-worker console only.
    """
    r = _run("stranger")
    quiet = _run("nothingLanded")
    assert r["media"] is False
    assert r["fetched"] == 0, "a refused download must never be read"
    assert r["skipped"] == quiet["skipped"]
    assert "cdn.example.org" not in r["skipped"]
    # ...and the timeout no longer claims flatly that nothing arrived: it says where to look.
    assert "service-worker console" in r["skipped"]


@needs_node
def test_BUG_018_a_made_up_apid_off_ancestry_cannot_claim_an_ancestry_download():
    """Codex adversarial review, 2026-09-29: pre-existing, and preserved by the first draft.

    The apid comes from the page event. It used to select the ancestry profile on its own,
    so any page could arm with an invented apid and claim, read and (after the capture)
    DELETE the researcher's next Ancestry download.
    """
    r = _run("hostileApid")
    assert r["armed"] == {"ok": True}  # it may arm for its OWN host...
    assert r["media"] is False  # ...but never claims Ancestry's file
    assert r["fetched"] == 0


@needs_node
def test_BUG_018_a_refusal_does_not_block_the_real_download_after_it():
    r = _run("refusedThenAccepted")
    assert r["media"] is True, r["skipped"]
    assert r["provenance"] == "site:official-download"
    assert r["fetched"] == 1, "only the accepted download is read"


@needs_node
def test_BUG_018_a_non_image_download_from_the_armed_site_is_reported():
    r = _run("wrongMime")
    assert r["media"] is False
    assert r["fetched"] == 0
    assert "text/html" in r["skipped"]


@needs_node
def test_BUG_018_a_refusal_from_an_expired_arm_is_not_reported_later():
    r = _run("staleRefusal")
    assert r["armedAgain"] is True
    assert "text/html" not in r["skipped"]
    assert "did the site's own Download actually start" in r["skipped"]


@needs_node
def test_BUG_018_a_download_started_before_the_arm_is_never_claimed():
    """Codex adversarial review, second pass: pre-existing. A download the researcher had
    already started, finishing inside the window, was claimed, and cleanUpDownload then
    deleted their file."""
    r = _run("preStarted")
    assert r["media"] is False
    assert r["downloadId"] is None, "must never be handed to cleanUpDownload"
    assert r["fetched"] == 0


@needs_node
def test_BUG_018_no_caller_can_arm_a_profile_off_its_own_domains():
    """The one-call flow passes "ancestry" itself; the authorization must hold there too."""
    r = _run("profileOffSite")
    assert r == {"bad": False, "noHost": False, "good": True}


@needs_node
def test_BUG_018_an_expired_arm_hides_unrelated_downloads_from_the_page():
    """Expiry used to be settled by whichever download fired the listener next, so the page
    could tell (Codex adversarial review, second pass, 2026-09-29)."""
    assert _run("expiredWith") == _run("expiredWithout")
    mid_with, mid_without = _run("midPollWith"), _run("midPollWithout")
    assert mid_with == mid_without, "neither the reason nor the timing may differ"


@needs_node
def test_BUG_018_an_export_landing_during_the_wait_is_received():
    r = _run("landsMidPoll")
    assert r["media"] is True, r["skipped"]


@needs_node
def test_BUG_018_a_tab_that_navigated_to_another_site_cannot_collect():
    """Codex adversarial review, fourth pass: the tab id alone survived navigation."""
    r = _run("sameTabNavigated")
    assert r["attacker"] == "no download was armed"
    assert r["owner"] is True, "the attacker's attempt must not disturb the owner's download"


@needs_node
def test_BUG_018_the_ancestry_arm_in_viewer_capture_from_record_flow_still_works():
    r = _run("ancestryViewerThenRecord")
    assert r == {"media": True, "provenance": "ancestry:download"}


@needs_node
def test_BUG_018_only_the_arming_tab_can_collect_the_download():
    """Codex adversarial review, third pass: the consumer was authorized by the page-supplied
    key alone, so another tab naming the same apid took the staged download."""
    r = _run("crossTab")
    assert r["thief"] == "no download was armed"  # same answer as nothing armed at all
    assert r["noTab"] == "no download was armed"
    assert r["owner"] is True, "the stranger's attempt must not disturb the owner's download"


def test_BUG_018_every_consumer_passes_the_senders_tab():
    background = (_EXT / "background.js").read_text(encoding="utf-8")
    calls = re.findall(r"takeStagedDownload\((.*)\);", background)
    assert len(calls) == 3, calls
    assert all(", tabId, hostOf(msg." in c for c in calls), calls


def test_BUG_018_the_one_call_flow_refuses_a_non_ancestry_page_before_any_work():
    """Source check: the origin guard precedes the ledger write and the identity guard."""
    background = (_EXT / "background.js").read_text(encoding="utf-8")
    body = background[background.index("async function captureAncestryImage") :]
    guard = body.index("SIBLING_HOSTS.ancestry.test(hostOf(msg.recordUrl")
    assert guard < body.index("identityMismatch(")
    assert guard < body.index("markStarted(")


@needs_node
def test_BUG_018_a_redirect_to_another_site_is_judged_by_where_the_bytes_came_from():
    """Codex adversarial review, fifth pass: pre-existing. `[url, finalUrl].some(...)` let the
    attacker's own original URL claim a provider file it redirected to."""
    r = _run("redirectOut")
    assert r == {"media": False, "downloadId": None, "fetched": 0}


@needs_node
def test_BUG_018_two_exports_completing_together_cannot_file_the_second_under_the_first():
    """Codex adversarial review, sixth pass: pre-existing staging race."""
    r = _run("stagingRaceNoRearm")
    assert r == {"first": 1, "again": "no download was armed"}
    r = _run("stagingRace")
    assert r["first"] == 1
    assert r["rearm"] is True
    assert r["afterFirst"] != "GOT MEDIA"
    assert r["second"] is None, "a stale read from the old arm must not land in the new one"


@needs_node
def test_BUG_018_two_simultaneous_collections_get_the_download_once():
    """Codex adversarial review, seventh pass: consumption was not atomic."""
    r = _run("doubleTake")
    assert r["got"] == 1
    assert r["other"] is not None


@needs_node
def test_BUG_018_nothing_is_recorded_about_downloads_when_nothing_is_armed():
    """The researcher's own downloads are none of our business, not even as a diagnostic."""
    r = _run("unarmed")
    assert r["keys"] == []
    assert r["fetched"] == 0


#: (download host, armed page host, profile, must be accepted). The riksarkivet profile must
#: widen the rule to riksarkivet.se and nothing else.
_CORRELATION = [
    ("lbiiif.riksarkivet.se", "sok.riksarkivet.se", "riksarkivet", True),
    ("sok.riksarkivet.se", "sok.riksarkivet.se", "riksarkivet", True),
    ("riksarkivet.se", "sok.riksarkivet.se", "riksarkivet", True),
    # Without the profile, siblings stay strangers. That is the general rule, unchanged.
    ("lbiiif.riksarkivet.se", "sok.riksarkivet.se", None, False),
    # Over-matching is the dangerous direction.
    ("riksarkivet.se.evil.com", "sok.riksarkivet.se", "riksarkivet", False),
    ("evilriksarkivet.se", "sok.riksarkivet.se", "riksarkivet", False),
    ("lbiiif.riksarkivet.se.example.org", "sok.riksarkivet.se", "riksarkivet", False),
    ("example.org", "sok.riksarkivet.se", "riksarkivet", False),
    # The ancestry profile names Ancestry's real domains. It used to accept any host with
    # an "ancestry" label, so these passed (found in this review, 2026-09-29).
    ("mediasvc.ancestrycdn.com", "www.ancestry.com", "ancestry", True),
    ("www.ancestry.co.uk", "www.ancestry.com", "ancestry", True),
    ("www.ancestry.com.au", "www.ancestry.com", "ancestry", True),
    ("ancestry.evil.com", "www.ancestry.com", "ancestry", False),
    ("ancestry.com.evil.example", "www.ancestry.com", "ancestry", False),
    ("ancestrycdn.attacker.net", "www.ancestry.com", "ancestry", False),
]


@needs_node
def test_BUG_018_the_riksarkivet_profile_widens_to_riksarkivet_se_only():
    script = """
      const {hostBelongsToArmedSite} = require(process.argv[1]);
      const cases = JSON.parse(process.argv[2]);
      console.log(JSON.stringify(cases.filter(([h, a, p, want]) =>
        hostBelongsToArmedSite(h, a, p === null ? undefined : p) !== want)));
    """
    proc = subprocess.run(
        ["node", "-e", script, str(_INTERCEPT), json.dumps(_CORRELATION)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout.strip()) == []


#: ({apid, host}, expected profile). The profile is chosen from the content script's own
#: `location.hostname` (and the apid), never from anything the page event supplied.
_PROFILES = [
    ({"host": "sok.riksarkivet.se"}, "riksarkivet"),
    ({"host": "riksarkivet.se"}, "riksarkivet"),
    ({"host": "www.archion.de"}, None),
    ({"host": "riksarkivet.se.evil.com"}, None),
    ({"host": "evilriksarkivet.se"}, None),
    ({"host": ""}, None),
    # Ancestry is keyed to the apid, exactly as before.
    ({"apid": "1,7602::56510272", "host": "www.ancestry.com"}, "ancestry"),
    ({"host": "www.ancestry.com"}, None),
    ({"apid": "1,7602::56510272", "host": "www.ancestry.co.uk"}, "ancestry"),
    # An apid does not make a page Ancestry (Codex adversarial review, 2026-09-29).
    ({"apid": "1,7602::56510272", "host": "evil.example"}, None),
    ({"apid": "1,7602::56510272", "host": "ancestry.com.evil.example"}, None),
    ({"apid": "1,7602::56510272", "host": "ancestry.evil.com"}, None),
    ({"apid": "1,7602::56510272", "host": ""}, None),
]


@needs_node
def test_BUG_018_the_profile_is_chosen_from_the_page_host():
    script = """
      const {armProfileFor} = require(process.argv[1]);
      const cases = JSON.parse(process.argv[2]);
      console.log(JSON.stringify(cases.filter(([req, want]) =>
        (armProfileFor(req) ?? null) !== want)));
    """
    proc = subprocess.run(
        ["node", "-e", script, str(_INTERCEPT), json.dumps(_PROFILES)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout.strip()) == []


def test_BUG_018_background_derives_the_profile_never_from_the_message():
    """The arm handler must compute the profile. A page-named profile is a page-named allow-list."""
    background = (_EXT / "background.js").read_text(encoding="utf-8")
    assert re.search(r"profile:\s*armProfileFor\(\{\s*apid:\s*msg\.apid,\s*host:\s*msg\.host\s*\}\)", background)
    assert "profile: msg.profile" not in background
    content = (_EXT / "content.js").read_text(encoding="utf-8")
    assert "profile: detail." not in content
