"""Ingestion: the only module that touches the filesystem or subprocesses.

Responsibilities:
  * Discover videos (.mp4/.mov, case-insensitive) and sidecars (*.meta.json,
    case-insensitive) under a root directory (recursion allowed, flat is normal).
  * Pair them within a directory keyed by (dir, stem).
  * Surface orphans (video without sidecar, sidecar without video) as Episodes
    carrying a warning -- never dropped.
  * Order episodes by the numeric part of the stem (natural order) so
    ``video10`` sorts after ``video2``.
  * Measure video durations via ffprobe (diversity mode only), degrading
    gracefully when ffprobe is absent or a file is unreadable.
"""

from __future__ import annotations

import concurrent.futures as cf
import json
import os
import re
import shutil
import subprocess
from typing import Any, Dict, List, Optional, Tuple

from . import normalize as N
from .models import Episode
from .profiles import GENERIC, SourceProfile

VIDEO_EXTS = (".mp4", ".mov")
META_SUFFIX = ".meta.json"


# ---------------------------------------------------------------------------
# Natural ordering
# ---------------------------------------------------------------------------
_NUM_CHUNK = re.compile(r"\d+|\D+")


def natural_key(stem: str):
    """Split a stem into digit / non-digit chunks for natural ordering.

    Each chunk is wrapped in a type-tagged tuple so int and str never compare
    against each other (which raises on Python 3).
    """
    parts = []
    for chunk in _NUM_CHUNK.findall(stem):
        if chunk.isdigit():
            parts.append((0, int(chunk), ""))
        else:
            parts.append((1, 0, chunk.lower()))
    return parts


def _classify(name: str) -> Optional[Tuple[str, str]]:
    """Return (kind, stem) for a filename, or None if it is neither kind.

    kind is 'meta' or 'video'. Matching is case-insensitive.
    """
    lower = name.lower()
    if lower.endswith(META_SUFFIX):
        return "meta", name[: -len(META_SUFFIX)]
    for ext in VIDEO_EXTS:
        if lower.endswith(ext):
            return "video", name[: -len(ext)]
    return None


# ---------------------------------------------------------------------------
# Discovery + pairing
# ---------------------------------------------------------------------------
def build_episodes(root: str, profile: SourceProfile = GENERIC) -> List[Episode]:
    """Walk ``root`` and return paired Episodes in natural order.

    ``profile`` reshapes each metadata file before validation (default: none).
    Raises FileNotFoundError / NotADirectoryError for a bad path (the caller maps
    those to exit code 2).
    """
    if not os.path.exists(root):
        raise FileNotFoundError(root)
    if not os.path.isdir(root):
        raise NotADirectoryError(root)

    # (dir, stem) -> {"video": path, "meta": path}
    pairs: Dict[Tuple[str, str], Dict[str, str]] = {}

    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            classified = _classify(name)
            if classified is None:
                continue
            kind, stem = classified
            full = os.path.join(dirpath, name)
            slot = pairs.setdefault((dirpath, stem), {})
            # If two videos share a stem (e.g. .mp4 + .mov), keep the first seen
            # deterministically; note the collision as a warning later.
            if kind in slot:
                slot.setdefault("_collisions", []).append(full)  # type: ignore[arg-type]
            else:
                slot[kind] = full

    episodes: List[Episode] = []
    for (dirpath, stem), slot in pairs.items():
        episodes.append(_make_episode(dirpath, stem, slot, profile))

    episodes.sort(key=lambda e: (natural_key(e.stem), e.directory.lower()))
    return episodes


def _make_episode(dirpath: str, stem: str, slot: Dict[str, str],
                  profile: SourceProfile = GENERIC) -> Episode:
    video = slot.get("video")
    meta = slot.get("meta")
    source = meta or video or os.path.join(dirpath, stem)

    ep = Episode(stem=stem, directory=dirpath, source=source,
                 video_path=video, meta_path=meta)

    collisions = slot.get("_collisions")
    if collisions:
        ep.warnings.append(
            f"multiple files share stem '{stem}'; extra file(s) ignored: "
            + ", ".join(os.path.basename(c) for c in collisions)
        )

    if meta is None:
        ep.warnings.append("orphan video: no .meta.json sidecar found")
    if video is None:
        ep.warnings.append("orphan metadata: no video file found")

    if meta is not None:
        _load_meta(ep, meta, profile)

    return ep


def _load_meta(ep: Episode, meta_path: str, profile: SourceProfile = GENERIC) -> None:
    try:
        with open(meta_path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        ep.error = f"invalid metadata JSON: {exc}"
        ep.warnings.append(ep.error)
        return
    ingest_meta_obj(ep, raw, profile)


def ingest_meta_obj(ep: Episode, raw: Any, profile: SourceProfile = GENERIC) -> None:
    """Apply the profile's container transform, then flatten into the episode.

    Shared by local file ingestion and cloud-direct (S3) ingestion. A non-object
    top level after unwrap is a captured per-episode error (§8), not a crash.
    """
    obj = profile.unwrap_meta(raw)
    if not isinstance(obj, dict):
        ep.error = f"metadata top level is {type(obj).__name__}, expected object"
        ep.warnings.append(ep.error)
        return
    ep.raw_meta = obj
    ep.flat_meta = N.flatten(obj)


# ---------------------------------------------------------------------------
# Durations (diversity mode only)
# ---------------------------------------------------------------------------
def ffprobe_available() -> bool:
    return shutil.which("ffprobe") is not None


def probe_duration_seconds(video_ref: str, timeout: int = 60) -> Optional[float]:
    """Measured video duration in seconds via ffprobe, or None on any failure.

    ``video_ref`` may be a local path or an http(s) URL -- ffprobe reads a remote
    URL with range requests, so cloud-direct episodes are probed without a
    download.
    """
    try:
        proc = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", video_ref],
            capture_output=True, text=True, timeout=timeout,
            # On Windows, suppress the console window each ffprobe would flash;
            # essential when probing thousands of clips unattended (scheduled task).
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    text = proc.stdout.strip()
    try:
        seconds = float(text)
    except ValueError:
        return None
    if seconds <= 0:
        return None
    return seconds


def _probe_one(ep: Episode) -> Optional[float]:
    if ep.video_path is not None:
        return probe_duration_seconds(ep.video_path)
    if ep.video_remote is not None:
        # Remote probe over a presigned URL (range requests, no download).
        return probe_duration_seconds(ep.video_remote, timeout=120)
    return None


def probe_durations(episodes: List[Episode], max_workers: int = 1) -> bool:
    """Attach measured video durations to episodes in place.

    Returns True if ffprobe was available. When absent, warns once (on the first
    episode) and leaves every ``video_seconds`` as None so the pure duration
    reconciliation falls back to metadata timestamps. ``max_workers`` > 1 probes
    concurrently -- essential for cloud-direct runs over many remote videos.
    """
    if not ffprobe_available():
        if episodes:
            episodes[0].warnings.append(
                "ffprobe not found on PATH; falling back to metadata timestamps "
                "for all durations"
            )
        return False

    targets = [ep for ep in episodes if ep.video_path or ep.video_remote]

    def assign(ep: Episode, secs: Optional[float]) -> None:
        if secs is None:
            ep.warnings.append("ffprobe could not read video duration")
        ep.video_seconds = secs

    if max_workers > 1 and len(targets) > 1:
        with cf.ThreadPoolExecutor(max_workers=max_workers) as ex:
            for ep, secs in zip(targets, ex.map(_probe_one, targets)):
                assign(ep, secs)
    else:
        for ep in targets:
            assign(ep, _probe_one(ep))
    return True
