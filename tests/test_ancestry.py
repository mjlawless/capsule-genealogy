"""Tests for Ancestry identifier parsing (capsule/pipeline/ancestry.py).

These parsers are the identity guard's foundation (REQ-001 R5). During the pilot the
researcher read a page they believed was the 1900 census and it was a School Yearbook
entry for a different person. A wrong page captured and cited onto a direct ancestor is
exactly the silent corruption this project exists to eliminate — so every identifier we
bind on is DERIVED HERE, deterministically, never invented by a model.

Written FIRST (RED): capsule/pipeline/ancestry.py does not exist yet.
"""
from __future__ import annotations


class TestParseApid:
    def test_parses_the_gedcom_apid_pointer(self):
        from capsule.pipeline.ancestry import parse_apid

        # The pointer that survived the GEDCOM import: "1,7602::56510272".
        assert parse_apid("1,7602::56510272") == ("7602", "56510272")

    def test_tolerates_surrounding_whitespace(self):
        from capsule.pipeline.ancestry import parse_apid

        assert parse_apid("  1,7602::56510272  ") == ("7602", "56510272")

    def test_accepts_the_bare_pair_form(self):
        from capsule.pipeline.ancestry import parse_apid

        assert parse_apid("7602:56510272") == ("7602", "56510272")

    def test_garbage_is_None_never_an_exception(self):
        """A capture must never die parsing an id — it must REFUSE, which is different."""
        from capsule.pipeline.ancestry import parse_apid

        assert parse_apid("") is None
        assert parse_apid("not-an-apid") is None
        assert parse_apid("1,7602::") is None
        assert parse_apid(None) is None


class TestRecordIdsFromUrl:
    def test_search_collections_url(self):
        from capsule.pipeline.ancestry import record_ids_from_url

        url = "https://www.ancestry.com/search/collections/7602/records/56510272"
        assert record_ids_from_url(url) == ("7602", "56510272")

    def test_discoveryui_view_url(self):
        from capsule.pipeline.ancestry import record_ids_from_url

        url = "https://www.ancestry.com/discoveryui-content/view/56510272:7602"
        assert record_ids_from_url(url) == ("7602", "56510272")

    def test_url_with_query_and_fragment(self):
        from capsule.pipeline.ancestry import record_ids_from_url

        url = (
            "https://www.ancestry.com/search/collections/7602/records/56510272"
            "?tid=123&pid=456#hit"
        )
        assert record_ids_from_url(url) == ("7602", "56510272")

    def test_a_non_record_ancestry_url_is_None(self):
        from capsule.pipeline.ancestry import record_ids_from_url

        assert record_ids_from_url("https://www.ancestry.com/family-tree/person/123") is None

    def test_a_non_ancestry_url_is_None(self):
        from capsule.pipeline.ancestry import record_ids_from_url

        assert record_ids_from_url("https://www.rootsireland.ie/x") is None

    def test_unparseable_never_raises(self):
        from capsule.pipeline.ancestry import record_ids_from_url

        assert record_ids_from_url("") is None
        assert record_ids_from_url("::::") is None
        assert record_ids_from_url(None) is None


class TestImageIdFromUrl:
    def test_image_id_from_the_imageviewer_url(self):
        from capsule.pipeline.ancestry import image_id_from_url

        url = (
            "https://www.ancestry.com/imageviewer/collections/7602/images/"
            "4114737_00288?pId=56510272"
        )
        assert image_id_from_url(url) == "4114737_00288"

    def test_no_image_id_is_None(self):
        from capsule.pipeline.ancestry import image_id_from_url

        assert image_id_from_url("https://www.ancestry.com/search/collections/7602") is None
        assert image_id_from_url(None) is None


class TestIdentityGuard:
    """The guard: does the page I landed on ACTUALLY hold the record I asked for?"""

    def test_matching_page_is_allowed(self):
        from capsule.pipeline.ancestry import identity_mismatch

        assert (
            identity_mismatch(
                expected_apid="1,7602::56510272",
                url="https://www.ancestry.com/search/collections/7602/records/56510272",
            )
            is None
        )

    def test_BUG_the_yearbook_incident_a_different_record_is_refused(self):
        """Regression: the pilot landed on a School Yearbook entry for a different person.

        Benign cause (same browser window), catastrophic failure mode: a wrong page
        captured and cited onto a direct ancestor. The guard must ABORT, not warn.
        """
        from capsule.pipeline.ancestry import identity_mismatch

        reason = identity_mismatch(
            expected_apid="1,7602::56510272",
            url="https://www.ancestry.com/search/collections/60525/records/118449",
        )
        assert reason is not None
        assert "7602:56510272" in reason  # what we asked for
        assert "60525:118449" in reason  # what we actually landed on

    def test_an_unparseable_url_with_an_expectation_is_a_mismatch(self):
        """Cannot-verify is NOT verified.

        The same principle as the blank-page gate returning None rather than False: "I
        could not check" must never be recorded as "I checked and it is fine".
        """
        from capsule.pipeline.ancestry import identity_mismatch

        assert (
            identity_mismatch(expected_apid="1,7602::56510272", url="https://x.test/")
            is not None
        )

    def test_a_malformed_expectation_is_a_mismatch(self):
        from capsule.pipeline.ancestry import identity_mismatch

        assert (
            identity_mismatch(
                expected_apid="garbage",
                url="https://www.ancestry.com/search/collections/7602/records/56510272",
            )
            is not None
        )
