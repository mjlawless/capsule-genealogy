"""Contract 2 — TreeAdapter (placeholder).

All tree reads/writes MUST go through this interface. The read surface is
locked at M2 (against real Matcher needs); the write surface at M3 (GEDCOM
export → Ancestry research tree). First concrete impls: GedcomReadAdapter (M2),
GedcomWriteAdapter (M3). See docs/plan.md and .claude/prds/capsule.prd.md.

Intentionally empty at M0 — it exists to make the boundary visible/greppable
so no pipeline module quietly assumes tree structure.
"""

from typing import Protocol, runtime_checkable


@runtime_checkable
class TreeAdapter(Protocol):
    """Marker protocol. Methods added when the read surface locks at M2."""
    ...
