"""Package-format ingestion (PRD §3). The only module here that touches disk.

Responsibilities:
  * Discover capture packages under a root -- the root *is* a package (it has
    metadata.json / manifest.json, or it is a chunk ``.zip``), or each immediate
    entry is one.
  * Read a package from either a **directory** or a **zip archive**, so a
    delivery can ship ``chunk_0000_raw.zip`` per episode without being unpacked
    first (PRD §3, capture-app format).
  * Load every known side-car JSON *fail-open*: a malformed file yields a
    parse-error record and the other files still load (PRD §11.1).
  * Unwrap the chunk-envelope convention (PRD §3.1): a file shaped
    ``[{chunk, source, data: {...}}]`` has payload ``[0].data`` (and metadata's
    payload is further ``.metadata``). A bare object is accepted unchanged.
  * Enumerate depth ``.bin`` payloads whether they are loose in the package or
    bundled in ``depth_maps.zip`` (for C4).
  * Never fully materialize ar_session.json -- keep only ``len(frames)``
    (PRD §11.2).
"""

from __future__ import annotations

import json
import os
import re
import uuid
import zipfile
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import normalize as N
from .models import CaptureSession, Package

VIDEO_BASENAMES = ("video.mp4", "video.mov")
_PACKAGE_MARKERS = ("metadata.json", "manifest.json")

#: Accepted spellings per side-car. Capture apps disagree on singular vs plural,
#: so the reader tries each in order and uses the first present. Adding a
#: spelling is a one-line change here.
SIDECAR_NAMES: Dict[str, Tuple[str, ...]] = {
    "manifest.json": ("manifest.json",),
    "metadata.json": ("metadata.json", "episode_metadata.json"),
    "camera_intrinsic.json": ("camera_intrinsic.json", "camera_intrinsics.json"),
    "video_timestamps.json": ("video_timestamps.json",),
    "motion_data.json": ("motion_data.json",),
    "depth_camera_intrinsics.json": ("depth_camera_intrinsics.json",
                                     "depth_camera_intrinsic.json"),
    "hands.json": ("hands.json",),
    "hands_metadata.json": ("hands_metadata.json",),
    "ar_session.json": ("ar_session.json",),
}

#: Archives that bundle the depth payloads instead of shipping them loose.
DEPTH_ARCHIVES = ("depth_maps.zip", "depth.zip")

_DEPTH_BIN = re.compile(r"^depth_\d+\.bin$", re.IGNORECASE)
_CHUNK_ZIP = re.compile(r"^chunk[-_].*\.zip$", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Reading a package from a directory or a zip, behind one interface
# ---------------------------------------------------------------------------
class _Entries:
    """Flat map of ``lowercased basename -> read()`` for one package."""

    def __init__(self, readers: Dict[str, Callable[[], bytes]], label: str):
        self._readers = readers
        self.label = label

    def __contains__(self, name: str) -> bool:
        return name.lower() in self._readers

    def names(self) -> List[str]:
        return list(self._readers)

    def read(self, name: str) -> bytes:
        return self._readers[name.lower()]()

    def first_present(self, candidates: Tuple[str, ...]) -> Optional[str]:
        for cand in candidates:
            if cand.lower() in self._readers:
                return cand.lower()
        return None


def _dir_entries(directory: str) -> _Entries:
    readers: Dict[str, Callable[[], bytes]] = {}
    try:
        for name in os.listdir(directory):
            full = os.path.join(directory, name)
            if os.path.isfile(full):
                readers[name.lower()] = (lambda p=full: open(p, "rb").read())
    except OSError:
        pass
    return _Entries(readers, directory)


def _zip_entries(zip_path: str) -> _Entries:
    """Members directly inside the archive. Nesting is ignored, matching the
    flat-package rule used for directories."""
    readers: Dict[str, Callable[[], bytes]] = {}
    try:
        zf = zipfile.ZipFile(zip_path)
    except (OSError, zipfile.BadZipFile):
        return _Entries(readers, zip_path)
    for info in zf.infolist():
        if info.is_dir() or "/" in info.filename.strip("/"):
            continue
        readers[info.filename.lower()] = (
            lambda z=zf, n=info.filename: z.read(n))
    return _Entries(readers, zip_path)


def _entries_for(path: str) -> _Entries:
    return _zip_entries(path) if _is_zip(path) else _dir_entries(path)


def _is_zip(path: str) -> bool:
    return os.path.isfile(path) and path.lower().endswith(".zip")


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------
def _is_package_dir(directory: str) -> bool:
    entries = _dir_entries(directory)
    if any(m in entries for m in _PACKAGE_MARKERS):
        return True
    # A directory holding exactly the episode's chunk archive is a package too.
    return any(_CHUNK_ZIP.match(n) for n in entries.names())


def _package_path(directory: str) -> str:
    """The thing to read for this package: the directory, or the chunk zip in it."""
    entries = _dir_entries(directory)
    if any(m in entries for m in _PACKAGE_MARKERS):
        return directory
    for name in sorted(entries.names()):
        if _CHUNK_ZIP.match(name):
            return os.path.join(directory, name)
    return directory


def discover_sessions(root: str, limit: Optional[int] = None) -> List[CaptureSession]:
    """Load capture sessions under ``root`` in deterministic (sorted) order.

    ``root`` may be a package directory, a chunk ``.zip``, or a delivery whose
    immediate entries are either of those.

    Raises FileNotFoundError / NotADirectoryError for a bad path (the caller
    maps those to exit code 2).
    """
    if not os.path.exists(root):
        raise FileNotFoundError(root)

    if _is_zip(root):
        return [load_session(root)]
    if not os.path.isdir(root):
        raise NotADirectoryError(root)

    # (path_to_read, package_id). An episode shipped as <episode>/chunk_*.zip is
    # named after the episode folder, not the chunk -- otherwise every episode in
    # a delivery would be called "chunk_0000_raw".
    targets: List[Tuple[str, str]] = []
    if _is_package_dir(root):
        targets = [(_package_path(root), package_id_for(root))]
    else:
        for name in sorted(os.listdir(root)):
            full = os.path.join(root, name)
            if _is_zip(full):
                targets.append((full, package_id_for(full)))
            elif os.path.isdir(full) and _is_package_dir(full):
                targets.append((_package_path(full), name))

    if limit is not None:
        targets = targets[:limit]
    return [load_session(path, session_id=pid) for path, pid in targets]


def package_id_for(path: str) -> str:
    """Default package id: the folder name, or the archive's stem."""
    base = os.path.basename(os.path.normpath(path))
    return base[:-4] if _is_zip(path) else base


# ---------------------------------------------------------------------------
# Fail-open JSON loading + envelope unwrap
# ---------------------------------------------------------------------------
def _unwrap_records(raw: Any) -> Tuple[List[Any], List[Optional[str]]]:
    """Return ``(payloads, chunk_names)`` for a chunk-envelope file (PRD §3.1).

    A capture session is cut into ~60-second chunks and every side-car is an
    array with one record per chunk: ``[{chunk, source, data: {...}}, ...]``.
    Each record's ``data`` is one chunk's payload.

    This deliberately returns **every** record. An earlier version took only
    ``[0].data``, which silently discarded every chunk after the first -- around
    two thirds of a typical delivery.

    A bare object (no envelope) is a single unnamed chunk.
    """
    if isinstance(raw, list):
        if raw and all(isinstance(r, dict) and "data" in r for r in raw):
            return [r["data"] for r in raw], [r.get("chunk") for r in raw]
        return [raw], [None]          # a plain array payload is itself the data
    return [raw], [None]


def _as_frames(payload: Any) -> Optional[List[Any]]:
    """Best-effort extraction of a per-frame list from a payload."""
    if payload is None:
        return None
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        frames = payload.get("frames")
        if isinstance(frames, list):
            return frames
    return None


def _as_timestamps(payload: Any) -> Optional[List[float]]:
    """Extract per-frame relative-second timestamps from video_timestamps.json."""
    seq: Optional[List[Any]] = None
    if isinstance(payload, list):
        seq = payload
    elif isinstance(payload, dict):
        for key in ("timestamps", "relative_seconds", "frames"):
            v = payload.get(key)
            if isinstance(v, list):
                seq = v
                break
    if seq is None:
        return None
    out: List[float] = []
    for item in seq:
        if isinstance(item, dict):
            item = item.get("timestamp", item.get("t"))
        try:
            out.append(float(item))
        except (TypeError, ValueError):
            continue
    return out or None


def _metadata_object(payload: Any) -> Dict[str, Any]:
    """Resolve the episode metadata object from metadata.json's payload.

    The payload is ``[0].data``; the object is usually ``.metadata`` under it,
    but a payload that is itself the object is accepted too.
    """
    if isinstance(payload, dict):
        inner = payload.get("metadata")
        if isinstance(inner, dict):
            return inner
        return payload
    return {}


# ---------------------------------------------------------------------------
# Depth payload enumeration (loose, or bundled in an archive)
# ---------------------------------------------------------------------------
def _depth_bins(entries: _Entries, pkg: Package) -> set:
    """Lowercased names of every depth payload the package actually carries."""
    present = {n for n in entries.names() if _DEPTH_BIN.match(n)}
    for archive in DEPTH_ARCHIVES:
        member = entries.first_present((archive,))
        if member is None:
            continue
        try:
            with zipfile.ZipFile(_io_bytes(entries.read(member))) as zf:
                inner = {os.path.basename(n).lower() for n in zf.namelist()
                         if _DEPTH_BIN.match(os.path.basename(n))}
        except (OSError, zipfile.BadZipFile, KeyError) as exc:
            pkg.parse_errors.append({"file": archive, "message": str(exc)})
            continue
        pkg.depth_archives.append(archive)
        present |= inner
    return present


def _io_bytes(data: bytes):
    import io
    return io.BytesIO(data)


# ---------------------------------------------------------------------------
# Per-session load
# ---------------------------------------------------------------------------
def _resolve_episode_uuid(session: CaptureSession) -> None:
    """Identity of the episode, which is the session -- not any single chunk.

    A chunk's own ``episode_uuid`` usually holds the chunk label
    ("chunk_0498_raw"), so candidates are gathered from several places and the
    first one that actually parses as a UUID wins. Only if none parse do we fall
    back to the first non-empty candidate, so a delivery that never writes a
    real UUID still gets a stable id and B1.1 reports the truth about it.
    """
    candidates: List[Tuple[str, str]] = []          # (value, source)

    if isinstance(session.manifest, dict):
        for key in ("doc_id", "session_uuid", "session_id", "episode_uuid"):
            val = session.manifest.get(key)
            if not N.is_empty(val):
                candidates.append((str(val), f"manifest.json:{key}"))
    for chunk in session.chunks:
        val = N.value(chunk.meta_flat, _SESSION_ID_CANONS)
        if not N.is_empty(val):
            candidates.append((str(val), "metadata.json:session_id"))
            break
    for chunk in session.chunks:
        val = N.value(chunk.meta_flat, _EPISODE_UUID_CANONS)
        if not N.is_empty(val):
            candidates.append((str(val), "metadata.json:episode_uuid"))
            break
    if session.session_id:
        candidates.append((session.session_id, "folder name"))

    for value, source in candidates:
        try:
            uuid.UUID(value)
        except (ValueError, AttributeError, TypeError):
            continue
        session.episode_uuid, session.episode_uuid_source = value, source
        return
    if candidates:
        session.episode_uuid, session.episode_uuid_source = candidates[0]


_SESSION_ID_CANONS = N.canon_set(["session_id", "session_uuid"])
_EPISODE_UUID_CANONS = N.canon_set(["episode_uuid", "uuid"])


def load_session(path: str, session_id: Optional[str] = None) -> CaptureSession:
    """Read one capture session (= one episode) from a directory or chunk zip."""
    entries = _entries_for(path)
    session = CaptureSession(session_id=session_id or package_id_for(path), root=path)

    def read_records(logical: str):
        """All chunk payloads for a side-car, fail-open."""
        member = entries.first_present(SIDECAR_NAMES[logical])
        if member is None:
            return [], []
        try:
            raw = json.loads(entries.read(member).decode("utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError,
                zipfile.BadZipFile) as exc:
            session.parse_errors.append({"file": logical, "message": str(exc)})
            return [], []
        payloads, names = _unwrap_records(raw)
        session.record_counts[logical] = len(payloads)
        session.chunk_names[logical] = [n for n in names if n]
        return payloads, names

    # manifest.json is a plain object shared by the whole session.
    member = entries.first_present(SIDECAR_NAMES["manifest.json"])
    if member is not None:
        try:
            manifest = json.loads(entries.read(member).decode("utf-8"))
            session.manifest = manifest if isinstance(manifest, dict) else None
        except (OSError, UnicodeDecodeError, json.JSONDecodeError,
                zipfile.BadZipFile) as exc:
            session.parse_errors.append({"file": "manifest.json", "message": str(exc)})

    meta_p, meta_names = read_records("metadata.json")
    cam_p, _ = read_records("camera_intrinsic.json")
    vts_p, _ = read_records("video_timestamps.json")
    motion_p, _ = read_records("motion_data.json")
    depth_p, _ = read_records("depth_camera_intrinsics.json")
    hands_p, _ = read_records("hands.json")
    hmeta_p, _ = read_records("hands_metadata.json")
    ar_p, _ = read_records("ar_session.json")

    # Session-level file facts: one video covers the whole recording.
    session.video_files = [b for b in VIDEO_BASENAMES if b in entries]
    session.depth_bins_present = _depth_bins(entries, session)

    def at(seq, i):
        return seq[i] if i < len(seq) else None

    n = max([len(meta_p), len(vts_p), len(cam_p), len(motion_p), len(depth_p),
             len(hands_p), len(hmeta_p), len(ar_p)] or [0])
    for i in range(n):
        name = at(meta_names, i)
        chunk = Package(
            package_id=f"{session.session_id}#{name or i}",
            root=path, chunk_name=name, chunk_index=i,
            record_count=len(meta_p),
            manifest=session.manifest,
            video_files=session.video_files,
            depth_bins_present=session.depth_bins_present,
            depth_archives=session.depth_archives,
        )
        meta_payload = at(meta_p, i)
        chunk.meta = _metadata_object(meta_payload)
        chunk.meta_flat = N.flatten(chunk.meta) if chunk.meta else {}

        cam = at(cam_p, i)
        chunk.camera_intrinsic = cam if isinstance(cam, dict) else None
        chunk.video_timestamps = _as_timestamps(at(vts_p, i))
        motion = at(motion_p, i)
        chunk.motion_data = motion if isinstance(motion, dict) else None
        chunk.depth_frames = _as_frames(at(depth_p, i))
        chunk.hands_frames = _as_frames(at(hands_p, i))
        hmeta = at(hmeta_p, i)
        chunk.hands_metadata = hmeta if isinstance(hmeta, dict) else None
        ar_frames = _as_frames(at(ar_p, i))     # counted, then dropped (PRD §11.2)
        chunk.ar_session_frame_count = len(ar_frames) if ar_frames is not None else None
        session.chunks.append(chunk)

    _resolve_episode_uuid(session)
    return session


def load_package(path: str, package_id: Optional[str] = None) -> Package:
    """Back-compat shim: the first chunk of a session as a standalone Package.

    Kept so single-chunk callers and older tests keep working. New code should
    use :func:`load_session`, which sees every chunk.
    """
    session = load_session(path, session_id=package_id)
    if session.chunks:
        chunk = session.chunks[0]
        chunk.package_id = session.session_id
        chunk.parse_errors = session.parse_errors
        return chunk
    empty = Package(package_id=session.session_id, root=path,
                    record_count=0, manifest=session.manifest,
                    video_files=session.video_files,
                    depth_bins_present=session.depth_bins_present)
    empty.parse_errors = session.parse_errors
    return empty
