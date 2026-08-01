"""Session-level checks -- the things only visible across chunks (PRD §8).

A capture session is one episode, cut into ~60-second chunks. Layer B and Layer
C both operate on a single chunk; these run once per session:

  S1  metadata present at all
  S2  chunk coherence  -- every side-car agrees on how many chunks there are,
                          and on their names (replaces the old D-2 rule, which
                          assumed one episode was a single chunk)
  S3  chunk continuity -- chunk k+1 starts where chunk k ended, so the session
                          tiles a continuous recording with no hole or overlap
  B1.1 episode identity -- validated against the SESSION uuid, since a chunk's
                          own ``episode_uuid`` is a chunk label

Pure and I/O-free.
"""

from __future__ import annotations

import uuid as _uuid
from typing import Any, Dict, List, Optional

from . import normalize as N
from . import ruleset as R
from . import sentinel
from .checks import check, passed
from .models import CaptureSession

#: A chunk boundary may drift by this much before it counts as a hole/overlap.
#: One frame at 30 fps is 0.033 s; the observed capture app lands near 0.13 s.
CHUNK_CONTINUITY_TOL_S = 0.5


def s1_metadata_present(session: CaptureSession) -> dict:
    name, cid, code = "session_metadata_present", "S1", "METADATA_MISSING"
    if session.has_meta:
        return passed(cid, name, observed=f"{session.chunk_count} chunk(s)",
                      source_file="metadata.json", spec_ref="§3")
    return check(cid, name, R.ERROR, code=code, source_file="metadata.json",
                 spec_ref="§3", expected="metadata.json with at least one chunk record",
                 message="no episode metadata found in the session")


def s2_chunk_coherence(session: CaptureSession) -> dict:
    """Every side-car must describe the same set of chunks.

    This replaces D-2 ("one episode == one chunk"), which was written before it
    was established that an episode is a session of N chunks. N chunks is
    normal; side-cars *disagreeing* on N is the real defect, because it means
    some stream is missing part of the recording.
    """
    name, cid, code = "chunk_coherence", "S2", "CHUNK_COUNT_MISMATCH"
    counts = {f: n for f, n in session.record_counts.items() if n}
    if not counts:
        return check(cid, name, R.ERROR, code=code, source_file="metadata.json",
                     spec_ref="§3.1", observed=counts,
                     expected="at least one chunk record",
                     message="no chunk records found in any side-car")
    distinct = set(counts.values())
    if len(distinct) > 1:
        return check(cid, name, R.ERROR, code=code, spec_ref="§3.1",
                     observed=counts, expected="every side-car covers the same chunks",
                     message=f"side-cars disagree on chunk count: {counts}")

    # Names, where declared, must match too -- same count but different chunks
    # would mean streams describing different parts of the recording.
    named = {f: names for f, names in session.chunk_names.items() if names}
    if len(named) > 1:
        reference = None
        for f, names in named.items():
            if reference is None:
                reference, ref_file = names, f
            elif names != reference:
                return check(cid, name, R.ERROR, code=code, spec_ref="§3.1",
                             observed=f"{ref_file}={reference}, {f}={names}",
                             expected="identical chunk names across side-cars",
                             message=f"chunk names differ between {ref_file} and {f}")
    n = distinct.pop()
    return passed(cid, name, observed=f"{n} chunk(s) across {len(counts)} side-car(s)",
                  spec_ref="§3.1")


def _chunk_start(chunk) -> Optional[float]:
    val = N.value_in_order(chunk.meta_flat, R.FIELD_CANON_ORDER["episode_start_time"])
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def s3_chunk_continuity(session: CaptureSession) -> dict:
    """Chunks must tile the recording: chunk k+1 starts where chunk k ended."""
    name, cid, code = "chunk_continuity", "S3", "CHUNK_DISCONTINUITY"
    if session.chunk_count < 2:
        return passed(cid, name, observed=f"{session.chunk_count} chunk(s)",
                      spec_ref="§8 (derived)",
                      message="single-chunk session; continuity not applicable")

    rows = []
    for c in session.chunks:
        start = _chunk_start(c)
        ts = c.video_timestamps
        span = (ts[-1] - ts[0]) if ts and len(ts) >= 2 else None
        rows.append((c.chunk_name or f"#{c.chunk_index}", start, span))

    if any(start is None for _, start, _ in rows):
        missing = [n for n, s, _ in rows if s is None]
        return check(cid, name, R.BLOCKED, code=code, spec_ref="§8 (derived)",
                     observed=f"no start time for: {', '.join(missing[:5])}",
                     expected="every chunk declares a start time",
                     message="cannot order chunks without start times")

    ordered = sorted(rows, key=lambda r: r[1])
    breaks = []
    for (n0, s0, sp0), (n1, s1, _) in zip(ordered, ordered[1:]):
        if sp0 is None:
            continue
        drift = s1 - (s0 + sp0)
        if abs(drift) > CHUNK_CONTINUITY_TOL_S:
            breaks.append({"after": n0, "before": n1,
                           "gap_s" if drift > 0 else "overlap_s": round(abs(drift), 3)})
    total = None
    first_start = ordered[0][1]
    last_start, last_span = ordered[-1][1], ordered[-1][2]
    if last_span is not None:
        total = (last_start + last_span) - first_start

    if breaks:
        detail = "; ".join(f"{b['after']}->{b['before']} "
                           f"{'gap' if 'gap_s' in b else 'overlap'} "
                           f"{b.get('gap_s', b.get('overlap_s'))}s" for b in breaks[:4])
        return check(cid, name, R.ERROR, code=code, spec_ref="§8 (derived)",
                     observed=detail, expected=f"within {CHUNK_CONTINUITY_TOL_S:g}s",
                     message=f"{len(breaks)} discontinuity(ies) between chunks: {detail}")
    return passed(cid, name,
                  observed=f"{session.chunk_count} chunks tile "
                           f"{total:.1f}s" if total else f"{session.chunk_count} chunks",
                  spec_ref="§8 (derived)")


def b1_1_episode_uuid_valid(session: CaptureSession) -> dict:
    """Episode identity, checked against the SESSION uuid.

    A chunk's ``episode_uuid`` holds the chunk label, so validating it per chunk
    would reject every well-formed session. ``pkg_ingest`` resolves the session's
    identity from the manifest, a declared session id, or the folder.
    """
    name, cid, code = "episode_uuid_valid", "B1.1", "EPISODE_UUID_INVALID"
    val = session.episode_uuid
    src = session.episode_uuid_source or "unresolved"
    if N.is_empty(val):
        return check(cid, name, R.ERROR, code=code, source_file="metadata.json",
                     spec_ref="Doc1 §5.1", expected="a UUID identifying the session",
                     message="no episode/session uuid could be resolved")
    is_bad, reason = sentinel.classify(val)
    if is_bad:
        return check(cid, name, R.ERROR, code=code, source_file="metadata.json",
                     spec_ref="Doc1 §5.1", observed=f"{val} (from {src})",
                     expected="a real UUID", message=f"episode uuid invalid: {reason}")
    try:
        _uuid.UUID(str(val))
    except (ValueError, AttributeError, TypeError):
        return check(cid, name, R.ERROR, code=code, source_file="metadata.json",
                     spec_ref="Doc1 §5.1", observed=f"{val} (from {src})",
                     expected="a UUID",
                     message=f"episode uuid '{val}' (from {src}) does not parse as a UUID")
    return passed(cid, name, observed=f"{val} (from {src})",
                  source_file="metadata.json", spec_ref="Doc1 §5.1")


CHECKS = [s1_metadata_present, s2_chunk_coherence, s3_chunk_continuity,
          b1_1_episode_uuid_valid]


def run(session: CaptureSession) -> List[dict]:
    return [fn(session) for fn in CHECKS]
