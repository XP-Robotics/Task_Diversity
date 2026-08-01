"""Layer C -- cross-file referential & consistency checks (PRD §8).

These are not in the source spec; they are derived from the observed package
format and catch defects per-field validation cannot. All rates and spans are
computed from actual timestamp data, never from declared metadata (PRD §11.3).

Scope note: C1's "video nb_frames" leg needs a decode (Layer A) and is omitted
here -- concordance is checked across the JSON-declared stream frame counts.
Pure and I/O-free.
"""

from __future__ import annotations

from typing import Any, List, Optional, Tuple

from . import normalize as N
from . import ruleset as R
from .checks import check, passed
from .models import Package
from .normalize import canon_set

# Declared episode durations. Deliberately EXCLUDES motion_data's
# ``recording_duration``: it is observed to be a constant 60.000 (the configured
# chunk length, not a measurement), so treating it as a declaration would fail
# every package for a value nobody ever computed.
_DURATION_CANONS = canon_set(["duration", "duration_seconds", "duration_s", "length_seconds",
                              "total_duration"])
_TS_CANONS = canon_set(["timestamps", "imu_timestamps", "sample_timestamps", "times"])


# ---------------------------------------------------------------------------
# Shared measurements
# ---------------------------------------------------------------------------
def _span(ts: Optional[List[float]]) -> Optional[float]:
    if ts and len(ts) >= 2:
        return ts[-1] - ts[0]
    return None


def _median(values: List[float]) -> Optional[float]:
    if not values:
        return None
    s = sorted(values)
    mid = len(s) // 2
    return s[mid] if len(s) % 2 else (s[mid - 1] + s[mid]) / 2.0


def _nominal_interval(ts: Optional[List[float]]) -> Optional[float]:
    if not ts or len(ts) < 2:
        return None
    return _median([ts[i] - ts[i - 1] for i in range(1, len(ts))])


def _floats(seq) -> Optional[List[float]]:
    out = []
    for v in seq:
        try:
            out.append(float(v))
        except (TypeError, ValueError):
            pass
    return out or None


def _columnar_timestamps(motion: dict) -> Optional[List[float]]:
    """Pull the timestamp column out of a columnar IMU dump.

    Some capture apps ship motion data as ``{"columns": ["timestamp", "gyro_x",
    …], "data": [[…], …]}`` rather than a named timestamps array. Without this
    the samples are invisible and C3 reports BLOCKED on a package that does
    carry IMU data.
    """
    columns = motion.get("columns")
    rows = motion.get("data")
    if not isinstance(columns, list) or not isinstance(rows, list) or not rows:
        return None
    idx = next((i for i, c in enumerate(columns)
                if N.canon_key(c) in _TS_CANONS or N.canon_key(c) == "timestamp"), None)
    if idx is None:
        return None
    return _floats(r[idx] for r in rows
                   if isinstance(r, (list, tuple)) and len(r) > idx)


def _imu_timestamps(pkg: Package) -> Optional[List[float]]:
    if not pkg.motion_data:
        return None
    flat = N.flatten(pkg.motion_data)
    hit = N.value(flat, _TS_CANONS)
    if isinstance(hit, list):
        return _floats(hit)
    # Columnar layout: columns/data may sit at the top level or under metadata.
    for block in (pkg.motion_data, pkg.motion_data.get("metadata")):
        if isinstance(block, dict):
            merged = dict(block)
            merged.setdefault("data", pkg.motion_data.get("data"))
            found = _columnar_timestamps(merged)
            if found:
                return found
    return None


def _declared_durations(pkg: Package) -> List[Tuple[str, float]]:
    """Every explicitly-declared duration value, with its source file."""
    found: List[Tuple[str, float]] = []
    sources = [("metadata.json", pkg.meta_flat)]
    if pkg.motion_data:
        sources.append(("motion_data.json", N.flatten(pkg.motion_data)))
    for src, flat in sources:
        for path, val in flat.items():
            leaf = path.rsplit(".", 1)[-1]
            if N.canon_key(leaf) in _DURATION_CANONS and isinstance(val, (int, float)):
                found.append((f"{src}:{path}", float(val)))
    return found


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------
def c1_frame_count_concordance(pkg: Package) -> dict:
    name, cid, code = "frame_count_concordance", "C1", "FRAME_COUNT_MISMATCH"
    counts = {}
    if pkg.video_timestamps is not None:
        counts["video_timestamps"] = len(pkg.video_timestamps)
    if pkg.hands_frames is not None:
        counts["hands"] = len(pkg.hands_frames)
    if pkg.ar_session_frame_count is not None:
        counts["ar_session"] = pkg.ar_session_frame_count
    if pkg.depth_frames is not None:
        counts["depth"] = len(pkg.depth_frames)
    if len(counts) < 2:
        return check(cid, name, R.BLOCKED, code=code, spec_ref="§8 (derived)",
                     observed=counts, expected="≥2 streams to compare",
                     message="too few per-stream frame counts to check concordance")
    distinct = set(counts.values())
    if len(distinct) > 1:
        return check(cid, name, R.ERROR, code=code, spec_ref="§8 (derived)",
                     observed=counts, expected="all stream frame counts equal",
                     message=f"frame counts disagree across streams: {counts}")
    return passed(cid, name, observed=counts, spec_ref="§8 (derived)")


def c2_duration_concordance(pkg: Package) -> dict:
    name, cid, code = "duration_concordance", "C2", "DURATION_DECLARATION_CONFLICT"
    span = _span(pkg.video_timestamps)
    if span is None:
        return check(cid, name, R.BLOCKED, code=code, spec_ref="§8 (derived)",
                     expected="an authoritative timestamp span",
                     message="no video timestamp span to anchor durations")
    declared = _declared_durations(pkg)
    conflicts = [(src, val) for src, val in declared
                 if abs(val - span) > R.DURATION_CONCORDANCE_TOL_S]
    if conflicts:
        detail = ", ".join(f"{src}={val:g}s" for src, val in conflicts)
        return check(cid, name, R.ERROR, code=code, source_file="video_timestamps.json",
                     spec_ref="§8 (derived)",
                     observed=f"span={span:.4g}s vs {detail}",
                     expected=f"within {R.DURATION_CONCORDANCE_TOL_S:g}s of the span",
                     message=f"declared duration(s) conflict with the {span:.4g}s timestamp span")
    return passed(cid, name, observed=f"span={span:.4g}s, {len(declared)} declaration(s) agree",
                  spec_ref="§8 (derived)")


def c3_imu_covers_video_span(pkg: Package) -> dict:
    name, cid, code = "imu_covers_video_span", "C3", "IMU_COVERAGE_GAP"
    vts, imu = pkg.video_timestamps, _imu_timestamps(pkg)
    if not vts or len(vts) < 2 or not imu or len(imu) < 2:
        return check(cid, name, R.BLOCKED, code=code, spec_ref="§8 (derived)",
                     expected="video + IMU timestamps",
                     message="missing video or IMU timestamps to compare spans")
    nominal = _nominal_interval(vts) or 0.0
    max_gap = max(imu[i] - imu[i - 1] for i in range(1, len(imu)))
    covers = imu[0] <= vts[0] + 1e-9 and imu[-1] >= vts[-1] - 1e-9
    gap_ok = nominal <= 0 or max_gap <= R.IMU_GAP_FACTOR * nominal
    if not covers or not gap_ok:
        reasons = []
        if not covers:
            reasons.append(f"IMU span [{imu[0]:.4g},{imu[-1]:.4g}] does not cover "
                           f"video [{vts[0]:.4g},{vts[-1]:.4g}]")
        if not gap_ok:
            reasons.append(f"max IMU gap {max_gap:.4g}s > {R.IMU_GAP_FACTOR:g}x nominal {nominal:.4g}s")
        return check(cid, name, R.ERROR, code=code, source_file="motion_data.json",
                     spec_ref="§8 (derived)", observed="; ".join(reasons),
                     expected="IMU covers the video span with no large gaps",
                     message="; ".join(reasons))
    return passed(cid, name, observed=f"IMU [{imu[0]:.4g},{imu[-1]:.4g}] covers video span",
                  spec_ref="§8 (derived)")


def c4_referenced_files_exist(pkg: Package) -> dict:
    name, cid, code = "referenced_files_exist", "C4", "REFERENCED_FILE_MISSING"
    referenced = []
    for frame in (pkg.depth_frames or []):
        if isinstance(frame, dict) and isinstance(frame.get("file"), str):
            referenced.append(frame["file"])
    if not referenced:
        return passed(cid, name, observed="no indexed files referenced",
                      source_file="depth_camera_intrinsics.json", spec_ref="§8 (derived)")
    missing = [f for f in referenced if f.lower() not in pkg.depth_bins_present]
    if missing:
        shown = ", ".join(missing[:3]) + (" ..." if len(missing) > 3 else "")
        return check(cid, name, R.ERROR, code=code,
                     source_file="depth_camera_intrinsics.json", spec_ref="§8 (derived)",
                     observed=f"{len(missing)}/{len(referenced)} referenced files absent",
                     expected="every referenced file exists on disk",
                     message=f"missing referenced payload(s): {shown}")
    return passed(cid, name, observed=f"{len(referenced)} referenced files present",
                  source_file="depth_camera_intrinsics.json", spec_ref="§8 (derived)")


def c5_single_authoritative_video(pkg: Package) -> dict:
    name, cid, code = "single_authoritative_video", "C5", "AMBIGUOUS_VIDEO_SOURCE"
    videos = pkg.video_files
    if len(videos) == 1:
        return passed(cid, name, observed=videos, spec_ref="§8 (derived, D-3)")
    if not videos:
        return check(cid, name, R.BLOCKED, code=code, spec_ref="§8 (derived, D-3)",
                     expected="at least one video", message="no video present in package")
    declared = N.value(pkg.meta_flat, canon_set(["authoritative_video", "primary_video"]))
    if not N.is_empty(declared):
        return passed(cid, name, observed=f"authoritative={declared}", spec_ref="§8 (derived, D-3)")
    return check(cid, name, R.WARN, code=code, spec_ref="§8 (derived, D-3)",
                 observed=videos, expected="one video, or an authoritative one declared",
                 message=f"{len(videos)} videos present and none declared authoritative")


def _optical_sensors(pkg: Package) -> set:
    sensors = set()
    if pkg.camera_intrinsic is not None:
        sensors.add("rgb")
    if pkg.depth_frames is not None:
        sensors.add("depth")
    return sensors


def c6_sensor_relation_declared(pkg: Package) -> dict:
    name, cid, code = "sensor_relation_declared", "C6", "SENSOR_RELATION_UNDECLARED"
    sensors = _optical_sensors(pkg)
    if len(sensors) < 2:
        return passed(cid, name, observed=sorted(sensors),
                      spec_ref="§8 (derived, D-5)",
                      message="fewer than two optical sensors; relation not required")
    declared = N.value(pkg.meta_flat, canon_set(
        ["sensor_relation", "registration", "extrinsics", "depth_registration",
         "rgb_depth_extrinsic"]))
    if not N.is_empty(declared):
        return passed(cid, name, observed="relation declared", spec_ref="§8 (derived, D-5)")
    return check(cid, name, R.WARN, code=code, spec_ref="§8 (derived, D-5)",
                 observed=sorted(sensors),
                 expected="extrinsic transform or explicit pre-registration declaration",
                 message="two optical sensors but their spatial relation is undeclared")


def _flags_enabled(hmeta: Optional[dict]) -> List[str]:
    if not hmeta:
        return []
    on = []
    for key, val in hmeta.items():
        if "enabled" in N.canon_key(key) and val in (True, "true", "True", 1):
            on.append(key)
    return on


def c7_non_empty_payloads(pkg: Package) -> dict:
    name, cid, code = "non_empty_payloads", "C7", "EMPTY_PAYLOAD"
    enabled = _flags_enabled(pkg.hands_metadata)
    if not enabled:
        return passed(cid, name, observed="no streams flagged enabled",
                      source_file="hands_metadata.json", spec_ref="§8 (derived)")
    if pkg.hands_frames is None:
        return check(cid, name, R.WARN, code=code, source_file="hands.json",
                     spec_ref="§8 (derived)", observed=f"enabled={enabled}",
                     expected="non-empty hand data",
                     message="hand processing enabled but hands.json is absent/empty")
    non_empty = 0
    for frame in pkg.hands_frames:
        if isinstance(frame, dict):
            if frame.get("interactions") or frame.get("objects") or frame.get("hands"):
                non_empty += 1
        elif frame:
            non_empty += 1
    if non_empty == 0:
        return check(cid, name, R.WARN, code=code, source_file="hands.json",
                     spec_ref="§8 (derived)",
                     observed=f"{len(pkg.hands_frames)} frames, all empty; enabled={enabled}",
                     expected="non-empty data for enabled streams",
                     message="hand processing flags enabled but every frame is empty")
    return passed(cid, name, observed=f"{non_empty}/{len(pkg.hands_frames)} non-empty frames",
                  source_file="hands.json", spec_ref="§8 (derived)")


# Declaration keys each optical stream must carry. Presence only -- see below.
_INTRINSIC_DECLARATIONS = {
    "focal_length": canon_set(["focal_length", "focallength", "f"]),
    "principal_point": canon_set(["principal_point", "principalpoint", "c"]),
    "image_width": canon_set(["image_width", "width"]),
    "image_height": canon_set(["image_height", "height"]),
    "intrinsic_matrix": canon_set(["intrinsic_matrix", "camera_matrix", "k"]),
}

_DEPTH_GEOMETRY_KEYS = ("file", "bytes_per_row", "width", "height")


def c8_camera_intrinsics_declared(pkg: Package) -> dict:
    """The RGB intrinsics must exist and declare their geometry.

    Deliberately presence-only: whether ``image_width``/``image_height`` match
    the real video, or whether ``intrinsic_matrix`` agrees with
    ``focal_length``/``principal_point``, is NOT checked here. Validating those
    needs the decoded video (Layer A) and a numeric policy that has not been
    agreed; this check answers only "is the calibration declared at all".
    """
    name, cid, code = "camera_intrinsics_declared", "C8", "CAMERA_INTRINSICS_MISSING"
    if pkg.camera_intrinsic is None:
        if not pkg.video_files:
            return passed(cid, name, observed="no video and no intrinsics",
                          spec_ref="§8 (derived)",
                          message="no optical stream in the package; intrinsics not required")
        return check(cid, name, R.ERROR, code=code,
                     source_file="camera_intrinsic.json", spec_ref="§8 (derived)",
                     observed="absent", expected="camera intrinsics for the video stream",
                     message="a video is present but no camera intrinsics were found")

    flat = N.flatten(pkg.camera_intrinsic)
    missing = [field for field, canons in _INTRINSIC_DECLARATIONS.items()
               if N.is_empty(N.value(flat, canons))]
    if missing:
        return check(cid, name, R.ERROR, code=code,
                     source_file="camera_intrinsic.json", spec_ref="§8 (derived)",
                     observed=f"missing: {', '.join(missing)}",
                     expected=", ".join(_INTRINSIC_DECLARATIONS),
                     message=f"camera intrinsics present but incomplete: "
                             f"{', '.join(missing)} not declared")
    return passed(cid, name, observed=f"{len(_INTRINSIC_DECLARATIONS)} declarations present",
                  source_file="camera_intrinsic.json", spec_ref="§8 (derived)")


def c9_depth_geometry_declared(pkg: Package) -> dict:
    """Every depth frame record must declare its payload and geometry.

    Deliberately presence-only: ``bytes_per_row`` is checked for being declared,
    never for agreeing with the payload's real byte size. Stride correctness is
    out of scope by decision.
    """
    name, cid, code = "depth_geometry_declared", "C9", "DEPTH_GEOMETRY_UNDECLARED"
    frames = pkg.depth_frames
    if frames is None:
        return passed(cid, name, observed="no depth stream declared",
                      source_file="depth_camera_intrinsics.json", spec_ref="§8 (derived)",
                      message="package declares no depth stream; geometry not required")
    if not frames:
        return check(cid, name, R.ERROR, code=code,
                     source_file="depth_camera_intrinsics.json", spec_ref="§8 (derived)",
                     observed="0 records", expected="one record per depth frame",
                     message="depth index is present but empty")

    absent = set()
    for frame in frames:
        if not isinstance(frame, dict):
            absent.add("<record is not an object>")
            continue
        flat = N.flatten(frame)
        for key in _DEPTH_GEOMETRY_KEYS:
            if N.is_empty(N.value(flat, canon_set([key]))):
                absent.add(key)
    if absent:
        return check(cid, name, R.ERROR, code=code,
                     source_file="depth_camera_intrinsics.json", spec_ref="§8 (derived)",
                     observed=f"missing: {', '.join(sorted(absent))}",
                     expected=", ".join(_DEPTH_GEOMETRY_KEYS),
                     message=f"depth records do not declare: {', '.join(sorted(absent))}")
    return passed(cid, name,
                  observed=f"{len(frames)} record(s), all declaring "
                           f"{', '.join(_DEPTH_GEOMETRY_KEYS)}",
                  source_file="depth_camera_intrinsics.json", spec_ref="§8 (derived)")


# Per-chunk: each chunk has its own metadata, timestamps, intrinsics and hands.
CHUNK_CHECKS = [
    c1_frame_count_concordance, c2_duration_concordance, c3_imu_covers_video_span,
    c6_sensor_relation_declared, c7_non_empty_payloads,
    c8_camera_intrinsics_declared, c9_depth_geometry_declared,
]

# Session-level: these read files that belong to the whole session (one video,
# one set of depth payloads), so running them per chunk would just repeat the
# same finding N times.
SESSION_FILE_CHECKS = [c4_referenced_files_exist, c5_single_authoritative_video]

CHECKS = CHUNK_CHECKS + SESSION_FILE_CHECKS


def run_chunk(pkg: Package) -> List[dict]:
    return [fn(pkg) for fn in CHUNK_CHECKS]


def run(pkg: Package) -> List[dict]:
    """Every Layer C check against a single package (back-compat)."""
    return [fn(pkg) for fn in CHECKS]
