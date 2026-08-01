"""Layer B -- metadata presence, validity and internal consistency (PRD §7).

One function per check (B1.1 .. B2.7). Each takes the loaded Package plus a small
context and returns exactly one check record, whatever the outcome. Pure and
I/O-free: everything is read from the already-ingested Package.

Fields that Doc 2 requires but that have *no location in the capture format*
(B1.7, B1.10, B1.11, B2.5, B2.6, B2.7) emit under the FORMAT_GAP category -- a
capture-app change request, not a vendor data error (PRD §7 note).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, List, Optional

from . import normalize as N
from . import ruleset as R
from . import sentinel
from .checks import check, passed
from .models import Package
from .normalize import canon_set

SRC_META = "metadata.json"


@dataclass
class Context:
    """Per-run inputs a check may need beyond the package itself."""
    now_unix: float


# ---------------------------------------------------------------------------
# Small resolution helpers
# ---------------------------------------------------------------------------
def _field(pkg: Package, name: str) -> Any:
    """Resolve a registry field's value from the flattened metadata.

    Priority-ordered: the canonical spelling wins over a fallback whenever both
    are present, so the outcome never depends on JSON key order.
    """
    return N.value_in_order(pkg.meta_flat, R.FIELD_CANON_ORDER[name])


def _raw(pkg: Package, *spellings: str) -> Any:
    """Resolve an ad-hoc field (for FORMAT_GAP candidates not in the registry)."""
    return N.value(pkg.meta_flat, canon_set(spellings))


def _parse_utc(value: Any) -> Optional[float]:
    """Parse a unix-seconds number or an ISO-8601 string into unix seconds."""
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def _format_gap(cid: str, name: str, code: str, spec_ref: str,
                what: str) -> dict:
    return check(cid, name, R.FORMAT_GAP, code=code, spec_ref=spec_ref,
                 source_file=SRC_META, expected=what,
                 message=f"{what} -- no location in the capture format; "
                         f"capture-app change request, not a vendor data error")


# ===========================================================================
# B1 -- core episode metadata
# ===========================================================================
def b1_1_episode_uuid_valid(pkg: Package, ctx: Context) -> dict:
    name, cid, code = "episode_uuid_valid", "B1.1", "EPISODE_UUID_INVALID"
    val = _field(pkg, "episode_uuid")
    if N.is_empty(val):
        return check(cid, name, R.ERROR, code=code, source_file=SRC_META,
                     spec_ref="Doc1 §5.1", expected="a UUID",
                     message="episode_uuid is missing")
    try:
        uuid.UUID(str(val))
    except (ValueError, AttributeError, TypeError):
        return check(cid, name, R.ERROR, code=code, source_file=SRC_META,
                     spec_ref="Doc1 §5.1", observed=val, expected="a UUID",
                     message=f"episode_uuid '{val}' does not parse as a UUID")
    return passed(cid, name, observed=val, source_file=SRC_META, spec_ref="Doc1 §5.1")


def b1_2_operator_id_valid(pkg: Package, ctx: Context) -> dict:
    name, cid, code = "operator_id_valid", "B1.2", "OPERATOR_ID_INVALID"
    val = _field(pkg, "operator_id")
    is_bad, reason = sentinel.classify(val) if not N.is_empty(val) else (True, "missing")
    if N.is_empty(val) or is_bad:
        return check(cid, name, R.ERROR, code=code, source_file=SRC_META,
                     spec_ref="Doc1 §5.1", observed=val, expected="stable non-empty id",
                     message=f"operator_id invalid: {reason}")
    return passed(cid, name, observed=val, source_file=SRC_META, spec_ref="Doc1 §5.1")


def b1_3_start_time_plausible(pkg: Package, ctx: Context) -> dict:
    name, cid, code = "episode_start_time_plausible", "B1.3", "START_TIME_IMPLAUSIBLE"
    val = _field(pkg, "episode_start_time")
    secs = _parse_utc(val)
    hi = ctx.now_unix + R.START_TIME_FUTURE_SLACK_S
    expected = f"unix seconds in [{R.START_TIME_MIN_UNIX}, now+24h]"
    if secs is None:
        return check(cid, name, R.ERROR, code=code, source_file=SRC_META,
                     spec_ref="Doc2 §1", observed=val, expected=expected,
                     message="episode_start_time missing or unparseable")
    if not (R.START_TIME_MIN_UNIX <= secs <= hi):
        return check(cid, name, R.ERROR, code=code, source_file=SRC_META,
                     spec_ref="Doc2 §1", observed=secs, expected=expected,
                     message=f"episode_start_time {secs} outside plausible window")
    return passed(cid, name, observed=secs, source_file=SRC_META, spec_ref="Doc2 §1")


def b1_4_device_identified(pkg: Package, ctx: Context) -> dict:
    name, cid, code = "device_identified", "B1.4", "DEVICE_UNDERSPECIFIED"
    dtype = _field(pkg, "device_type")
    dmodel = _field(pkg, "device_model")
    for label, val in (("device_type", dtype), ("device_model", dmodel)):
        if N.is_empty(val):
            return check(cid, name, R.ERROR, code=code, source_file=SRC_META,
                         spec_ref="Doc1 §5.3", expected="non-empty, non-generic",
                         message=f"{label} is missing")
        is_bad, reason = sentinel.classify(val, key_name=label, generic_device=True)
        if is_bad:
            return check(cid, name, R.ERROR, code=code, source_file=SRC_META,
                         spec_ref="Doc1 §5.3", observed=val,
                         expected="identifies hardware generation",
                         message=f"{label} underspecified: {reason}")
    return passed(cid, name, observed=f"{dtype} / {dmodel}", source_file=SRC_META,
                  spec_ref="Doc1 §5.3")


_SEMVER_CHARS = set("0123456789.")


def b1_5_software_version_present(pkg: Package, ctx: Context) -> dict:
    name, cid, code = "software_version_present", "B1.5", "SOFTWARE_VERSION_MISSING"
    val = _field(pkg, "software_version")
    if N.is_empty(val):
        return check(cid, name, R.ERROR, code=code, source_file=SRC_META,
                     spec_ref="Doc2 §2", expected="non-empty, semver-like",
                     message="software_version is missing")
    text = str(val)
    semverish = any(c.isdigit() for c in text) and set(text.strip()) <= _SEMVER_CHARS | set("v-+")
    if not semverish:
        return check(cid, name, R.WARN, code=code, source_file=SRC_META,
                     spec_ref="Doc2 §2", observed=val, expected="semver-like",
                     message=f"software_version '{val}' is not semver-like")
    return passed(cid, name, observed=val, source_file=SRC_META, spec_ref="Doc2 §2")


def _stream_kind(token: str) -> Optional[str]:
    """Map a capture app's stream token onto {rgb, depth}.

    ``lidar`` and ``tof`` are depth sensors under a different name -- the iOS
    capture app names the sensor, not the stream it produces.
    """
    t = token.lower()
    if any(k in t for k in ("depth", "lidar", "tof")):
        return "depth"
    if any(k in t for k in ("rgb", "ego", "color", "wide", "camera")):
        return "rgb"
    return None


#: Keys under which a config record may carry its stream name(s). Capture apps
#: disagree; adding a spelling is a one-line change here.
_STREAM_KEYS = ("stream", "streams", "name", "type",
                "cameras_present", "camera_name", "sensors")


def _config_tokens(value: Any) -> List[str]:
    """Flatten a camera_configuration value into candidate stream tokens.

    Handles a bare string, a list, a dict of key/values, and a list of records
    such as ``[{"mounting_locations": …, "cameras_present": ["lidar"]}]`` --
    recursing so a nested list of sensor names is not lost.
    """
    out: List[str] = []
    if value is None:
        return out
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        for key in _STREAM_KEYS:
            if key in value:
                out.extend(_config_tokens(value[key]))
        if not out:                       # fall back to the whole record
            out.extend(str(k) for k in value)
            for v in value.values():
                if isinstance(v, (str, list)):
                    out.extend(_config_tokens(v))
        return out
    if isinstance(value, list):
        for item in value:
            out.extend(_config_tokens(item))
        return out
    return [str(value)]


def _config_streams(value: Any) -> set:
    """Normalize a camera_configuration value into a {rgb, depth} stream set."""
    kinds = set()
    for token in _config_tokens(value):
        kind = _stream_kind(str(token))
        if kind:
            kinds.add(kind)
    return kinds


def b1_6_camera_config_complete(pkg: Package, ctx: Context) -> dict:
    name, cid, code = "camera_config_complete", "B1.6", "CAMERA_CONFIG_MISMATCH"
    present = set()
    if pkg.camera_intrinsic is not None:
        present.add("rgb")
    if pkg.depth_frames is not None:
        present.add("depth")
    config = _config_streams(_field(pkg, "camera_configuration"))
    missing = present - config          # streams in the package, absent from config
    extra = config - present            # streams in config, absent from the package
    if missing or extra:
        parts = []
        if missing:
            parts.append(f"present but not in config: {sorted(missing)}")
        if extra:
            parts.append(f"in config but not present: {sorted(extra)}")
        return check(cid, name, R.ERROR, code=code, source_file=SRC_META,
                     spec_ref="Doc2 §2",
                     observed=f"package={sorted(present)}, config={sorted(config)}",
                     expected="every present stream configured, and vice versa",
                     message="; ".join(parts))
    return passed(cid, name, observed=sorted(present), source_file=SRC_META,
                  spec_ref="Doc2 §2")


def b1_7_task_description_present(pkg: Package, ctx: Context) -> dict:
    name, cid, code = "task_description_present", "B1.7", "TASK_DESCRIPTION_MISSING"
    val = _raw(pkg, "task_description", "description")
    if not N.is_empty(val) and not sentinel.is_sentinel(val):
        return passed(cid, name, observed=val, source_file=SRC_META, spec_ref="Doc2 §2 (D-6)")
    return _format_gap(cid, name, code, "Doc2 §2 (D-6)", "free-text task description")


def b1_8_task_category_valid(pkg: Package, ctx: Context) -> dict:
    name, cid, code = "task_category_valid", "B1.8", "TASK_CATEGORY_INVALID"
    val = _field(pkg, "task_category")
    if N.is_empty(val):
        return check(cid, name, R.ERROR, code=code, source_file=SRC_META,
                     spec_ref="Doc1 §5.1", expected="non-empty, in vocabulary",
                     message="task_category is missing")
    is_bad, reason = sentinel.classify(val, key_name="task_category")
    if is_bad:
        return check(cid, name, R.ERROR, code=code, source_file=SRC_META,
                     spec_ref="Doc1 §5.1", observed=val, expected="a real category",
                     message=f"task_category invalid: {reason}")
    if R.TASK_CATEGORY_VOCAB and sentinel.normalize(str(val)) not in R.TASK_CATEGORY_VOCAB:
        return check(cid, name, R.ERROR, code=code, source_file=SRC_META,
                     spec_ref="Doc1 §5.1", observed=val, expected="in controlled vocabulary",
                     message=f"task_category '{val}' not in controlled vocabulary")
    return passed(cid, name, observed=val, source_file=SRC_META, spec_ref="Doc1 §5.1")


def b1_9_environment_labelled(pkg: Package, ctx: Context) -> dict:
    name, cid, code = "environment_labelled", "B1.9", "ENVIRONMENT_LABEL_MISSING"
    label = _field(pkg, "environment_label")
    env_id = _field(pkg, "environment_id")
    missing = [n for n, v in (("environment_label", label), ("environment_id", env_id))
               if N.is_empty(v)]
    if missing:
        return check(cid, name, R.ERROR, code=code, source_file=SRC_META,
                     spec_ref="Doc1 §5.2", expected="both non-empty",
                     message=f"missing: {', '.join(missing)}")
    # Presence alone is not enough: a label of "Unknown" carries no more
    # information than an empty one (PRD §9), so screen it like every other
    # declared string field.
    for field_name, value in (("environment_label", label), ("environment_id", env_id)):
        is_bad, reason = sentinel.classify(value, key_name=field_name)
        if is_bad:
            return check(cid, name, R.ERROR, code=code, source_file=SRC_META,
                         spec_ref="Doc1 §5.2", observed=value,
                         expected="a real environment label/id",
                         message=f"{field_name} invalid: {reason}")
    return passed(cid, name, observed=f"{label} / {env_id}", source_file=SRC_META,
                  spec_ref="Doc1 §5.2")


def b1_10_indoor_outdoor_declared(pkg: Package, ctx: Context) -> dict:
    name, cid, code = "indoor_outdoor_declared", "B1.10", "INDOOR_OUTDOOR_MISSING"
    val = _raw(pkg, "indoor_outdoor", "indoor_or_outdoor", "scene_setting")
    if N.is_empty(val):
        return _format_gap(cid, name, code, "Doc2 §1", "indoor/outdoor declaration")
    if sentinel.normalize(str(val)) not in R.INDOOR_OUTDOOR_VALUES:
        return check(cid, name, R.ERROR, code=code, source_file=SRC_META,
                     spec_ref="Doc2 §1", observed=val,
                     expected=f"one of {sorted(R.INDOOR_OUTDOOR_VALUES)}",
                     message=f"indoor/outdoor value '{val}' not recognized")
    return passed(cid, name, observed=val, source_file=SRC_META, spec_ref="Doc2 §1")


def b1_11_episode_end_time_present(pkg: Package, ctx: Context) -> dict:
    name, cid, code = "episode_end_time_present", "B1.11", "END_TIME_MISSING"
    end = _raw(pkg, "episode_end_time", "end_time_unix", "end_time", "end_timestamp")
    if N.is_empty(end):
        return _format_gap(cid, name, code, "Doc2 §1", "episode end time")
    end_s = _parse_utc(end)
    start_s = _parse_utc(_field(pkg, "episode_start_time"))
    if end_s is None or start_s is None or end_s <= start_s:
        return check(cid, name, R.ERROR, code=code, source_file=SRC_META,
                     spec_ref="Doc2 §1", observed=end,
                     expected="present and > episode_start_time",
                     message="episode_end_time is not after episode_start_time")
    return passed(cid, name, observed=end_s, source_file=SRC_META, spec_ref="Doc2 §1")


# ===========================================================================
# B2 -- ids, timestamps, cross-file anchors
# ===========================================================================
def b2_1_task_id_present(pkg: Package, ctx: Context) -> dict:
    name, cid, code = "task_id_present", "B2.1", "TASK_ID_MISSING"
    val = _field(pkg, "task_id")
    if N.is_empty(val):
        return check(cid, name, R.ERROR, code=code, source_file=SRC_META,
                     spec_ref="Doc1 §5.1", expected="non-empty", message="task_id is missing")
    return passed(cid, name, observed=val, source_file=SRC_META, spec_ref="Doc1 §5.1")


def b2_2_environment_id_present(pkg: Package, ctx: Context) -> dict:
    name, cid, code = "environment_id_present", "B2.2", "ENVIRONMENT_ID_MISSING"
    val = _field(pkg, "environment_id")
    if N.is_empty(val):
        return check(cid, name, R.ERROR, code=code, source_file=SRC_META,
                     spec_ref="Doc1 §5.2", expected="non-empty",
                     message="environment_id is missing")
    return passed(cid, name, observed=val, source_file=SRC_META, spec_ref="Doc1 §5.2")


def b2_3_frame_timestamps_present(pkg: Package, ctx: Context) -> dict:
    name, cid, code = "frame_timestamps_present", "B2.3", "FRAME_TIMESTAMPS_INVALID"
    src = "video_timestamps.json"
    ts = pkg.video_timestamps
    if not ts:
        return check(cid, name, R.ERROR, code=code, source_file=src,
                     spec_ref="Doc2 §1", expected="non-empty, strictly monotonic",
                     message="no per-frame timestamps present")
    non_monotonic = [i for i in range(1, len(ts)) if ts[i] <= ts[i - 1]]
    if non_monotonic:
        return check(cid, name, R.ERROR, code=code, source_file=src,
                     spec_ref="Doc2 §1", observed=f"{len(non_monotonic)} non-increasing step(s)",
                     expected="strictly monotonic",
                     message="frame timestamps are not strictly increasing")
    return passed(cid, name, observed=f"{len(ts)} frames", source_file=src, spec_ref="Doc2 §1")


def b2_4_utc_reference_present(pkg: Package, ctx: Context) -> dict:
    name, cid, code = "utc_reference_present", "B2.4", "UTC_REFERENCE_INCONSISTENT"
    start_s = _parse_utc(_field(pkg, "episode_start_time"))
    # A UTC anchor may live in the motion metadata block or the manifest.
    anchor = None
    anchor_src = None
    if pkg.motion_data:
        flat_motion = N.flatten(pkg.motion_data)
        anchor = N.value(flat_motion, canon_set(["recording_date", "utc", "utc_time",
                                                 "recorded_at", "capture_time"]))
        anchor_src = "motion_data.json"
    if N.is_empty(anchor) and pkg.manifest:
        anchor = pkg.manifest.get("generated_at")
        anchor_src = "manifest.json"
    if N.is_empty(anchor) or start_s is None:
        return check(cid, name, R.ERROR, code=code, source_file=anchor_src or SRC_META,
                     spec_ref="Doc2 §1", expected="a UTC anchor agreeing with start time",
                     message="no UTC anchor found to cross-check episode_start_time")
    anchor_s = _parse_utc(anchor)
    if anchor_s is None:
        return check(cid, name, R.ERROR, code=code, source_file=anchor_src,
                     spec_ref="Doc2 §1", observed=anchor, expected="parseable UTC",
                     message=f"UTC anchor '{anchor}' unparseable")
    diff = abs(anchor_s - start_s)
    if diff > R.UTC_AGREEMENT_TOL_S:
        return check(cid, name, R.ERROR, code=code, source_file=anchor_src,
                     spec_ref="Doc2 §1", observed=f"{diff:.3f}s apart",
                     expected=f"within {R.UTC_AGREEMENT_TOL_S:g}s",
                     message=f"UTC anchor disagrees with episode_start_time by {diff:.3f}s")
    return passed(cid, name, observed=f"{diff:.3f}s apart", source_file=anchor_src,
                  spec_ref="Doc2 §1")


def b2_5_geo_location_present(pkg: Package, ctx: Context) -> dict:
    name, cid, code = "geo_location_present", "B2.5", "GEO_LOCATION_MISSING"
    lat = _raw(pkg, "latitude", "lat")
    lon = _raw(pkg, "longitude", "lon", "lng")
    if not N.is_empty(lat) and not N.is_empty(lon):
        return passed(cid, name, observed=f"{lat},{lon}", source_file=SRC_META,
                      spec_ref="Doc2 §2")
    return _format_gap(cid, name, code, "Doc2 §2", "geo lat/lon")


def b2_6_subtask_annotations_present(pkg: Package, ctx: Context) -> dict:
    name, cid, code = "subtask_annotations_present", "B2.6", "SUBTASK_ANNOTATIONS_MISSING"
    val = _raw(pkg, "subtask_annotations", "subtasks", "annotations")
    if not N.is_empty(val):
        return passed(cid, name, source_file=SRC_META, spec_ref="Doc2 §2")
    return _format_gap(cid, name, code, "Doc2 §2", "timestamped subtask annotations")


def b2_7_instruction_present(pkg: Package, ctx: Context) -> dict:
    name, cid, code = "instruction_present", "B2.7", "INSTRUCTION_MISSING"
    val = _raw(pkg, "instruction")
    if not N.is_empty(val) and not sentinel.is_sentinel(val):
        return passed(cid, name, observed=val, source_file=SRC_META, spec_ref="Doc2 §2")
    return _format_gap(cid, name, code, "Doc2 §2", "non-empty instruction string")


# Ordered list of Layer B checks.
# B1.1 is NOT here: episode identity belongs to the session, not a chunk (a
# chunk's ``episode_uuid`` holds the chunk label). See session_checks.py.
CHECKS = [
    b1_2_operator_id_valid, b1_3_start_time_plausible,
    b1_4_device_identified, b1_5_software_version_present, b1_6_camera_config_complete,
    b1_7_task_description_present, b1_8_task_category_valid, b1_9_environment_labelled,
    b1_10_indoor_outdoor_declared, b1_11_episode_end_time_present,
    b2_1_task_id_present, b2_2_environment_id_present, b2_3_frame_timestamps_present,
    b2_4_utc_reference_present, b2_5_geo_location_present,
    b2_6_subtask_annotations_present, b2_7_instruction_present,
]


def run(pkg: Package, ctx: Context) -> List[dict]:
    return [fn(pkg, ctx) for fn in CHECKS]
