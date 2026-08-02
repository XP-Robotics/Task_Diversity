"""Mode B -- diversity metrics over the whole delivery. Pure and I/O-free.

Consumes Episodes whose ``video_seconds`` have already been measured by ingest
(or left None when ffprobe is unavailable). All duration reconciliation, field
normalization and thresholding happen here with no I/O.

Every check returns a dict:
    {name, status, detail, offenders, extra?}
with status in {PASS, FAIL, WARN, NOT_COMPUTABLE, INFO}.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from . import config as C
from . import normalize as N
from .models import Episode

# Canonical token sets for diversity fields (built once).
_CANONS = {name: N.canon_set(aliases) for name, aliases in C.DIVERSITY_ALIASES.items()}


def rebuild_canons() -> None:
    """Recompute diversity canon sets from ``config.DIVERSITY_ALIASES``.

    Called after a source profile merges extra alias spellings. Generic runs
    never call this, so default behaviour is unchanged.
    """
    global _CANONS
    _CANONS = {name: N.canon_set(aliases) for name, aliases in C.DIVERSITY_ALIASES.items()}


# ---------------------------------------------------------------------------
# Field normalization helpers (declared fields only -- never inferred).
# ---------------------------------------------------------------------------
def _norm_difficulty(raw: Any) -> Optional[str]:
    if N.is_empty(raw):
        return None
    return C.DIFFICULTY_SYNONYMS.get(N.canon_key(raw))


def _norm_business_size(raw: Any) -> Optional[str]:
    if N.is_empty(raw):
        return None
    return C.BUSINESS_SIZE_SYNONYMS.get(N.canon_key(raw))


def _norm_worker_type(raw: Any) -> Optional[str]:
    if N.is_empty(raw):
        return None
    return C.WORKER_TYPE_SYNONYMS.get(N.canon_key(raw))


def _norm_bool(raw: Any) -> Optional[bool]:
    if isinstance(raw, bool):
        return raw
    if N.is_empty(raw):
        return None
    tok = N.canon_key(raw)
    if tok in C.TRUTHY:
        return True
    if tok in C.FALSY:
        return False
    return None


def _to_float(raw: Any) -> Optional[float]:
    if N.is_empty(raw):
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Duration reconciliation (§6). Pure: given a measured video length (or None)
# and metadata timestamps, decide the hours + source + warnings.
# ---------------------------------------------------------------------------
def resolve_duration(video_seconds: Optional[float], start: Any, end: Any) -> Tuple[float, str, List[str]]:
    warnings: List[str] = []
    s = _to_float(start)
    e = _to_float(end)
    meta_seconds = e - s if (s is not None and e is not None and e > s) else None

    if video_seconds is not None and meta_seconds is not None:
        source = "both"
        chosen = video_seconds
        diff = abs(video_seconds - meta_seconds)
        rel = diff / meta_seconds if meta_seconds else float("inf")
        if diff > C.DURATION_ABS_TOL_S and rel > C.DURATION_REL_TOL:
            warnings.append(
                f"duration mismatch: video={video_seconds:.1f}s vs "
                f"metadata={meta_seconds:.1f}s (kept video)"
            )
    elif video_seconds is not None:
        source, chosen = "video", video_seconds
    elif meta_seconds is not None:
        source, chosen = "metadata", meta_seconds
        warnings.append("no measured video duration; used metadata timestamps")
    else:
        source, chosen = "none", 0.0
        warnings.append("no duration available (no video, no valid timestamps); using 0h")

    return chosen / 3600.0, source, warnings


# ---------------------------------------------------------------------------
# Per-episode diversity row.
# ---------------------------------------------------------------------------
@dataclass
class DivRow:
    stem: str
    hours: float
    duration_source: str
    task_id: Optional[str]
    operator_id: Optional[str]
    environment_id: Optional[str]
    environment_l1: Optional[str]
    environment_l2: Optional[str]
    environment_l3: Optional[str]
    difficulty: Optional[str]
    business_size: Optional[str]
    worker_type: Optional[str]
    human_interaction: Optional[bool]
    warnings: List[str] = field(default_factory=list)


def _str_or_none(raw: Any) -> Optional[str]:
    return None if N.is_empty(raw) else str(raw)


def build_row(ep: Episode) -> DivRow:
    flat = ep.flat_meta or {}

    def val(name: str) -> Any:
        return N.value(flat, _CANONS[name])

    hours, source, warns = resolve_duration(
        ep.video_seconds, val("start_time_unix"), val("end_time_unix")
    )

    raw_diff = val("task_difficulty")
    difficulty = _norm_difficulty(raw_diff)
    if difficulty is None and not N.is_empty(raw_diff):
        warns.append(f"unrecognized difficulty '{raw_diff}'; excluded from difficulty checks")
    elif difficulty is None:
        warns.append("difficulty absent; excluded from difficulty checks")

    row = DivRow(
        stem=ep.stem,
        hours=hours,
        duration_source=source,
        task_id=_str_or_none(val("task_id")),
        operator_id=_str_or_none(val("operator_id")),
        environment_id=_str_or_none(val("environment_id")),
        environment_l1=_str_or_none(val("environment_l1")),
        environment_l2=_str_or_none(val("environment_l2")),
        environment_l3=_str_or_none(val("environment_l3")),
        difficulty=difficulty,
        business_size=_norm_business_size(val("business_size")),
        worker_type=_norm_worker_type(val("worker_type")),
        human_interaction=_norm_bool(val("human_interaction")),
        warnings=warns,
    )
    return row


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
def _pct(part: float, whole: float) -> float:
    return (100.0 * part / whole) if whole else 0.0


def _severity(label: Optional[str]) -> int:
    return C.DIFFICULTY_SEVERITY.get(label or "", 0)


def resolve_group_difficulty(rows: List[DivRow]) -> Tuple[Optional[str], bool]:
    """Most-severe declared difficulty across a group; conflict flag if labels differ."""
    labels = {r.difficulty for r in rows if r.difficulty is not None}
    if not labels:
        return None, False
    best = max(labels, key=lambda d: C.DIFFICULTY_SEVERITY[d])
    return best, len(labels) > 1


def _is_factory(rows: List[DivRow]) -> bool:
    for r in rows:
        l1 = (r.environment_l1 or "").lower()
        if any(k in l1 for k in C.FACTORY_KEYWORDS):
            return True
    return False


def _check(name: str, status: str, detail: str,
           offenders: Optional[List[Dict[str, Any]]] = None,
           extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    out = {"name": name, "status": status, "detail": detail, "offenders": offenders or []}
    if extra:
        out["extra"] = extra
    return out


# ===========================================================================
# Checks
# ===========================================================================
def check_environment_l1(rows: List[DivRow]) -> Dict[str, Any]:
    name = "Environment category (L1)"
    hours = defaultdict(float)
    for r in rows:
        if r.environment_l1:
            hours[r.environment_l1] += r.hours
    if not hours:
        return _check(name, "NOT_COMPUTABLE", "no environment_l1 labels present")

    total = sum(hours.values())
    ranked = sorted(hours.items(), key=lambda kv: -kv[1])
    dist = [{"label": k, "hours": round(v, 3), "pct": round(_pct(v, total), 2)} for k, v in ranked]

    offenders = [d for d in dist if d["pct"] > C.ENV_L1_SINGLE_MAX_PCT]
    top5 = sum(v for _, v in ranked[:5])
    top10 = sum(v for _, v in ranked[:10])
    top5_pct = _pct(top5, total)
    top10_pct = _pct(top10, total)
    distinct = len(hours)

    fails = []
    if offenders:
        fails.append(f"{len(offenders)} category(ies) exceed {C.ENV_L1_SINGLE_MAX_PCT:g}%")
    if top5_pct > C.ENV_L1_TOP5_MAX_PCT:
        fails.append(f"top-5={top5_pct:.1f}% > {C.ENV_L1_TOP5_MAX_PCT:g}%")
    if top10_pct > C.ENV_L1_TOP10_MAX_PCT:
        fails.append(f"top-10={top10_pct:.1f}% > {C.ENV_L1_TOP10_MAX_PCT:g}%")
    if distinct < C.ENV_L1_MIN_DISTINCT:
        fails.append(f"distinct={distinct} < {C.ENV_L1_MIN_DISTINCT}")

    status = "FAIL" if fails else "PASS"
    detail = (f"distinct={distinct}, top-5={top5_pct:.1f}%, top-10={top10_pct:.1f}%; "
              + ("; ".join(fails) if fails else "within limits"))
    return _check(name, status, detail, offenders, extra={"distribution": dist})


def check_difficulty_mix(rows: List[DivRow]) -> Dict[str, Any]:
    name = "Difficulty mix"
    hours = defaultdict(float)
    unknown = 0.0
    for r in rows:
        if r.difficulty is None:
            unknown += r.hours
        else:
            hours[r.difficulty] += r.hours
    known_total = sum(hours.values())
    if known_total <= 0:
        status = "NOT_COMPUTABLE" if unknown <= 0 else "WARN"
        detail = "no known-difficulty hours" + (f"; {unknown:.2f}h unknown" if unknown else "")
        return _check(name, status, detail)

    easy = _pct(hours["easy"], known_total)
    hard = _pct(hours["hard"], known_total)
    medium = _pct(hours["medium"], known_total)

    fails = []
    if easy > C.DIFF_EASY_MAX_PCT:
        fails.append(f"easy={easy:.1f}% > {C.DIFF_EASY_MAX_PCT:g}%")
    if hard < C.DIFF_HARD_MIN_PCT:
        fails.append(f"hard={hard:.1f}% < {C.DIFF_HARD_MIN_PCT:g}%")

    if fails:
        status = "FAIL"
    elif unknown > 0:
        status = "WARN"
    else:
        status = "PASS"

    detail = (f"easy={easy:.1f}% (max {C.DIFF_EASY_MAX_PCT:g}), "
              f"hard={hard:.1f}% (min {C.DIFF_HARD_MIN_PCT:g}), "
              f"medium={medium:.1f}% (target {C.DIFF_MEDIUM_TARGET_PCT:g}, INFO)")
    if unknown > 0:
        detail += f"; {unknown:.2f}h unknown-difficulty (excluded)"
    if fails:
        detail += "; " + "; ".join(fails)
    return _check(name, status, detail, extra={
        "easy_pct": round(easy, 2), "medium_pct": round(medium, 2),
        "hard_pct": round(hard, 2), "unknown_hours": round(unknown, 3),
    })


def _group(rows: List[DivRow], keyfn) -> Dict[Any, List[DivRow]]:
    g: Dict[Any, List[DivRow]] = defaultdict(list)
    for r in rows:
        k = keyfn(r)
        if k is not None:
            g[k].append(r)
    return g


def check_per_task_share(rows: List[DivRow]) -> Dict[str, Any]:
    name = "Per-task share"
    total = sum(r.hours for r in rows)
    groups = _group(rows, lambda r: r.task_id)
    if not groups:
        return _check(name, "NOT_COMPUTABLE", "no task_id present")

    offenders = []
    conflicts = 0
    for task, grp in groups.items():
        gh = sum(r.hours for r in grp)
        diff, conflict = resolve_group_difficulty(grp)
        conflicts += 1 if conflict else 0
        share = _pct(gh, total)
        reasons = []
        if gh > C.PER_TASK_ABS_MAX_H:
            reasons.append(f"{gh:.1f}h > {C.PER_TASK_ABS_MAX_H:g}h absolute")
        cap = C.PER_TASK_SHARE_MAX_PCT.get(diff)
        if cap is not None and share > cap:
            reasons.append(f"{share:.1f}% > {cap:g}% ({diff})")
        if reasons:
            offenders.append({"task_id": task, "difficulty": diff, "hours": round(gh, 3),
                              "pct": round(share, 2), "reason": "; ".join(reasons),
                              "conflict": conflict})

    status = "FAIL" if offenders else "PASS"
    detail = f"{len(groups)} task(s); {len(offenders)} over cap"
    if conflicts:
        detail += f"; {conflicts} task(s) with mixed difficulty (used most severe)"
    return _check(name, status, detail, offenders)


def _by_difficulty_cap_check(name: str, rows: List[DivRow], keyfn, caps: Dict[str, float],
                             label_key: str, no_data: str) -> Dict[str, Any]:
    """Generic 'hours per group must be <= cap[difficulty]' check."""
    groups = _group(rows, keyfn)
    if not groups:
        return _check(name, "NOT_COMPUTABLE", no_data)
    offenders = []
    conflicts = 0
    for key, grp in groups.items():
        gh = sum(r.hours for r in grp)
        diff, conflict = resolve_group_difficulty(grp)
        conflicts += 1 if conflict else 0
        cap = caps.get(diff)
        if cap is not None and gh > cap:
            offenders.append({label_key: key, "difficulty": diff, "hours": round(gh, 3),
                              "cap_h": cap, "conflict": conflict})
    status = "FAIL" if offenders else "PASS"
    detail = f"{len(groups)} group(s); {len(offenders)} over cap"
    if conflicts:
        detail += f"; {conflicts} with mixed difficulty (used most severe)"
    return _check(name, status, detail, offenders)


def check_task_env(rows: List[DivRow], factory_dynamic: bool) -> Dict[str, Any]:
    name = "Task x Environment"
    groups = _group(rows, lambda r: (r.task_id, r.environment_id)
                    if r.task_id and r.environment_id else None)
    if not groups:
        return _check(name, "NOT_COMPUTABLE", "no (task_id, environment_id) pairs present")

    factory_cap = C.FACTORY_CAP_DYNAMIC_H if factory_dynamic else C.FACTORY_CAP_REPETITIVE_H
    offenders = []
    conflicts = 0
    for (task, env), grp in groups.items():
        gh = sum(r.hours for r in grp)
        factory = _is_factory(grp)
        if factory:
            cap = factory_cap
            diff_label = "factory"
            conflict = False
        else:
            diff, conflict = resolve_group_difficulty(grp)
            conflicts += 1 if conflict else 0
            cap = C.TASK_ENV_MAX_H.get(diff)
            diff_label = diff
        if cap is not None and gh > cap:
            offenders.append({"task_id": task, "environment_id": env,
                              "difficulty": diff_label, "hours": round(gh, 3),
                              "cap_h": cap, "factory": factory, "conflict": conflict})
    status = "FAIL" if offenders else "PASS"
    detail = (f"{len(groups)} pair(s); {len(offenders)} over cap; "
              f"factory cap={factory_cap:g}h "
              f"({'dynamic' if factory_dynamic else 'repetitive'})")
    if conflicts:
        detail += f"; {conflicts} with mixed difficulty (used most severe)"
    return _check(name, status, detail, offenders)


def check_op_task_env(rows: List[DivRow]) -> Dict[str, Any]:
    return _by_difficulty_cap_check(
        "Operator x Task x Environment", rows,
        lambda r: (r.operator_id, r.task_id, r.environment_id)
        if r.operator_id and r.task_id and r.environment_id else None,
        C.OP_TASK_ENV_MAX_H, "group",
        "no (operator_id, task_id, environment_id) triples present",
    )


def check_env_op(rows: List[DivRow]) -> Dict[str, Any]:
    name = "Environment x Operator"
    groups = _group(rows, lambda r: (r.environment_id, r.operator_id)
                    if r.environment_id and r.operator_id else None)
    if not groups:
        return _check(name, "NOT_COMPUTABLE", "no (environment_id, operator_id) pairs present")
    offenders = []
    for (env, op), grp in groups.items():
        gh = sum(r.hours for r in grp)
        if gh > C.ENV_OP_MAX_H:
            offenders.append({"environment_id": env, "operator_id": op,
                              "hours": round(gh, 3), "cap_h": C.ENV_OP_MAX_H})
    status = "FAIL" if offenders else "PASS"
    return _check(name, status, f"{len(groups)} pair(s); {len(offenders)} over "
                  f"{C.ENV_OP_MAX_H:g}h", offenders)


def check_business_size(rows: List[DivRow]) -> Dict[str, Any]:
    name = "Business-size caps per environment"
    if not any(r.business_size is not None for r in rows):
        return _check(name, "NOT_COMPUTABLE", "requires business_size; field absent from delivery")
    # group by (environment_id, business_size)
    groups: Dict[Tuple[Any, str], float] = defaultdict(float)
    for r in rows:
        if r.environment_id and r.business_size:
            groups[(r.environment_id, r.business_size)] += r.hours
    offenders = []
    for (env, size), gh in groups.items():
        cap = C.BUSINESS_SIZE_MAX_H.get(size)
        if cap is not None and gh > cap:
            offenders.append({"environment_id": env, "business_size": size,
                              "hours": round(gh, 3), "cap_h": cap})
    status = "FAIL" if offenders else "PASS"
    return _check(name, status, f"{len(groups)} (env, size) group(s); "
                  f"{len(offenders)} over cap", offenders)


def check_workforce(rows: List[DivRow]) -> Dict[str, Any]:
    name = "Workforce mix"
    if not any(r.worker_type is not None for r in rows):
        return _check(name, "NOT_COMPUTABLE", "requires worker_type; field absent from delivery")
    real = sum(r.hours for r in rows if r.worker_type == "real")
    contractor = sum(r.hours for r in rows if r.worker_type == "contractor")
    total = real + contractor
    if total <= 0:
        return _check(name, "NOT_COMPUTABLE", "no hours attributed to real/contractor workers")
    real_pct = _pct(real, total)
    contractor_pct = _pct(contractor, total)
    fails = []
    if real_pct < C.WORKFORCE_REAL_MIN_PCT:
        fails.append(f"real={real_pct:.1f}% < {C.WORKFORCE_REAL_MIN_PCT:g}%")
    if contractor_pct > C.WORKFORCE_CONTRACTOR_MAX_PCT:
        fails.append(f"contractor={contractor_pct:.1f}% > {C.WORKFORCE_CONTRACTOR_MAX_PCT:g}%")
    status = "FAIL" if fails else "PASS"
    detail = f"real={real_pct:.1f}%, contractor={contractor_pct:.1f}% by hours"
    if fails:
        detail += "; " + "; ".join(fails)
    return _check(name, status, detail)


def check_human_interaction(rows: List[DivRow]) -> Dict[str, Any]:
    name = "Human-human interaction"
    known = [r for r in rows if r.human_interaction is not None]
    if not known:
        return _check(name, "NOT_COMPUTABLE", "requires human_interaction; field absent from delivery")
    n_true = sum(1 for r in known if r.human_interaction)
    pct = _pct(n_true, len(rows))  # % of all episodes
    status = "PASS" if pct >= C.HUMAN_INTERACTION_MIN_PCT else "WARN"
    detail = (f"{n_true}/{len(rows)} episodes = {pct:.1f}% interactive "
              f"(target >= {C.HUMAN_INTERACTION_MIN_PCT:g}%)")
    return _check(name, status, detail)


# ===========================================================================
# Driver
# ===========================================================================
FAIL_STATUSES = {"FAIL"}


def run(episodes: List[Episode], factory_dynamic: bool = False) -> Dict[str, Any]:
    rows = [build_row(ep) for ep in episodes]
    checks = [
        check_environment_l1(rows),
        check_difficulty_mix(rows),
        check_per_task_share(rows),
        check_task_env(rows, factory_dynamic),
        check_op_task_env(rows),
        check_env_op(rows),
        check_business_size(rows),
        check_workforce(rows),
        check_human_interaction(rows),
    ]
    status_counts = defaultdict(int)
    for c in checks:
        status_counts[c["status"]] += 1
    any_fail = any(c["status"] in FAIL_STATUSES for c in checks)

    total_hours = sum(r.hours for r in rows)
    row_dump = [{
        "stem": r.stem, "hours": round(r.hours, 4), "duration_source": r.duration_source,
        "task_id": r.task_id, "operator_id": r.operator_id,
        "environment_id": r.environment_id, "environment_l1": r.environment_l1,
        "environment_l2": r.environment_l2, "environment_l3": r.environment_l3,
        "difficulty": r.difficulty, "business_size": r.business_size,
        "worker_type": r.worker_type, "human_interaction": r.human_interaction,
        "warnings": r.warnings,
    } for r in rows]

    return {
        "checks": checks,
        "summary": {
            "episodes": len(rows),
            "total_hours": round(total_hours, 3),
            "status_counts": dict(status_counts),
            "any_fail": any_fail,
            "factory_dynamic": factory_dynamic,
        },
        "rows": row_dump,
    }
