"""BUG-022 — Newspapers.com: the armed capture never received the site's JPG download.

On `www.newspapers.com/image/<id>/` the researcher armed, used the site's own Download →
"Save as JPG", and the file landed in ~/Downloads. The capture still reported
`image_status: "none"`. The file's own download provenance (`kMDItemWhereFroms`) shows it
came from `img.newspapers.com`, a SIBLING of the armed `www.newspapers.com`, which the
dotted-suffix correlation rule refuses. Same shape as BUG-018 (Riksarkivet).

These reuse BUG-018's node harness (the shipped `download-intercept.js` against a fake
`chrome` and a fake clock), so the real listener → stage → take path is exercised. The live
E2E (one armed Newspapers.com capture returning `image_status: full`) is the owner's.
"""
from __future__ import annotations

import json
import subprocess

import tests.test_bug_018_riksarkivet_download as t18

needs_node = t18.needs_node

# The download URL as Newspapers.com serves it, minus its signed `iat=` token.
_IMG = "https://img.newspapers.com/img/img?institutionId=0&id=715957102&a=download&filename=x"

_SCENARIOS = r"""
  // The bug itself: armed on www., the JPG comes from img.
  async newspapers() {
    const a = await arm("www.newspapers.com");
    await land(1, { url: "%(img)s", mime: "image/jpeg" });
    const t = await take();
    return { armed: a, provenance: t.media && t.media.provenance, skipped: t.skipped || null, fetched };
  },
  // A page that is NOT on newspapers.com gets no profile, so img.newspapers.com stays a
  // stranger to it: the researcher's own Newspapers.com download is not claimed.
  async newspapersFromElsewhere() {
    const a = await arm("evil.example");
    await land(1, { url: "%(img)s", mime: "image/jpeg" });
    const t = await take();
    return { armed: a, media: !!t.media, fetched };
  },
  // The profile is authorized centrally: naming it from a page off newspapers.com is refused.
  async newspapersProfileForged() {
    return await m.armDownload({ key: "K", tabId: 1, host: "newspapers.com.evil.example", profile: "newspapers" });
  },
""" % {"img": _IMG}

_HARNESS = t18._HARNESS.replace("const scenarios = {", "const scenarios = {" + _SCENARIOS, 1)


def _run(scenario: str) -> dict:
    proc = subprocess.run(
        ["node", "-e", _HARNESS, str(t18._INTERCEPT), scenario],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip())


def test_BUG_022_the_harness_took_the_new_scenarios():
    """Guard the string splice: if BUG-018's harness is reshaped, fail here, not silently."""
    assert "async newspapers()" in _HARNESS


@needs_node
def test_BUG_022_a_newspapers_jpg_from_img_is_received():
    """The reported case: armed on www.newspapers.com, the JPG comes from img.newspapers.com."""
    r = _run("newspapers")
    assert r["armed"] == {"ok": True}
    assert r["skipped"] is None, r["skipped"]
    assert r["provenance"] == "site:official-download"
    assert r["fetched"] == 1


@needs_node
def test_BUG_022_a_page_elsewhere_cannot_claim_a_newspapers_download():
    r = _run("newspapersFromElsewhere")
    assert r["armed"] == {"ok": True}
    assert r["media"] is False
    assert r["fetched"] == 0


@needs_node
def test_BUG_022_the_newspapers_profile_is_refused_off_newspapers_com():
    r = _run("newspapersProfileForged")
    assert r["ok"] is False


#: (download host, armed page host, profile, must be accepted). The newspapers profile must
#: widen the rule to newspapers.com and nothing else.
_CORRELATION = [
    ("img.newspapers.com", "www.newspapers.com", "newspapers", True),
    ("www.newspapers.com", "www.newspapers.com", "newspapers", True),
    ("newspapers.com", "www.newspapers.com", "newspapers", True),
    # Without the profile, siblings stay strangers. That is the general rule, unchanged.
    ("img.newspapers.com", "www.newspapers.com", None, False),
    # Over-matching is the dangerous direction.
    ("newspapers.com.evil.com", "www.newspapers.com", "newspapers", False),
    ("evilnewspapers.com", "www.newspapers.com", "newspapers", False),
    ("img.newspapers.com.example.org", "www.newspapers.com", "newspapers", False),
    ("example.org", "www.newspapers.com", "newspapers", False),
    # The newspapers profile does not widen to the other profiles' sites.
    ("lbiiif.riksarkivet.se", "www.newspapers.com", "newspapers", False),
]


@needs_node
def test_BUG_022_the_newspapers_profile_widens_to_newspapers_com_only():
    script = """
      const {hostBelongsToArmedSite} = require(process.argv[1]);
      const cases = JSON.parse(process.argv[2]);
      console.log(JSON.stringify(cases.filter(([h, a, p, want]) =>
        hostBelongsToArmedSite(h, a, p === null ? undefined : p) !== want)));
    """
    proc = subprocess.run(
        ["node", "-e", script, str(t18._INTERCEPT), json.dumps(_CORRELATION)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout.strip()) == []


#: ({apid, host}, expected profile), chosen from the content script's own `location.hostname`.
_PROFILES = [
    ({"host": "www.newspapers.com"}, "newspapers"),
    ({"host": "newspapers.com"}, "newspapers"),
    ({"host": "newspapers.com.evil.example"}, None),
    ({"host": "evilnewspapers.com"}, None),
    # An apid keeps meaning Ancestry-or-nothing, even on newspapers.com.
    ({"apid": "1,7602::56510272", "host": "www.newspapers.com"}, None),
    # The existing host-selected profile is unaffected.
    ({"host": "sok.riksarkivet.se"}, "riksarkivet"),
]


@needs_node
def test_BUG_022_the_profile_is_chosen_from_the_page_host():
    script = """
      const {armProfileFor} = require(process.argv[1]);
      const cases = JSON.parse(process.argv[2]);
      console.log(JSON.stringify(cases.filter(([req, want]) =>
        (armProfileFor(req) ?? null) !== want)));
    """
    proc = subprocess.run(
        ["node", "-e", script, str(t18._INTERCEPT), json.dumps(_PROFILES)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout.strip()) == []
