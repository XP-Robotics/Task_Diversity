"""Sentinel / placeholder detection (PRD §9).

Presence checks pass on placeholder values, so every string field validated for
presence must additionally be screened here. Detection is deliberately fuzzy:
the reference sample's ``task_category`` is ``"Unknown_Catagory"`` -- both a
sentinel *and* a misspelling -- which an exact-match deny-list alone would miss.

Pure and I/O-free.
"""

from __future__ import annotations

import re
from typing import Optional, Tuple

_ALNUM = re.compile(r"[^a-z0-9]+")

# Deny-list terms, already in normalized form (lowercase, non-alphanumerics
# stripped). "n/a" -> "na", "-" -> "" (caught by the empty check).
_SENTINELS = frozenset({
    "unknown", "unknowncatagory", "unknowncategory", "na", "none", "null",
    "tbd", "todo", "test", "default", "placeholder", "undefined",
})

# Generic device models that do not identify hardware generation (PRD §9, B1.4).
_GENERIC_DEVICES = frozenset({"iphone", "android", "phone", "device"})

# Only apply edit-distance fuzzing to terms this long, so short tokens like
# "na"/"tbd" don't swallow every 2-4 char value.
_FUZZ_MIN_LEN = 5
_FUZZ_MAX_EDITS = 2


def normalize(value: str) -> str:
    """Lowercase and strip every non-alphanumeric character (PRD §9)."""
    return _ALNUM.sub("", str(value).lower())


def _edit_distance(a: str, b: str) -> int:
    """Levenshtein distance (iterative, single-row DP)."""
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def _matches_denylist(norm: str) -> bool:
    if norm in _SENTINELS:
        return True
    for term in _SENTINELS:
        if len(term) >= _FUZZ_MIN_LEN and _edit_distance(norm, term) <= _FUZZ_MAX_EDITS:
            return True
    return False


def classify(value, *, key_name: Optional[str] = None,
             generic_device: bool = False) -> Tuple[bool, str]:
    """Return ``(is_sentinel, reason)`` for a candidate string value.

    ``key_name``       -- when given, a value equal to its own key is a sentinel.
    ``generic_device`` -- also reject bare generic device names (B1.4).
    """
    if value is None:
        return True, "value is null"
    text = str(value)
    norm = normalize(text)
    if norm == "":
        return True, "empty or whitespace/punctuation-only"
    if key_name is not None and norm == normalize(key_name):
        return True, f"value equals its own key name '{key_name}'"
    if _matches_denylist(norm):
        return True, f"placeholder/sentinel value '{text}'"
    if generic_device and norm in _GENERIC_DEVICES:
        return True, f"generic device name '{text}' does not identify hardware generation"
    return False, ""


def is_sentinel(value, **kwargs) -> bool:
    return classify(value, **kwargs)[0]
