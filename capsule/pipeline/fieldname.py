"""Mechanical snake_case for the record's own field labels.

`Assertion.field` is the label the record prints — "Borough", "Date of Marriage",
"Certificate #". The model copies it; it does not invent it. But a label is
typography, and two captures of the same register would otherwise disagree on
`Date of Marriage` vs `date_of_marriage` vs `DATE OF MARRIAGE` purely by how the
page happened to style it.

Folding that away is *code's* job, not the model's, because it is a deterministic
string transform with no judgment in it. Every judgment call — "is `Borough` the
same fact as `death_place`?" — lives in `normalized_field`, where a human can see
it and disagree.

Deliberately no camelCase handling: these are labels printed for human readers,
not identifiers, so `DateOfMarriage` does not occur and guessing at word
boundaries would only add a way to be wrong.
"""
from __future__ import annotations

import re

_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")


def snake_case(label: str) -> str:
    """Fold a printed field label to `snake_case`; "" if it holds no alphanumerics.

    Returns "" rather than raising, so the pure eval scorer can fall back to the
    raw label while the capture pipeline treats the same input as a hard error.
    """
    return _NON_ALNUM_RE.sub("_", label.strip().casefold()).strip("_")
