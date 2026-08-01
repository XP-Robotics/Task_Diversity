"""Versioned ruleset for the Layer B + cross-file (Layer C) episode validator.

Everything that a spec revision or a landed blocking-decision (§4) can change
lives here, so tuning a threshold or a field location never requires touching
check logic (PRD §11.5 -- "version the ruleset separately from the code").

Nothing in this module does I/O.
"""

from __future__ import annotations

from typing import Dict, FrozenSet, List, Tuple

from .normalize import canon_key, canon_set

# ---------------------------------------------------------------------------
# Identity (emitted on every report -- PRD §5.2)
# ---------------------------------------------------------------------------
SPEC_VERSION = "human-egocentric-umi-2026-04-09"
PIPELINE_VERSION = "0.1.0"

# ---------------------------------------------------------------------------
# Severity levels (PRD §5.1) and their delivery-level meaning.
#   ERROR      spec-required condition violated by delivered data  -> reject
#   WARN       preferred condition unmet / recoverable ambiguity    -> accept, flag
#   INFO       observation, no judgement
#   BLOCKED    cannot evaluate (spec gap / missing prerequisite)    -> hold for review
#   CEILING    hardware-unmeetable requirement (reported per delivery, not episode)
#   FORMAT_GAP required field has no location in the capture format -> capture-app
#              change request, NOT attributed to the vendor (PRD §7 note)
#   PASS       check ran and the condition held
# ---------------------------------------------------------------------------
ERROR = "ERROR"
WARN = "WARN"
INFO = "INFO"
BLOCKED = "BLOCKED"
CEILING = "CEILING"
FORMAT_GAP = "FORMAT_GAP"
PASS = "PASS"

SEVERITIES: List[str] = [ERROR, WARN, INFO, BLOCKED, CEILING, FORMAT_GAP, PASS]

# Which severities block acceptance / count as vendor fault.
BLOCKING = {ERROR}          # -> verdict REJECT
REVIEW = {BLOCKED}          # -> verdict REVIEW (held, not vendor fault)
# WARN, INFO, CEILING, FORMAT_GAP never block and are not vendor data errors.

VERDICT_REJECT = "REJECT"
VERDICT_REVIEW = "REVIEW"
VERDICT_ACCEPT = "ACCEPT"

# ---------------------------------------------------------------------------
# Blocking-decision defaults (PRD §4). Encoded so the eventual answers are a
# one-line edit here, not a code change.
# ---------------------------------------------------------------------------
# D-2: one episode == one chunk; a package with >1 metadata record fails.
MAX_METADATA_RECORDS = 1

# ---------------------------------------------------------------------------
# Layer B thresholds
# ---------------------------------------------------------------------------
# B1.3 -- episode_start_time plausibility window (unix seconds).
START_TIME_MIN_UNIX = 1_704_067_200        # 2024-01-01T00:00:00Z
START_TIME_FUTURE_SLACK_S = 24 * 3600      # now + 24h

# B2.4 -- a UTC anchor must agree with episode_start_time within this tolerance.
UTC_AGREEMENT_TOL_S = 1.0

# B1.8 -- controlled vocabulary for task_category. Empty == not enforced yet
# (a non-empty, non-sentinel value passes); populate when the vocab lands.
TASK_CATEGORY_VOCAB: FrozenSet[str] = frozenset()

# B1.10 -- accepted indoor/outdoor values (field is a FORMAT_GAP today).
INDOOR_OUTDOOR_VALUES = frozenset({"indoor", "outdoor", "mixed"})

# ---------------------------------------------------------------------------
# Layer C thresholds
# ---------------------------------------------------------------------------
# C2 -- declared durations must match the authoritative timestamp span within.
DURATION_CONCORDANCE_TOL_S = 0.1
# C3 -- an IMU inter-sample gap above (factor x nominal video interval) is a gap.
IMU_GAP_FACTOR = 5.0

# ---------------------------------------------------------------------------
# Field location registry (canonical field -> accepted spellings). canon_key()
# already unifies case / dash / underscore / camelCase, so these list only the
# genuine synonyms. Editing a field's location is a one-line change here.
# ---------------------------------------------------------------------------
_FIELD_ALIASES: Dict[str, List[str]] = {
    # ``session_id`` is a FALLBACK, deliberately last: some capture apps carry
    # the episode's UUID there. Order matters (see FIELD_CANON_ORDER) -- when a
    # record declares ``episode_uuid`` it always wins, even if the value is
    # rubbish, so an app writing the chunk name into that field is reported by
    # B1.1 rather than silently patched over from elsewhere. Uniqueness (B1.1u)
    # applies to whichever value resolves, so a session id shared across several
    # chunks fails loudly.
    "episode_uuid": ["episode_uuid", "session_uuid", "uuid", "session_id"],
    "operator_id": ["operator_id"],
    "episode_start_time": ["episode_start_time", "start_time_unix", "start_time",
                           "start_timestamp"],
    "device_type": ["device_type"],
    "device_model": ["device_model"],
    "software_version": ["software_version", "app_version"],
    "camera_configuration": ["camera_configuration", "camera_config"],
    "task_category": ["task_category"],
    "environment_label": ["environment_label", "environment_l1"],
    "environment_id": ["environment_id"],
    "task_id": ["task_id"],
}

#: canonical field name -> frozenset of canonical tokens that resolve it.
FIELD_CANONS: Dict[str, FrozenSet[str]] = {
    name: canon_set(spellings) for name, spellings in _FIELD_ALIASES.items()
}

#: The same tokens in *priority* order. Where a record carries more than one
#: accepted spelling, the earlier entry wins -- so the canonical name always
#: beats a fallback, regardless of the order keys happen to appear in the JSON.
FIELD_CANON_ORDER: Dict[str, Tuple[str, ...]] = {
    name: tuple(canon_key(s) for s in spellings)
    for name, spellings in _FIELD_ALIASES.items()
}
