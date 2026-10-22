"""The supported-sites catalog (`capsule/catalog/sites.toml`) stays honest.

- SITES.md and the README table are generated from it and must be current.
- Every site the code special-cases has an entry, so a new site profile cannot ship
  without a line in the public list.
- Each entry's link goes to one of its own hosts (a typo or a lookalike domain fails).
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from capsule.catalog import (
    ACCESS,
    LEVELS,
    REPO_ROOT,
    load_sites,
    readme_with_table,
    render_sites_md,
    site_for_host,
)

SITES = load_sites()


def test_sites_md_is_generated_from_the_catalog():
    current = (REPO_ROOT / "SITES.md").read_text(encoding="utf-8")
    assert current == render_sites_md(SITES), "run: uv run python -m capsule.catalog"


def test_the_readme_table_is_generated_from_the_catalog():
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    assert readme == readme_with_table(readme, SITES), "run: uv run python -m capsule.catalog"


def test_entries_are_well_formed():
    ids = [s.id for s in SITES]
    assert len(ids) == len(set(ids))
    for s in SITES:
        assert s.level in LEVELS and s.access in ACCESS, s.id
        assert s.hosts and s.pages, s.id
        for h in s.hosts:
            assert re.fullmatch(r"[a-z0-9-]+(\.[a-z0-9-]+)+", h), (s.id, h)


def test_each_link_goes_to_the_sites_own_host():
    for s in SITES:
        parts = urlsplit(s.url)
        assert parts.scheme == "https", s.id
        assert site_for_host(parts.hostname or "", [s]) is s, (s.id, s.url)


def test_no_host_belongs_to_two_entries():
    for s in SITES:
        for h in s.hosts:
            owners = [o.id for o in SITES if site_for_host(h, [o])]
            assert owners == [s.id], (h, owners)


def _covered(host: str) -> bool:
    """A code host is covered when a catalog host equals it or is its parent domain."""
    return site_for_host(host, SITES) is not None


def test_every_host_the_backend_special_cases_has_an_entry():
    from capsule.pipeline import dedup, external_ref
    from capsule.pipeline.page_identity import LEGACY_DISCRIMINATOR_HOSTS, LIVE_LABEL_HOSTS
    from capsule.pipeline.sufficiency import TILE_VIEWER_HOSTS

    code_hosts = (
        set(TILE_VIEWER_HOSTS)
        | set(LIVE_LABEL_HOSTS)
        | set(LEGACY_DISCRIMINATOR_HOSTS)
        | set(dedup._NAI_HOSTS)
        | {external_ref._NLI_HOST}
    )
    missing = sorted(h for h in code_hosts if not _covered(h))
    assert not missing, f"add these to capsule/catalog/sites.toml: {missing}"


def test_ancestry_entry_matches_the_code():
    from capsule.pipeline.ancestry import _ANCESTRY_HOST

    ancestry = next(s for s in SITES if s.id == "ancestry")
    for h in ancestry.hosts:
        assert _ANCESTRY_HOST.search(h), h
    # Every national domain the code accepts is listed.
    tlds = re.search(r"\\\.\(([^)]*)\)\$$", _ANCESTRY_HOST.pattern).group(1).split("|")
    listed = {h.removeprefix("ancestry.") for h in ancestry.hosts}
    assert {t.replace("\\.", ".") for t in tlds} == listed


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_every_download_profile_in_the_extension_has_an_entry():
    """`SIBLING_HOSTS` in download-intercept.js names the sites with an armed-download
    profile. It is module-private, so read it by running the file."""
    source = REPO_ROOT / "extension" / "download-intercept.js"
    script = (
        "const vm = require('vm'), fs = require('fs');"
        f"const src = fs.readFileSync({json.dumps(str(source))}, 'utf8');"
        "const ctx = { module: {}, console };"
        "vm.runInNewContext(src + ';this.__S = SIBLING_HOSTS;', ctx);"
        "const out = {}; for (const [k, re] of Object.entries(ctx.__S)) out[k] = re.source;"
        "console.log(JSON.stringify(out));"
    )
    proc = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    profiles = json.loads(proc.stdout)
    assert set(profiles) == {s.id for s in SITES if s.arm_download}
    for site_id, pattern in profiles.items():
        site = next(s for s in SITES if s.id == site_id)
        for h in site.hosts:
            assert re.search(pattern, h, re.IGNORECASE), (site_id, h)


def test_the_catalog_ships_no_record_addresses():
    """Public file: home or search pages only, never a record."""
    text = (Path(__file__).resolve().parent.parent / "capsule/catalog/sites.toml").read_text(
        encoding="utf-8"
    )
    assert not re.search(r"\d{5,}", text)
    assert not re.search(r"record_?id=|/records?/\d|view_detail|/ark:|/memorial/\d", text)
