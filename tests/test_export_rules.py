"""The public export carries code, not decision records.

`release/forbidden.txt` makes `release/export-public.sh` refuse to build when a decision record
or a quote is left in a shipped file. These pin what it must catch, including an attribution
split across two lines, and what it must leave alone (`author="owner"` is a real value). They run
the export's own `grep -iE -f` check. The rules exist only in the private repository, so the
checks skip in the public tree. Sample text is built from pieces so this file never trips the
guard it tests.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

_RELEASE = Path(__file__).resolve().parent.parent / "release"
_FORBIDDEN = _RELEASE / "forbidden.txt"

pytestmark = [
    pytest.mark.skipif(not _FORBIDDEN.is_file(), reason="export rules exist only privately"),
    pytest.mark.skipif(shutil.which("grep") is None, reason="grep is not installed"),
]

_OWNER = "own" + "er"


def _caught(line: str, tmp_path: Path) -> bool:
    """The export's own check, as `export-public.sh` runs it, on one line."""
    patterns = tmp_path / "patterns.txt"
    patterns.write_text(
        "\n".join(
            p for p in _FORBIDDEN.read_text(encoding="utf-8").splitlines()
            if p and not p.startswith("#")
        ) + "\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        ["grep", "-iE", "-f", str(patterns)],
        input=line + "\n", capture_output=True, text=True, timeout=30, check=False,
    )
    assert result.returncode in (0, 1), result.stderr
    return result.returncode == 0


@pytest.mark.parametrize(
    "line",
    [
        f"    on ({_OWNER} ruling, 2026-10-21). Capturing the record",
        f"read just because it is pending ({_OWNER}, 2026-10-24).",
        f"which may not be what the user wants ({_OWNER},",
        f"the capture itself succeeded ({_OWNER} deference, 2026-10-21).",
        f"{_OWNER.capitalize()} ruling R1 (2026-09-24): transcribed values live here",
        f"{_OWNER.capitalize()}'s call after the model trial.",
        f"The {_OWNER} ruled that adding a key must not read them all.",
        f"the 2026-07-14 P{'M'} ruling already allows it",
        f"## 2. Capture ID (P{'M'} decision D6)",
        f"closes itself after this long ({_OWNER}: ~15 s)",
        f"regresses extraction on the {_OWNER}'s real records.",
        f"{_OWNER.capitalize()}-confirmed corrections 2026-09-24: Undertaker",
    ],
)
def test_a_decision_record_or_quote_is_caught(line, tmp_path):
    assert _caught(line, tmp_path), line


@pytest.mark.parametrize(
    "line",
    [
        f'    author: str = "{_OWNER}",',
        f'    actor: str = "{_OWNER}"',
        f'"the stranger\'s attempt must not disturb the {_OWNER}\'s download"',
        f"Persist the token ({_OWNER}-only where the OS supports it).",
    ],
)
def test_ordinary_code_is_left_alone(line, tmp_path):
    assert not _caught(line, tmp_path), line


def test_the_export_expands_patterns_in_the_exclude_list():
    script = (_RELEASE / "export-public.sh").read_text(encoding="utf-8")
    assert 'for match in "${target:?}"/$path; do rm -rf "$match"; done' in script
    assert "docs/specs/REQ-*" in (_RELEASE / "exclude.txt").read_text(encoding="utf-8")
