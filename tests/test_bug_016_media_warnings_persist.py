"""BUG-016 — what the capture knew about its own media must survive the capture.

`media_warnings` was computed at capture time and then existed only in the HTTP response
and a toast. So `20260718-rootsirelandie-98854fa1` sat in the collection holding a page
render measuring 99.14% near-white — `looks_blank()` returns True — while its folder
looked clean: hashes verified, manifest well-formed, `verify_ok` true. The one artifact
that recorded the problem was a notification that had long since gone.

Two halves, and the tests below pin both:

1. **Going forward** the warning is written into `manifest.json`. Additive and omitted
   when empty, so a clean capture serializes byte-for-byte as before.
2. **Retroactively** `verify_capture` RE-MEASURES the renders on disk, so a capture
   banked before the field existed still reports its blank render. This is the move that
   made REQ-007 §3.3's floor retroactive rather than going-forward-only.

The warning deliberately does NOT gate `verify_ok`. A blank render is a real problem, but
the snapshot, the extraction and the hashes are still real evidence — BUG-001 settled
this: "the capture itself still succeeds; it is the SUCCESS REPORT that was lying."
Folding it into `ok` would retroactively fail captures that hold their record perfectly
well, which is the BUG-006 false-FAIL mistake in a new costume.

And the end-to-end test is the load-bearing one. REQ-007's `postAndShape` lesson —
*adding a field to the response is half the change* — was learned from a test that
asserted a consumer MENTIONED a field, and passed vacuously while the value never
arrived. So the last test drives a real blank render through `POST /capture` and reads
the value back off disk.
"""
from __future__ import annotations

import base64
import json
from datetime import datetime, timezone
from pathlib import Path

_DT = datetime(2026, 8, 24, 12, 0, 0, tzinfo=timezone.utc)
_TOKEN = "test-token-abc123"
_AUTH = {"X-Capsule-Token": _TOKEN}
_URL = "https://rootsireland.ie/ifhf/view_detail.php?recordid=141370&type=bch"
_PAGE = (
    "<html><head><title>Baptism — Patrick Doyle 1890</title></head>"
    "<body><p>Name: Patrick Doyle</p><p>Baptised: 1890</p></body></html>"
)
_RAW = {
    "record_type": "baptism",
    "personas": [{"label": "Patrick Doyle", "role": "child"}],
    "assertions": [
        {
            "persona": "Patrick Doyle",
            "field": "baptism_year",
            "value": "1890",
            "evidence_type": "stated",
            "snippet": "Baptised: 1890",
        }
    ],
}


def _pdf(content_stream: bytes) -> bytes:
    """A minimal, valid one-page PDF (US Letter) drawing `content_stream`.

    Same hand-built helper as `test_render_check`: the test owns exactly what is on the
    page, so a full-page `re f` fill is genuinely the shape of the bug rather than an
    approximation of it.
    """
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        b"/Resources << >> >>",
        b"<< /Length "
        + str(len(content_stream)).encode()
        + b" >>\nstream\n"
        + content_stream
        + b"\nendstream",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += str(i).encode() + b" 0 obj\n" + body + b"\nendobj\n"
    xref_at = len(out)
    out += b"xref\n0 " + str(len(objects) + 1).encode() + b"\n"
    out += b"0000000000 65535 f \n"
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += (
        b"trailer\n<< /Size "
        + str(len(objects) + 1).encode()
        + b" /Root 1 0 R >>\nstartxref\n"
        + str(xref_at).encode()
        + b"\n%%EOF\n"
    )
    return bytes(out)


#: The bug's own shape: an opaque white fill painted over the whole page.
BLANK_RENDER = _pdf(b"1 1 1 rg\n0 0 612 792 re f\n")
#: Ink on the page — a human sees something.
INKED_RENDER = _pdf(b"0 0 0 rg\n50 50 500 700 re f\n")


def _client(tmp_path, response_text, token=_TOKEN):
    from fastapi.testclient import TestClient

    from capsule.agent.base import Agent
    from capsule.agent.fake import FakeTransport
    from capsule.backend import app as backend
    from capsule.backend.app import Settings, app, get_settings, provide_agent

    settings = Settings(
        db_path=tmp_path / "capsule.db",
        captures_root=tmp_path / "captures",
        token=token,
    )
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[provide_agent] = lambda: Agent(
        name="extractor",
        model="claude-opus-4-8",
        transport=FakeTransport(response_text),
        now=lambda: _DT,
        new_id=lambda: "call-1",
    )
    return TestClient(app), backend


def _capture_with(tmp_path, *, media, warnings=()):
    """Write a capture folder directly, bypassing the backend."""
    from capsule.capture.manifest import write_capture
    from capsule.pipeline.build import build_capture_models
    from capsule.pipeline.extract import parse_extraction
    from capsule.pipeline.fetch import ProvidedContentFetcher

    snapshot = ProvidedContentFetcher(_PAGE, now=lambda: _DT).fetch(_URL)
    built = build_capture_models(
        snapshot,
        parse_extraction(json.dumps(_RAW)),
        new_id=lambda: "id-1",
        now=lambda: _DT,
    )
    return write_capture(
        tmp_path / "captures",
        source=built.source,
        citation=built.citation,
        extraction=built.extraction,
        snapshot_html=snapshot.html,
        media=media,
        created_at=_DT,
        media_warnings=warnings,
    )


def _media(data: bytes, provenance: str = "render:page-pdf"):
    from capsule.capture.manifest import MediaItem

    return [
        MediaItem(
            # The writer prefixes the media index itself, so the on-disk name is
            # `media/0-record.pdf` — the same path the manifest's media[] entry carries,
            # which is what the warning must name.
            slug="record",
            ext="pdf",
            data=data,
            mime="application/pdf",
            provenance=provenance,
            source_url=None,
        )
    ]


class TestTheManifestRecordsWhatWeKnew:
    def test_a_warning_is_written_into_the_manifest(self, tmp_path):
        """The whole bug in one assertion: it has to be ON DISK."""
        folder = _capture_with(
            tmp_path,
            media=_media(BLANK_RENDER),
            warnings=["media/0-record.pdf renders BLANK — re-capture with overlays hidden."],
        )
        manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))

        assert manifest["media_warnings"] == [
            "media/0-record.pdf renders BLANK — re-capture with overlays hidden."
        ]

    def test_a_clean_capture_carries_no_key_at_all(self, tmp_path):
        """Additive: the 240 existing captures must serialize exactly as they did."""
        folder = _capture_with(tmp_path, media=_media(INKED_RENDER))
        manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))

        assert "media_warnings" not in manifest

    def test_warnings_do_not_break_verify(self, tmp_path):
        """A warned capture is still a valid, hash-true capture."""
        from capsule.capture.manifest import verify_capture

        folder = _capture_with(
            tmp_path, media=_media(BLANK_RENDER), warnings=["something was wrong"]
        )
        assert verify_capture(folder).ok is True


class TestVerifyReDerivesThemRetroactively:
    def test_a_capture_written_before_the_field_still_reports_its_blank_render(
        self, tmp_path
    ):
        """The retroactive half — the reason this is measured, not read back.

        This capture's manifest has NO `media_warnings` key, exactly like every capture
        banked before 2026-08-24. `verify_capture` must still catch it, by looking at the
        bytes rather than trusting what the manifest happens to say.
        """
        from capsule.capture.manifest import verify_capture

        folder = _capture_with(tmp_path, media=_media(BLANK_RENDER))
        manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
        assert "media_warnings" not in manifest  # the pre-fix shape, confirmed

        result = verify_capture(folder)

        assert len(result.warnings) == 1
        assert "renders BLANK" in result.warnings[0]
        assert "media/0-record.pdf" in result.warnings[0]

    def test_a_blank_render_does_NOT_fail_verify(self, tmp_path):
        """BUG-001 settled this: the capture succeeds, the success REPORT was lying.

        Failing here would retroactively invalidate captures whose snapshot, extraction
        and hashes are all sound — the BUG-006 false-FAIL mistake in a new costume.
        """
        from capsule.capture.manifest import verify_capture

        result = verify_capture(_capture_with(tmp_path, media=_media(BLANK_RENDER)))

        assert result.warnings  # it is reported
        assert result.ok is True  # and it does not invalidate
        assert result.insufficient == ()  # nor is it confused with a shortfall

    def test_an_inked_render_is_not_warned_about(self, tmp_path):
        from capsule.capture.manifest import verify_capture

        folder = _capture_with(tmp_path, media=_media(INKED_RENDER))
        assert verify_capture(folder).warnings == ()

    def test_a_near_white_scan_the_SITE_served_is_never_warned_about(self, tmp_path):
        """No crying wolf. A mostly-blank register page is legitimately near-white.

        Only a RENDER is judged — it is supposed to be a picture of the page in view, so
        blank means something composited over it. Media the page itself served gets no
        such inference, which is the scoping `_check_rendered_media` has always had.
        """
        from capsule.capture.manifest import verify_capture

        folder = _capture_with(
            tmp_path, media=_media(BLANK_RENDER, provenance="page:embedded")
        )
        assert verify_capture(folder).warnings == ()


class TestItSurvivesTheWholeRoundTrip:
    def test_a_blank_render_posted_to_the_backend_lands_in_the_manifest(self, tmp_path):
        """The `postAndShape` lesson, applied: pin the ARRIVAL, not the mention.

        Drives a genuinely blank render through `POST /capture` and reads the value back
        off disk. If someone later computes `media_warnings` and forgets to forward it —
        the exact defect BUG-016 reports — this fails here rather than in the field.
        """
        client, backend = _client(tmp_path, json.dumps(_RAW))
        try:
            resp = client.post(
                "/capture",
                json={
                    "url": _URL,
                    "html": _PAGE,
                    "media": [
                        {
                            "filename": "record.pdf",
                            "mime": "application/pdf",
                            "provenance": "render:page-pdf",
                            "data_base64": base64.b64encode(BLANK_RENDER).decode(),
                        }
                    ],
                },
                headers=_AUTH,
            )
            assert resp.status_code == 200, resp.text
            body = resp.json()

            # It still reaches the operator, as it always did...
            assert any("BLANK" in w for w in body["media_warnings"])

            # ...and now it also reaches the archive, which is the bug.
            folder = Path(body["capture_folder"])
            manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
            assert any("BLANK" in w for w in manifest["media_warnings"])

            # The capture is still good: blank render, real evidence, hashes true.
            assert body["verify_ok"] is True
        finally:
            backend.app.dependency_overrides.clear()

    def test_a_good_render_posted_to_the_backend_writes_no_key(self, tmp_path):
        client, backend = _client(tmp_path, json.dumps(_RAW))
        try:
            resp = client.post(
                "/capture",
                json={
                    "url": _URL,
                    "html": _PAGE,
                    "media": [
                        {
                            "filename": "record.pdf",
                            "mime": "application/pdf",
                            "provenance": "render:page-pdf",
                            "data_base64": base64.b64encode(INKED_RENDER).decode(),
                        }
                    ],
                },
                headers=_AUTH,
            )
            assert resp.status_code == 200, resp.text
            folder = Path(resp.json()["capture_folder"])
            manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
            assert "media_warnings" not in manifest
        finally:
            backend.app.dependency_overrides.clear()
