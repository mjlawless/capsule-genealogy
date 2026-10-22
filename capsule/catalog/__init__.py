"""The supported-sites catalog: which sites Genealogy Capsule is known to work with.

`sites.toml` is the source of truth. SITES.md and the table in README.md are generated
from it (`uv run python -m capsule.catalog`), and a test fails while either is out of date,
or while a site the code special-cases has no entry.

Levels are set by hand from evidence (verified captures of the named pages), never computed
from a collection: the catalog is public, and a collection is private.
"""
from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

CATALOG_PATH = Path(__file__).with_name("sites.toml")
REPO_ROOT = Path(__file__).resolve().parent.parent.parent

LEVELS = {
    "tested": "Tested in real use",
    "special": "Built-in support, not yet tested live",
    "generic": "Should work, not yet tested",
}
ACCESS = {
    "free": "Free",
    "free-registration": "Free (sign-up)",
    "subscription": "Subscription",
}

README_START = "<!-- sites:start (generated from capsule/catalog/sites.toml) -->"
README_END = "<!-- sites:end -->"


@dataclass(frozen=True)
class Site:
    id: str
    name: str
    url: str
    hosts: tuple[str, ...]
    access: str
    level: str
    pages: str
    handling: tuple[str, ...]
    limits: str
    arm_download: bool


def load_sites(path: Path = CATALOG_PATH) -> list[Site]:
    with path.open("rb") as fh:
        raw = tomllib.load(fh)
    return [
        Site(
            id=s["id"],
            name=s["name"],
            url=s["url"],
            hosts=tuple(s["hosts"]),
            access=s["access"],
            level=s["level"],
            pages=s["pages"],
            handling=tuple(s.get("handling", ())),
            limits=s.get("limits", ""),
            arm_download=bool(s.get("arm_download", False)),
        )
        for s in raw["site"]
    ]


def site_for_host(host: str, sites: list[Site]) -> Site | None:
    """The catalog entry whose host is `host` or a parent domain of it."""
    host = host.lower()
    for site in sites:
        if any(host == h or host.endswith("." + h) for h in site.hosts):
            return site
    return None


_PREAMBLE = """\
Genealogy Capsule saves the page you are viewing, in your own browser session, when you
click Capture. It works on most record pages; the sites below are the ones it is known to
work with, and how well. Capsule is not affiliated with any of these sites. Pages from
subscription sites are kept only on your computer and never published.
"""


def render_sites_md(sites: list[Site]) -> str:
    out = [
        "# Sites Genealogy Capsule works with",
        "",
        "<!-- Generated from capsule/catalog/sites.toml by `uv run python -m capsule.catalog`."
        " Edit that file, not this one. -->",
        "",
        _PREAMBLE,
        "Tried it on another site? Please say how it went in an issue, so the list can grow.",
        "",
    ]
    for level, title in LEVELS.items():
        group = [s for s in sites if s.level == level]
        if not group:
            continue
        out += [f"## {title}", ""]
        for s in group:
            out += [f"### [{s.name}]({s.url})", "", f"- **Access:** {ACCESS[s.access]}"]
            out.append(f"- **Pages:** {s.pages}")
            if s.handling:
                out.append("- **Built-in handling:** " + "; ".join(s.handling))
            if s.limits:
                out.append(f"- **Note:** {s.limits}")
            out.append("")
    return "\n".join(out).rstrip("\n") + "\n"


def render_readme_table(sites: list[Site]) -> str:
    rows = [
        README_START,
        "| Site | Access | Status |",
        "|---|---|---|",
    ]
    for level, title in LEVELS.items():
        for s in sites:
            if s.level == level:
                rows.append(f"| [{s.name}]({s.url}) | {ACCESS[s.access]} | {title} |")
    rows += ["", "Details and notes for each site: [SITES.md](SITES.md).", README_END]
    return "\n".join(rows)


def readme_with_table(readme: str, sites: list[Site]) -> str:
    start, end = readme.index(README_START), readme.index(README_END) + len(README_END)
    return readme[:start] + render_readme_table(sites) + readme[end:]


def main() -> None:
    sites = load_sites()
    (REPO_ROOT / "SITES.md").write_text(render_sites_md(sites), encoding="utf-8", newline="\n")
    readme_path = REPO_ROOT / "README.md"
    readme = readme_path.read_text(encoding="utf-8")
    readme_path.write_text(readme_with_table(readme, sites), encoding="utf-8", newline="\n")
