"""Synthetic reference package for the Layer B + C validator.

No real capture package ships with the repo, so this builds one on disk whose
metadata reproduces the documented reference-fixture outcomes (PRD §10) for the
checks in scope (Layer B + Layer C). It is deliberately close to the real
observed format: every side-car is a chunk-envelope array, metadata lives at
``[0].data.metadata``, and the depth ``.bin`` payloads are intentionally absent.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, List

FRAME_COUNT = 245
VIDEO_SPAN_S = 8.1343          # authoritative timestamp span (PRD §10)
IMU_SPAN_S = 8.30             # IMU covers the video span
START_UNIX = 1784876817.882431  # -> 2026-07-24T07:06:57Z (PRD §10)


def _env(data: Any) -> List[Dict[str, Any]]:
    """Wrap a payload in the single-chunk envelope convention (PRD §3.1)."""
    return [{"chunk": "chunk_0000_raw", "source": ".../chunk_0000_raw.zip", "data": data}]


def _linspace(n: int, total: float) -> List[float]:
    return [round(total * i / (n - 1), 6) for i in range(n)]


def _iso(unix: float) -> str:
    return datetime.fromtimestamp(unix, timezone.utc).isoformat().replace("+00:00", "Z")


def _write(path: str, obj: Any) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh)


# The episode metadata object. Values chosen to exercise every Layer B outcome.
METADATA = {
    "episode_uuid": "chunk_0000_raw",          # B1.1 ERROR: not a UUID
    "operator_id": "OP-1837",                  # B1.2 PASS
    "episode_start_time": START_UNIX,          # B1.3 PASS
    "device_type": "Apple iPhone 15 Pro",      # B1.4 PASS (identifies generation)
    "device_model": "iPhone15,2",
    "software_version": "1.4.2",               # B1.5 PASS
    "camera_configuration": {"streams": ["depth"]},  # B1.6 ERROR: rgb absent from config
    "task_category": "Unknown_Catagory",       # B1.8 ERROR: sentinel + misspelling
    "task_id": "TASK-552",                     # B2.1 PASS
    "environment_id": "ENV-334",               # B2.2 PASS / B1.9 PASS
    "environment_label": "warehouse",
    "duration": 60.0,                          # C2 ERROR: conflicts with the span
    # Absent by construction -> FORMAT_GAP: task_description, indoor_outdoor,
    # episode_end_time, geo lat/lon, subtask_annotations, instruction.
}


def build_reference_package(dest_root: str,
                            package_id: str = "23DE884C-74DA-4BB1-A2E9-7E5B434CE7A3") -> str:
    """Materialize the reference package under ``dest_root`` and return its path."""
    pkg = os.path.join(dest_root, package_id)
    os.makedirs(pkg, exist_ok=True)

    vts = _linspace(FRAME_COUNT, VIDEO_SPAN_S)

    _write(os.path.join(pkg, "manifest.json"), {
        "doc_id": package_id, "bucket_id": "bucket-x",
        "source_prefix": "deliveries/xphi", "generated_at": _iso(START_UNIX + 1),
    })
    _write(os.path.join(pkg, "metadata.json"), _env({"metadata": METADATA}))
    _write(os.path.join(pkg, "camera_intrinsic.json"), _env({
        "focal_length": [1456.254, 1456.254],
        "principal_point": [957.116, 710.713],
        "intrinsic_matrix": [1456.254, 0, 957.116, 0, 1456.254, 710.713, 0, 0, 1],
        "image_width": 1920, "image_height": 1440, "stream": "ego_camera",
    }))
    _write(os.path.join(pkg, "video_timestamps.json"), _env({"timestamps": vts}))
    _write(os.path.join(pkg, "motion_data.json"), _env({
        "metadata": {
            "recording_date": _iso(START_UNIX + 9),   # B2.4 ERROR: ~9s off start
            "coordinate_system": "gravity-aligned, Y up, X right",
            "duration": 9.11,                          # C2 ERROR: conflicts with span
        },
        "timestamps": _linspace(825, IMU_SPAN_S),      # C3 PASS: covers video span
    }))
    _write(os.path.join(pkg, "depth_camera_intrinsics.json"), _env({
        "frames": [
            {"file": f"depth_{i:05d}.bin", "bytes_per_row": 1024,
             "height": 192, "width": 256, "frame": i, "timestamp": vts[i]}
            for i in range(FRAME_COUNT)
        ]
    }))
    _write(os.path.join(pkg, "ar_session.json"),
           _env({"frames": [{"points": []} for _ in range(FRAME_COUNT)]}))
    _write(os.path.join(pkg, "hands.json"),
           _env({"frames": [{"interactions": [], "objects": []}
                            for _ in range(FRAME_COUNT)]}))     # C7 WARN: all empty
    _write(os.path.join(pkg, "hands_metadata.json"),
           _env({"graspClassificationEnabled": True, "objectDetectionEnabled": True}))

    # Both videos present, none authoritative -> C5 WARN. Content is irrelevant
    # to Layer B/C, so empty placeholder files suffice for presence checks.
    for name in ("video.mp4", "video.mov"):
        open(os.path.join(pkg, name), "w").close()

    # depth_*.bin deliberately NOT written -> C4 ERROR (referenced, absent).
    return pkg
