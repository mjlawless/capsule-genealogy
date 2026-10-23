"""Group the model's free-text record types into the common categories a researcher uses.

The reader writes a record type in its own words ("baptism", "Church Baptism Record",
"baptism/birth index (search results)"), which on the owner's archive made 85 distinct
values for 476 captures. The index shows and filters by the category; the exact type stays
in the capture (it is evidence of what the reader saw) and is shown in its detail. A
display projection only: nothing stored changes.

Rules are checked in order and the first match wins, so a register that mixes baptisms,
marriages and burials is a church register, not a baptism.
"""
from __future__ import annotations

import re

UNCLASSIFIED = "Unclassified"

_RULES: tuple[tuple[str, str], ...] = (
    ("Reference lists", r"coverage|holdings"),
    ("Church registers", r"church register|kirchenbuch"),
    ("Probate & wills", r"probate|\bwills?\b|letters of administration|administration records|testament"),
    ("Military", r"military|draft|pension|soldier|\bwar\b|regiment|enlist"),
    ("Immigration & naturalization", r"passenger|naturali[sz]|immigra|emigra|ship manifest|border crossing"),
    ("Census & household", r"census|household|husförhör|house interrogation|clerical survey"),
    ("Land & property", r"valuation|tithe|\bland\b|property|deed|estate map"),
    ("Marriage", r"marri|wedding|banns|trau"),
    ("Birth & baptism", r"baptis|birth|christening|taufe"),
    ("Death & burial", r"death|burial|obituar|grave|memorial|cemetery|funeral|beerdigung"),
    ("Directories & books", r"directory|book|almanac|yearbook"),
    ("Public records", r"social security|public records|voter|court"),
)
_COMPILED = tuple((name, re.compile(pattern, re.IGNORECASE)) for name, pattern in _RULES)
_EMPTY = {"", "none", "unknown", "unclassified", "n/a"}


def record_category(record_type: str | None) -> str:
    """The common category for a record type, or "Unclassified"."""
    text = (record_type or "").strip()
    if text.lower() in _EMPTY:
        return UNCLASSIFIED
    for name, pattern in _COMPILED:
        if pattern.search(text):
            return name
    return UNCLASSIFIED
