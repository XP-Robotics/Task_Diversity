"""Source profiles: adapt a specific delivery's metadata shape to the generic
tool, without touching the §5 registry or the diversity logic.

A profile declares two things:

  * ``unwrap`` -- a pure transform from the raw parsed JSON into the single
    metadata *object* the tool validates. This is where a container mismatch is
    resolved (e.g. an array-of-chunks wrapped under ``data.metadata``). It only
    reshapes structure; it never renames keys or invents values.

  * ``schema_aliases`` / ``diversity_aliases`` -- extra accepted spellings so a
    canonical field resolves to the delivery's own key. Only *confident*
    mappings belong here; genuinely-absent fields are left to report honestly.

``apply(profile)`` merges the aliases into the schema and diversity registries
and rebuilds their derived tables. The generic profile is a no-op, so the
default run behaves exactly as before.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from . import config as C
from . import schema as S


@dataclass
class SourceProfile:
    name: str
    description: str
    unwrap: Optional[Callable[[Any], Any]] = None
    schema_aliases: Dict[str, List[str]] = field(default_factory=dict)
    diversity_aliases: Dict[str, List[str]] = field(default_factory=dict)
    # When set, S3 folder ingestion reads exactly this sidecar basename instead
    # of the default ``metadata.json`` / ``*.meta.json`` (case-insensitive).
    meta_basename: Optional[str] = None

    def unwrap_meta(self, raw: Any) -> Any:
        """Apply the container transform (identity when no unwrap declared)."""
        if self.unwrap is None:
            return raw
        return self.unwrap(raw)


# ---------------------------------------------------------------------------
# Generic (default): no transform, no extra aliases.
# ---------------------------------------------------------------------------
GENERIC = SourceProfile(name="generic", description="No adaptation (default).")


# ---------------------------------------------------------------------------
# xphi-capture-v1: the Vultr `processed/` delivery.
#
# metadata.json is a JSON array of chunk records; the real payload is the first
# chunk's data.metadata object. One processed/<uid>/ folder == one episode (one
# video.mov), so we unwrap to that first chunk. Confident field mappings only:
#   episode_start_time -> start_time_unix  (a unix start time)
#   environment_label  -> environment_l1   (the environment category label)
# Deliberately NOT mapped (uncertain / semantically different, left as honest
# extras): task_category, device_model, device_type, software_version,
# camera_configuration. And end_time / difficulty / device / operator-physical
# fields are simply absent -> reported missing, never fabricated.
# ---------------------------------------------------------------------------
def _xphi_unwrap(raw: Any) -> Any:
    rec = raw[0] if isinstance(raw, list) and raw else raw
    if isinstance(rec, dict):
        data = rec.get("data", rec)
        if isinstance(data, dict) and isinstance(data.get("metadata"), dict):
            return data["metadata"]
        if isinstance(rec.get("metadata"), dict):
            return rec["metadata"]
    return rec


XPHI_V1 = SourceProfile(
    name="xphi-capture-v1",
    description="Vultr xphi-capture processed/ delivery (array/data.metadata).",
    unwrap=_xphi_unwrap,
    schema_aliases={
        "start_time_unix": ["episode_start_time"],
        "environment_l1": ["environment_label"],
    },
    diversity_aliases={
        "start_time_unix": ["episode_start_time"],
        "environment_l1": ["environment_label"],
    },
)


# ---------------------------------------------------------------------------
# licensed-egocentric-v1: Build.AI Egocentric-100K (xphi3/licensed_data).
#
# Split layout (videos/ + metadata/ by stem); metadata is already a flat object
# so no unwrap. The §5 vocabulary is genuinely absent (this is a licensed public
# dataset with technical metadata), so nothing is force-mapped for schema. For
# diversity, two confident structural mappings let the grouping checks run:
#   factory_id -> environment_id   (the collection site)
#   worker_id  -> operator_id      (the person)
# duration/resolution/codec are left as honest extras (they power an integrity
# cross-check, not §5 conformance).
# ---------------------------------------------------------------------------
LICENSED_V1 = SourceProfile(
    name="licensed-egocentric-v1",
    description="Build.AI Egocentric-100K licensed_data (videos/ + metadata/ by stem).",
    diversity_aliases={
        "environment_id": ["factory_id"],
        "operator_id": ["worker_id"],
    },
)


# ---------------------------------------------------------------------------
# xphi-episode-v1: the xphi4 `egocentric_data/` delivery, validated against the
# RICH per-episode sidecar ``episode_metadata.json`` (not the sparse chunk-array
# ``metadata.json`` that xphi-capture-v1 reads).
#
# episode_metadata.json is already a flat object (no unwrap) with dash-cased
# keys; canon_key() unifies dash/underscore/case, so operator-id, worker-type,
# task-difficulty, human-interaction and start/end-time-unix resolve to their
# canonical diversity fields with no aliases needed. The one confident mapping
# is the environment *label* used for the L1 category check:
#   environment -> environment_l1   (e.g. "warehouse", "office_packing_and_assembly")
# environment-category ("factory"/"warehouse") stays available as an extra; it
# is the coarser grouping, not the L1 label. business_size is genuinely absent
# -> that check stays NOT_COMPUTABLE, honestly.
# ---------------------------------------------------------------------------
XPHI_EPISODE_V1 = SourceProfile(
    name="xphi-episode-v1",
    description="xphi4 egocentric_data/ delivery, read from episode_metadata.json (rich sidecar).",
    meta_basename="episode_metadata.json",
    schema_aliases={
        "environment_l1": ["environment"],
    },
    diversity_aliases={
        "environment_l1": ["environment"],
    },
)


_REGISTRY: Dict[str, SourceProfile] = {
    p.name: p for p in (GENERIC, XPHI_V1, LICENSED_V1, XPHI_EPISODE_V1)
}


def names() -> List[str]:
    return list(_REGISTRY)


def get(name: str) -> SourceProfile:
    try:
        return _REGISTRY[name]
    except KeyError:
        raise KeyError(f"unknown profile '{name}'; known: {', '.join(_REGISTRY)}")


def _merge(target: Dict[str, List[str]], extra: Dict[str, List[str]]) -> None:
    for field_name, spellings in extra.items():
        bucket = target.setdefault(field_name, [])
        for spelling in spellings:
            if spelling not in bucket:
                bucket.append(spelling)


def apply(profile: SourceProfile) -> None:
    """Merge a profile's aliases into the live registries and rebuild lookups."""
    from . import metrics  # local import to avoid any import-order coupling

    if profile.schema_aliases:
        _merge(S.ALIASES, profile.schema_aliases)
        S.rebuild()
    if profile.diversity_aliases:
        _merge(C.DIVERSITY_ALIASES, profile.diversity_aliases)
        metrics.rebuild_canons()
