"""Cloud-direct ingestion: build Episodes straight from an S3 prefix.

No local download. Each immediate sub-folder of the prefix is one episode
(the delivery layout is ``<prefix>/<uid>/{video.*, metadata.json, ...}``):

  * metadata is fetched into memory and reshaped by the source profile;
  * the video is referenced by a presigned URL so ingest.probe_durations can
    measure its length remotely via ffprobe range requests.

I/O module. Orphans (folder with a video but no metadata, or vice versa) are
surfaced as Episodes carrying a warning, never dropped (§8).
"""

from __future__ import annotations

import concurrent.futures as cf
from typing import Dict, List, Optional

from . import ingest as _ingest
from .models import Episode
from .profiles import GENERIC, SourceProfile
from .s3client import S3Client

VIDEO_EXTS = (".mov", ".mp4")


def _is_video(key: str) -> bool:
    return key.lower().endswith(VIDEO_EXTS)


def _is_meta(key: str) -> bool:
    """The episode sidecar only -- exactly ``metadata.json`` or ``<stem>.meta.json``.

    Deliberately excludes companions like ``hands_metadata.json`` that merely end
    in ``metadata.json``.
    """
    base = key.rsplit("/", 1)[-1].lower()
    return base == "metadata.json" or base.endswith(".meta.json")


def build_episodes(client: S3Client, bucket: str, prefix: str,
                   profile: SourceProfile = GENERIC, want_video: bool = False,
                   max_episodes: Optional[int] = None,
                   max_workers: int = 24) -> List[Episode]:
    """Return Episodes for every episode folder under ``prefix``, natural order.

    Scales to the whole dataset: a single paginated listing groups keys by
    immediate folder (instead of one request per folder), then metadata is
    fetched concurrently.

    A profile may pin an exact sidecar basename (``meta_basename``); otherwise
    the default ``metadata.json`` / ``*.meta.json`` detection is used.
    """
    if prefix and not prefix.endswith("/"):
        prefix += "/"

    if profile.meta_basename:
        want_base = profile.meta_basename.lower()
        is_meta = lambda k: k.rsplit("/", 1)[-1].lower() == want_base
    else:
        is_meta = _is_meta

    # 1. One bulk listing -> {stem: {"meta": key, "video": key}} by folder.
    slots: Dict[str, Dict[str, Optional[str]]] = {}
    for key, _size in client.list_objects(bucket, prefix):
        rest = key[len(prefix):]
        if "/" not in rest:
            continue  # a stray object directly under the prefix, not an episode
        stem = rest.split("/", 1)[0]
        slot = slots.setdefault(stem, {"meta": None, "video": None})
        if slot["meta"] is None and is_meta(key):
            slot["meta"] = key
        elif slot["video"] is None and _is_video(key):
            slot["video"] = key

    # 2. Natural order + optional cap.
    stems = sorted(slots, key=_ingest.natural_key)
    if max_episodes is not None:
        stems = stems[:max_episodes]

    # 3. Build each episode (metadata fetch is the network cost -> parallelize).
    def make(stem: str) -> Episode:
        slot = slots[stem]
        meta_key, video_key = slot["meta"], slot["video"]
        ep = Episode(stem=stem, directory=f"s3://{bucket}/{prefix}{stem}/",
                     source=f"s3://{bucket}/{meta_key or video_key or prefix + stem}")
        if meta_key is None:
            ep.warnings.append("orphan video: no metadata object in folder")
        if video_key is None:
            ep.warnings.append("orphan metadata: no video object in folder")
        if meta_key is not None:
            _load_remote_meta(client, bucket, meta_key, ep, profile)
        if want_video and video_key is not None:
            ep.video_remote = client.presign(bucket, video_key)
        return ep

    if max_workers > 1 and len(stems) > 1:
        with cf.ThreadPoolExecutor(max_workers=max_workers) as ex:
            episodes = list(ex.map(make, stems))
    else:
        episodes = [make(s) for s in stems]
    return episodes


def build_episodes_paired(client: S3Client, bucket: str, prefix: str,
                          profile: SourceProfile = GENERIC, want_video: bool = False,
                          meta_sub: str = "metadata", media_sub: str = "videos",
                          max_episodes: Optional[int] = None,
                          max_workers: int = 24) -> List[Episode]:
    """Split-layout delivery: ``<prefix>/videos/<stem>.mp4`` paired with
    ``<prefix>/metadata/<stem>.json`` by identical stem (not co-located).

    Orphans (metadata without video or vice versa) are surfaced, not dropped.
    """
    if prefix and not prefix.endswith("/"):
        prefix += "/"

    def index(sub: str, want_meta: bool):
        out: Dict[str, str] = {}
        for key, _size in client.list_objects(bucket, prefix + sub + "/"):
            base = key.rsplit("/", 1)[-1]
            if base.startswith("."):
                continue  # .keep and friends
            is_meta = base.lower().endswith(".json")
            if want_meta != is_meta:
                continue
            stem = base.rsplit(".", 1)[0]
            out.setdefault(stem, key)
        return out

    metas = index(meta_sub, want_meta=True)
    videos = index(media_sub, want_meta=False)

    stems = sorted(set(metas) | set(videos), key=_ingest.natural_key)
    if max_episodes is not None:
        stems = stems[:max_episodes]

    def make(stem: str) -> Episode:
        meta_key, video_key = metas.get(stem), videos.get(stem)
        ep = Episode(stem=stem, directory=f"s3://{bucket}/{prefix}",
                     source=f"s3://{bucket}/{meta_key or video_key}")
        if meta_key is None:
            ep.warnings.append("orphan video: no metadata sidecar")
        if video_key is None:
            ep.warnings.append("orphan metadata: no video file")
        if meta_key is not None:
            _load_remote_meta(client, bucket, meta_key, ep, profile)
        if want_video and video_key is not None:
            ep.video_remote = client.presign(bucket, video_key)
        return ep

    if max_workers > 1 and len(stems) > 1:
        with cf.ThreadPoolExecutor(max_workers=max_workers) as ex:
            return list(ex.map(make, stems))
    return [make(s) for s in stems]


def _load_remote_meta(client: S3Client, bucket: str, key: str, ep: Episode,
                      profile: SourceProfile) -> None:
    import json
    try:
        raw = json.loads(client.get_bytes(bucket, key).decode("utf-8"))
    except Exception as exc:  # noqa: BLE001 -- any fetch/parse failure is per-episode
        ep.error = f"invalid metadata JSON: {exc}"
        ep.warnings.append(ep.error)
        return
    _ingest.ingest_meta_obj(ep, raw, profile)
