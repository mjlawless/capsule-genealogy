"""v0.2.3 — two things the owner saw in the popup on a Find a Grave memorial.

1. A long empty yellow bar under the title: the version banner. It is `hidden`, but
   `.row { display: flex }` is an author rule and beats the browser's `[hidden]` rule.
2. Six `.bin` files with a warning each: the popup took every `<iframe>` as a media
   candidate, and the frames were ad, tracking and consent pages. They also asked for
   access to the ad hosts, and could push a real scan out of the item cap.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

_EXT = Path(__file__).resolve().parent.parent / "extension"


def test_hidden_beats_the_row_layout_in_the_popup():
    html = (_EXT / "popup.html").read_text(encoding="utf-8")
    assert re.search(r"\[hidden\]\s*\{\s*display:\s*none\s*!important;?\s*\}", html)
    # The elements the bug showed: hidden AND laid out as a row.
    assert re.search(r'id="version-banner" class="row note" hidden', html)
    assert re.search(r'class="row" id="pending-row" hidden', html)


def _grab(frames=(), embeds=(), images=(), max_items=8):
    """Run the popup's real `grabPage` in node against a fake page."""
    source = (_EXT / "popup.js").read_text(encoding="utf-8")
    start = source.index("async function grabPage(")
    fn = source[start : source.index("\n}\n", start) + 3]
    page = {"frames": list(frames), "embeds": list(embeds), "images": list(images)}
    script = (
        fn
        + """
const page = %s;
// A frame or embed is a URL, or [url, type, shown]: a declared type, and for a same-site
// frame the content type of the document it is showing.
const el = (spec) => {
  const [src, type, shown] = Array.isArray(spec) ? spec : [spec, "", ""];
  return {
    getAttribute: (n) => (n === "src" || n === "data" ? src : n === "type" ? type || null : null),
    type,
    contentDocument: shown ? { contentType: shown } : null,
  };
};
globalThis.location = { href: "https://www.findagrave.com/memorial/1/someone" };
globalThis.document = {
  getElementById: () => null,
  documentElement: { outerHTML: "" },
  querySelectorAll(sel) {
    if (sel === "iframe[src]") return page.frames.map(el);
    if (sel === "embed[src]") return page.embeds.map(el);
    if (sel === "object[data]") return [];
    if (sel === "img") return page.images.map((src) => ({ currentSrc: src, src, naturalWidth: 900, naturalHeight: 700 }));
    throw new Error("unexpected selector " + sel);
  },
};
grabPage(true, false, %d, 1000).then((p) => console.log(JSON.stringify(p)));
"""
        % (json.dumps(page), max_items)
    )
    proc = subprocess.run(["node", "-e", script], capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


_AD_FRAMES = [
    "https://ads.example/syncframe",
    "https://ads.example/aframe",
    "https://pixels.example/partnerpixels",
    "https://cdn.example/local_storage_frame17.min.html",
    "https://widgets.example/embed/index.html",
    "https://www.findagrave.com/memorial/frame/249279068",
]
_PHOTO = "https://images.findagrave.com/photos/2020/1/1_abc.jpg"

node = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")


@node
def test_web_page_frames_are_not_media_and_ask_for_no_hosts():
    page = _grab(frames=_AD_FRAMES, images=[_PHOTO])
    assert [d["url"] for d in page["remote"]] == [_PHOTO]
    assert page["origins"] == ["https://images.findagrave.com"]


@node
def test_a_page_full_of_frames_cannot_push_the_scan_out():
    junk = [f"https://ads{i}.example/frame{i}" for i in range(12)]
    page = _grab(frames=junk, images=[_PHOTO], max_items=8)
    assert [d["url"] for d in page["remote"]] == [_PHOTO]


@node
def test_a_frame_showing_a_pdf_or_scan_is_still_taken():
    pdf = "https://records.example/docs/certificate.pdf"
    scan = "https://records.example/scans/page-26.JPG"
    page = _grab(frames=[pdf, scan, "https://ads.example/aframe"])
    assert [d["url"] for d in page["remote"]] == [pdf, scan]


@node
def test_a_certificate_frame_comes_before_a_page_of_photos():
    """Codex review: document frames keep their place ahead of images in the item cap."""
    pdf = "https://records.example/docs/certificate.pdf"
    photos = [f"https://images.example/photo{i}.jpg" for i in range(10)]
    page = _grab(frames=[pdf], images=photos, max_items=8)
    urls = [d["url"] for d in page["remote"]]
    assert urls[0] == pdf and len(urls) == 8


@node
def test_a_frame_declaring_a_pdf_at_a_signed_url_is_still_taken():
    """Codex review: a signed URL has no extension, and expires; the declared type must do."""
    signed = "https://archive.example/blob?sig=abc&sp=r"
    page = _grab(frames=[[signed, "application/pdf"], "https://ads.example/aframe"])
    assert [(d["url"], d["mime"]) for d in page["remote"]] == [(signed, "application/pdf")]


@node
def test_a_same_site_frame_showing_a_pdf_is_taken_and_one_showing_a_page_is_not():
    viewer = "https://www.findagrave.com/document?id=7"
    page = _grab(
        frames=[[viewer, "", "application/pdf"], ["https://www.findagrave.com/widget", "", "text/html"]]
    )
    assert [(d["url"], d["mime"]) for d in page["remote"]] == [(viewer, "application/pdf")]


@node
def test_an_embed_is_taken_whatever_its_path():
    """BUG-007: Chrome's PDF viewer is an <embed>; embeds are unchanged."""
    signed = "https://archive.example/blob?sig=abc&sp=r"
    page = _grab(embeds=[signed], frames=["https://ads.example/aframe"])
    assert [d["url"] for d in page["remote"]] == [signed]
