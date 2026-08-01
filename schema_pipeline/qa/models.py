"""The Episode dataclass -- the unit both modes operate on.

One video == one episode. An episode may be missing its video (orphan metadata)
or its metadata (orphan video); either way it still exists and carries warnings.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set


@dataclass
class Episode:
    stem: str                       # filename stem shared by video + sidecar
    directory: str                  # containing directory (pairing key with stem)
    source: str                     # human-readable source label (meta path / video path)

    video_path: Optional[str] = None
    meta_path: Optional[str] = None
    # Set for cloud-direct (S3) episodes: a presigned URL ffprobe can read via
    # range requests, so durations are measured without downloading the video.
    video_remote: Optional[str] = None

    raw_meta: Optional[Dict[str, Any]] = None    # parsed JSON (None if absent/invalid)
    flat_meta: Dict[str, Any] = field(default_factory=dict)  # flattened canonical view

    warnings: List[str] = field(default_factory=list)
    error: Optional[str] = None     # parse / orphan-metadata fatal note (still non-conformant)

    # Populated only in diversity mode by ingest.probe_durations().
    video_seconds: Optional[float] = None

    @property
    def has_meta(self) -> bool:
        return self.raw_meta is not None

    @property
    def has_video(self) -> bool:
        return self.video_path is not None or self.video_remote is not None

    def episode_id(self) -> str:
        """Stable id: episode_uuid if resolvable, else stem, else path (§8)."""
        from . import normalize as N
        from . import schema as S

        if self.flat_meta:
            val = N.value(self.flat_meta, S.CANONS_OF["episode_uuid"])
            if val is not None and not N.is_empty(val):
                return str(val)
        if self.stem:
            return self.stem
        return self.source


@dataclass
class Package:
    """One **chunk** of a capture session (PRD §3).

    The capture app cuts a continuous recording into ~60-second chunks. Each
    side-car file is an array with one record per chunk, so a session's
    ``metadata.json`` carries N metadata objects, its ``video_timestamps.json``
    carries N timestamp arrays, and so on. This dataclass is one chunk's slice
    across all of those files -- the unit every Layer B and Layer C check
    operates on.

    ``pkg_ingest`` loads every file fail-open: a malformed file becomes a
    ``parse_errors`` entry rather than aborting, so the remaining checks still
    run (PRD §11.1).
    """
    package_id: str                 # "<session-id>#<chunk-name>"
    root: str                       # session directory or archive path

    chunk_name: Optional[str] = None   # e.g. "chunk_0498_raw"
    chunk_index: int = 0               # position within the session

    # Fail-open parse outcomes: [{"file": name, "message": why}, ...]
    parse_errors: List[Dict[str, str]] = field(default_factory=list)

    # metadata.json is an array of chunk-envelope records (PRD §3.1).
    record_count: int = 0                        # len() of that array (D-2)
    manifest: Optional[Dict[str, Any]] = None    # manifest.json (plain object)
    meta: Dict[str, Any] = field(default_factory=dict)      # unwrapped metadata object
    meta_flat: Dict[str, Any] = field(default_factory=dict)  # flattened canonical view

    camera_intrinsic: Optional[Dict[str, Any]] = None   # camera_intrinsic.json payload
    video_timestamps: Optional[List[float]] = None      # per-frame relative seconds
    motion_data: Optional[Dict[str, Any]] = None        # motion_data.json payload
    depth_frames: Optional[List[Dict[str, Any]]] = None  # depth_camera_intrinsics frames[]
    hands_frames: Optional[List[Any]] = None            # hands.json frames[]
    hands_metadata: Optional[Dict[str, Any]] = None     # hands_metadata.json payload
    ar_session_frame_count: Optional[int] = None        # ar_session.json len(frames) only

    video_files: List[str] = field(default_factory=list)   # present video basenames
    depth_bins_present: Set[str] = field(default_factory=set)  # depth_*.bin, loose or archived
    depth_archives: List[str] = field(default_factory=list)    # archives the bins came from

    warnings: List[str] = field(default_factory=list)

    @property
    def has_meta(self) -> bool:
        return bool(self.meta)


@dataclass
class CaptureSession:
    """One capture session -- which is one **episode** (PRD §3).

    A session directory (or chunk archive) holds one file per stream, each an
    array of per-chunk records. The session is the deliverable unit: it carries
    a single video covering the whole recording, one manifest, and N chunks that
    tile it in time.

    Validation runs per chunk (Layer B and Layer C both operate on a chunk's own
    metadata, timestamps and intrinsics) and then at session level for the things
    only visible across chunks -- identity, chunk coherence and continuity.
    """
    session_id: str                              # folder name / archive stem
    root: str                                    # directory or archive path

    chunks: List[Package] = field(default_factory=list)
    manifest: Optional[Dict[str, Any]] = None    # manifest.json (plain object)

    # Resolved episode identity, in precedence order: manifest.doc_id, then a
    # session_id declared in chunk metadata, then the folder name. A chunk's own
    # ``episode_uuid`` is a CHUNK label ("chunk_0498_raw") and is not used here.
    episode_uuid: Optional[str] = None
    episode_uuid_source: Optional[str] = None

    # Session-level file facts (one video covers the whole session).
    video_files: List[str] = field(default_factory=list)
    depth_bins_present: Set[str] = field(default_factory=set)
    depth_archives: List[str] = field(default_factory=list)

    # Per-file record counts, so a file disagreeing on how many chunks exist is
    # detectable: {"metadata.json": 6, "video_timestamps.json": 6, ...}
    record_counts: Dict[str, int] = field(default_factory=dict)
    chunk_names: Dict[str, List[str]] = field(default_factory=dict)

    parse_errors: List[Dict[str, str]] = field(default_factory=list)

    @property
    def chunk_count(self) -> int:
        return len(self.chunks)

    @property
    def has_meta(self) -> bool:
        return any(c.has_meta for c in self.chunks)
