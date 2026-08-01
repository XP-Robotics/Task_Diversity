"""Episode validator -- orchestration (PRD §5, §7, §8).

An episode is a capture **session**, cut by the app into ~60-second chunks. Each
side-car carries one record per chunk, so validation runs:

  * per chunk   -- Layer B (metadata validity) and Layer C (cross-file
                   consistency), since every chunk has its own metadata,
                   timestamps and intrinsics;
  * per session -- identity, chunk coherence, chunk continuity, and the file
                   checks that concern the session's single video and depth
                   payloads.

Per-chunk findings that are identical across chunks are collapsed into one
record naming the affected chunks: a six-chunk session missing ``device_type``
reports the defect once, not six times.

A delivery-level pass enforces episode uuid uniqueness across sessions. Pure
over the already-ingested sessions.
"""

from __future__ import annotations

from collections import Counter, OrderedDict, defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from . import layerb, layerc, session_checks
from . import ruleset as R
from .checks import check, count_severities, verdict
from .layerb import Context
from .models import CaptureSession


def _parse_error_records(session: CaptureSession) -> List[dict]:
    """Fail-open parse errors, surfaced as ERRORs (PRD §11.1)."""
    return [check(f"PARSE:{pe['file']}", f"parse:{pe['file']}", R.ERROR,
                  code="PARSE_ERROR", source_file=pe["file"], spec_ref="§11.1",
                  observed=pe["message"], expected="valid JSON",
                  message=f"{pe['file']} failed to parse: {pe['message']}")
            for pe in session.parse_errors]


def _collapse(records: List[dict]) -> List[dict]:
    """Merge per-chunk findings that say the same thing.

    Grouped by (id, severity, code, message) so distinct failures stay distinct
    -- two chunks with different duration conflicts remain two records -- while a
    defect repeated across every chunk becomes one, with the chunk list attached.
    """
    grouped: "OrderedDict[tuple, dict]" = OrderedDict()
    for rec in records:
        key = (rec["id"], rec["severity"], rec.get("code"), rec.get("message"))
        hit = grouped.get(key)
        if hit is None:
            merged = dict(rec)
            merged["chunks"] = [rec["chunk"]] if rec.get("chunk") else []
            merged.pop("chunk", None)
            grouped[key] = merged
        elif rec.get("chunk"):
            hit["chunks"].append(rec["chunk"])
    out = []
    for rec in grouped.values():
        rec["chunk_count"] = len(rec["chunks"])
        if rec["chunk_count"] > 1:
            shown = ", ".join(str(c) for c in rec["chunks"][:4])
            if rec["chunk_count"] > 4:
                shown += f", +{rec['chunk_count'] - 4} more"
            rec["message"] = f"{rec['message']}  [{rec['chunk_count']} chunks: {shown}]"
        out.append(rec)
    return out


def validate_session(session: CaptureSession, ctx: Context,
                     validated_at: str) -> Dict[str, Any]:
    """Build the full §5.2 report for one session (= one episode)."""
    checks: List[dict] = []
    checks += _parse_error_records(session)
    checks += session_checks.run(session)

    # Session-level file checks run once, against any chunk -- they read the
    # session's shared video/depth facts, which every chunk carries a copy of.
    if session.chunks:
        for fn in layerc.SESSION_FILE_CHECKS:
            checks.append(fn(session.chunks[0]))

    per_chunk: List[dict] = []
    for chunk in session.chunks:
        label = chunk.chunk_name or f"#{chunk.chunk_index}"
        for rec in layerb.run(chunk, ctx) + layerc.run_chunk(chunk):
            rec["chunk"] = label
            per_chunk.append(rec)
    checks += _collapse(per_chunk)

    return {
        "package_id": session.session_id,
        "episode_uuid": session.episode_uuid,
        "episode_uuid_source": session.episode_uuid_source,
        "chunk_count": session.chunk_count,
        "chunk_names": [c.chunk_name for c in session.chunks],
        "validated_at": validated_at,
        "pipeline_version": R.PIPELINE_VERSION,
        "spec_version": R.SPEC_VERSION,
        "verdict": verdict(checks),
        "counts": dict(count_severities(checks)),
        "checks": checks,
    }


#: Back-compat alias: callers that still say "package" get a session report.
validate_package = validate_session


def _enforce_uuid_uniqueness(reports: List[Dict[str, Any]]) -> None:
    """Delivery-level B1.1u uniqueness: flag any episode uuid used more than once."""
    seen: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for rep in reports:
        if rep["episode_uuid"]:
            seen[rep["episode_uuid"]].append(rep)
    for uid, dupes in seen.items():
        if len(dupes) > 1:
            ids = ", ".join(d["package_id"] for d in dupes)
            for rep in dupes:
                rep["checks"].append(check(
                    "B1.1u", "episode_uuid_unique", R.ERROR, code="EPISODE_UUID_DUPLICATE",
                    source_file="metadata.json", spec_ref="Doc1 §5.1", observed=uid,
                    expected="unique within delivery",
                    message=f"episode uuid '{uid}' is shared by sessions: {ids}"))
                rep["counts"] = dict(count_severities(rep["checks"]))
                rep["verdict"] = verdict(rep["checks"])


def _aggregate(reports: List[Dict[str, Any]]) -> Dict[str, Any]:
    verdicts: Counter = Counter(r["verdict"] for r in reports)
    severities: Counter = Counter()
    code_counts: Counter = Counter()
    for rep in reports:
        for sev, n in rep["counts"].items():
            severities[sev] += n
        for c in rep["checks"]:
            if c["severity"] in (R.ERROR, R.WARN, R.FORMAT_GAP) and c.get("code"):
                code_counts[c["code"]] += 1
    return {
        "packages_total": len(reports),
        "chunks_total": sum(r.get("chunk_count", 0) for r in reports),
        "verdicts": dict(verdicts),
        "packages_rejected": verdicts.get(R.VERDICT_REJECT, 0),
        "packages_review": verdicts.get(R.VERDICT_REVIEW, 0),
        "packages_accepted": verdicts.get(R.VERDICT_ACCEPT, 0),
        "severity_totals": dict(severities),
        "top_findings": [{"code": c, "count": n}
                         for c, n in code_counts.most_common()],
    }


def run(sessions: List[CaptureSession], now_unix: float,
        validated_at: Optional[str] = None) -> Dict[str, Any]:
    """Validate every session; return per-session reports + delivery aggregate."""
    if validated_at is None:
        validated_at = datetime.fromtimestamp(now_unix, timezone.utc).isoformat().replace(
            "+00:00", "Z")
    ctx = Context(now_unix=now_unix)
    reports = [validate_session(s, ctx, validated_at) for s in sessions]
    _enforce_uuid_uniqueness(reports)
    return {"packages": reports, "aggregate": _aggregate(reports)}
