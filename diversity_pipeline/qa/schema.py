"""Reference §5 field registry for Mode A (schema conformance).

Transcribed from *Human Egocentric Videos, Optional Gloves (2026-07-10) §5*.

Everything here is an editable list so that misclassifying a field is a one-line
move between tiers, and adding an accepted spelling touches only ``ALIASES``.

Tiers:
  REQUIRED_BASE      - required for every episode
  OPTIONAL_BASE      - accepted, never required
  HANDHELD_REQUIRED  - required only for handheld/UMI episodes (§5.4)
  GLOVE_REQUIRED     - required only for glove episodes (§5.5)
"""

from __future__ import annotations

from typing import Dict, List, Tuple

from .normalize import canon_key, canon_set

# Each entry: (canonical_field_name, section_label)

REQUIRED_BASE: List[Tuple[str, str]] = [
    # §5.1 Episode
    ("episode_uuid", "§5.1 Episode"),
    ("operator_id", "§5.1 Episode"),
    ("operator_consent", "§5.1 Episode"),
    ("operator_job", "§5.1 Episode"),
    ("start_time_unix", "§5.1 Episode"),
    ("end_time_unix", "§5.1 Episode"),
    ("task_id", "§5.1 Episode"),
    ("task_description", "§5.1 Episode"),
    ("skill_group", "§5.1 Episode"),
    ("task_difficulty", "§5.1 Episode"),
    # §5.2 Environment & Location
    ("city", "§5.2 Environment & Location"),
    ("state_province", "§5.2 Environment & Location"),
    ("country", "§5.2 Environment & Location"),
    ("location", "§5.2 Environment & Location"),  # location = geo-hash
    ("environment_l1", "§5.2 Environment & Location"),
    ("environment_l2", "§5.2 Environment & Location"),
    ("environment_l3", "§5.2 Environment & Location"),
    ("environment_id", "§5.2 Environment & Location"),
    # §5.3 Ego Device
    ("device_kit_id", "§5.3 Ego Device"),
    ("device_id", "§5.3 Ego Device"),
    ("device_generation", "§5.3 Ego Device"),
    ("firmware_version", "§5.3 Ego Device"),
    # §5.6 Operator & Physical
    ("age_range", "§5.6 Operator & Physical"),
    ("gender", "§5.6 Operator & Physical"),
    ("handedness", "§5.6 Operator & Physical"),
]

OPTIONAL_BASE: List[Tuple[str, str]] = [
    # §5.2
    ("business_id", "§5.2 Environment & Location"),
    ("business_name", "§5.2 Environment & Location"),
    # §5.6
    ("role_experience", "§5.6 Operator & Physical"),
    ("task_experience", "§5.6 Operator & Physical"),
    ("collection_hours_today", "§5.6 Operator & Physical"),
    ("collection_experience_days", "§5.6 Operator & Physical"),
    ("hand_size_cm", "§5.6 Operator & Physical"),
]

HANDHELD_REQUIRED: List[Tuple[str, str]] = [
    ("handheld_device_id", "§5.4 Handheld/UMI"),
    ("handheld_device_generation", "§5.4 Handheld/UMI"),
    ("handheld_device_urdf", "§5.4 Handheld/UMI"),
]

GLOVE_REQUIRED: List[Tuple[str, str]] = [
    ("glove_device_id", "§5.5 Glove"),
    ("glove_generation", "§5.5 Glove"),
    ("glove_firmware_version", "§5.5 Glove"),
    ("glove_hand_model", "§5.5 Glove"),
    ("glove_tracking_method", "§5.5 Glove"),
    ("glove_tactile_resolution", "§5.5 Glove"),
    ("glove_sampling_rate_hz", "§5.5 Glove"),
]

# Accepted alternate spellings beyond dash/underscore (canon_key already unifies
# those). Keyed by canonical field name.
ALIASES: Dict[str, List[str]] = {
    "episode_uuid": ["episode-uuid", "session-uuid", "session_uuid", "uuid"],
    "task_description": ["instruction", "description"],
    "location": ["geo_hash", "geohash", "geo-hash"],
    "operator_consent": ["consent"],
}

# Profile trigger prefixes: a non-empty field whose canonical key starts with one
# of these activates the corresponding conditional profile (§4.2).
HANDHELD_PREFIX = "handheld"
GLOVE_PREFIX = "glove"

# ---------------------------------------------------------------------------
# Derived lookups (built once from the editable lists above).
# ---------------------------------------------------------------------------


def field_canons(field: str) -> frozenset:
    """Canonical token set for a field: its own name plus any aliases."""
    return canon_set([field, *ALIASES.get(field, [])])


def _all_fields() -> List[Tuple[str, str]]:
    return REQUIRED_BASE + OPTIONAL_BASE + HANDHELD_REQUIRED + GLOVE_REQUIRED


#: field name -> section label, across every tier.
SECTION_OF: Dict[str, str] = {name: section for name, section in _all_fields()}

#: field name -> canonical token set, across every tier.
CANONS_OF: Dict[str, frozenset] = {name: field_canons(name) for name, _ in _all_fields()}

#: Union of every canonical token known to the schema (fields + aliases). Used
#: by the extras algorithm to decide whether a path segment is "known".
SCHEMA_CANONS: frozenset = frozenset().union(*CANONS_OF.values())


def rebuild() -> None:
    """Recompute the derived canon tables from ``ALIASES``.

    Called after a source profile merges extra alias spellings, so both field
    resolution and the extras algorithm pick them up. The generic (no-profile)
    run never calls this, so default behaviour is unchanged.
    """
    global CANONS_OF, SCHEMA_CANONS
    CANONS_OF = {name: field_canons(name) for name, _ in _all_fields()}
    SCHEMA_CANONS = frozenset().union(*CANONS_OF.values())

# Convenience name-only lists per tier.
REQUIRED_BASE_FIELDS = [n for n, _ in REQUIRED_BASE]
OPTIONAL_BASE_FIELDS = [n for n, _ in OPTIONAL_BASE]
HANDHELD_FIELDS = [n for n, _ in HANDHELD_REQUIRED]
GLOVE_FIELDS = [n for n, _ in GLOVE_REQUIRED]
