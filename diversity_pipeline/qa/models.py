"""The Episode dataclass -- the unit both modes operate on.

One video == one episode. An episode may be missing its video (orphan metadata)
or its metadata (orphan video); either way it still exists and carries warnings.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


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
