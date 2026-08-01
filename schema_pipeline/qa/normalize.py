"""Metadata normalization: flatten + canonical-key matching.

Pure and I/O-free. Shared by both modes.

Rules (from spec §3):
  * Flatten nested dicts to dotted paths, leaving flat dotted keys unchanged and
    preserving array/object leaf values (only dicts are recursed into).
  * canon_key(k) = lowercase with dashes/underscores/dots/whitespace removed, so
    ``task-id``, ``task_id`` and ``Task_ID`` compare equal.
  * Resolve a canonical field from a list of aliases, matching the full dotted
    path first, then the leaf key, and finally any interior segment -- all via
    canon_key.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, Optional, Tuple

_CANON_STRIP = re.compile(r"[-_.\s]+")


def canon_key(key: Any) -> str:
    """Lowercase and strip dashes/underscores/dots/whitespace for comparison."""
    return _CANON_STRIP.sub("", str(key).lower())


def flatten(obj: Dict[str, Any], prefix: str = "") -> Dict[str, Any]:
    """Flatten nested dicts to dotted paths.

    Only ``dict`` values are recursed into; lists and scalars are kept as leaf
    values (preserving array/object leaves). An empty dict is itself a leaf so
    its presence is not lost.
    """
    out: Dict[str, Any] = {}
    for k, v in obj.items():
        path = f"{prefix}.{k}" if prefix else str(k)
        if isinstance(v, dict) and v:
            out.update(flatten(v, path))
        else:
            out[path] = v
    return out


def canon_set(names: Iterable[str]) -> frozenset:
    """Build the set of canonical tokens for a field name and its aliases."""
    return frozenset(canon_key(n) for n in names)


def is_empty(value: Any) -> bool:
    """A value counts as 'not present' when it is null or empty."""
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip() == ""
    if isinstance(value, (list, dict, tuple, set)):
        return len(value) == 0
    return False


def resolve(flat: Dict[str, Any], canons: frozenset) -> Optional[Tuple[str, Any]]:
    """Return the (path, value) that resolves a canonical field, or None.

    Matching precedence: full dotted path, then the leaf (last) segment, then
    any interior segment. Only non-empty values count.
    """
    # 1. full dotted path
    for path, val in flat.items():
        if canon_key(path) in canons and not is_empty(val):
            return path, val
    # 2. leaf segment
    for path, val in flat.items():
        if is_empty(val):
            continue
        leaf = path.rsplit(".", 1)[-1]
        if canon_key(leaf) in canons:
            return path, val
    # 3. any interior segment
    for path, val in flat.items():
        if is_empty(val):
            continue
        if any(canon_key(seg) in canons for seg in path.split(".")):
            return path, val
    return None


def present(flat: Dict[str, Any], canons: frozenset) -> bool:
    """True if some flattened path resolves the field with a non-empty value."""
    return resolve(flat, canons) is not None


def value(flat: Dict[str, Any], canons: frozenset) -> Any:
    """Return the resolved value for a canonical field, or None."""
    hit = resolve(flat, canons)
    return hit[1] if hit else None


def value_in_order(flat: Dict[str, Any], ordered_canons: Iterable[str]) -> Any:
    """Resolve a field trying each spelling in *priority* order.

    :func:`resolve` scans the flattened metadata and returns the first key that
    matches any accepted spelling -- so when a record carries two of them, the
    winner depends on JSON key order. That is fine for a field with one real
    name, but not where one spelling is canonical and another is a fallback.
    Here the alias list decides: the canonical name wins whenever it is present,
    whatever order the file happens to use.
    """
    for canon in ordered_canons:
        hit = resolve(flat, frozenset([canon]))
        if hit is not None:
            return hit[1]
    return None
