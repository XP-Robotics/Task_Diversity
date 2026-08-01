"""Turn a validation report into the per-session *missing parameter matrix*.

The schema report answers "what is wrong with this session" as a flat list of
check records. The database wants the transposed view: one document per session,
keyed by session id, whose value is a matrix of

    rows    = the session's chunks
    columns = the spec parameters (fixed registry order, below)
    cells   = a status code, worst-wins

A fixed column vector is the point: every session document carries the same
columns in the same order, so documents stack into one delivery-wide matrix
without realignment, and a column index means the same thing in every session.
Findings whose check id is not in the registry (parse errors, checks from a
future ruleset) are never dropped -- they land in ``parse_errors`` /
``unmapped``.

Pure and I/O-free; :mod:`qa.mongo_store` does the persistence.
"""

from __future__ import annotations

from collections import OrderedDict
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import ruleset as R

#: Document schema version. Bump when the column vector or the status codes
#: change, so a consumer can tell a stale document from a current one.
MATRIX_VERSION = "1.0.0"

# ---------------------------------------------------------------------------
# Cell status codes. Ordered by severity so merging two findings for the same
# cell is max(), and "is this parameter missing" is a >= comparison.
# ---------------------------------------------------------------------------
NOT_EVALUATED = -1     # the check emitted no record for this row (older report)
OK = 0                 # PASS
INFO = 1
CEILING = 2
WARN = 3
FORMAT_GAP = 4         # required, but has no location in the capture format
BLOCKED = 5            # could not be evaluated
ERROR = 6              # required and absent/invalid in delivered data

#: Cells at or above this are what "missing" means: the parameter is not usable
#: downstream, whoever is at fault. WARN and below are present-but-imperfect.
MISSING_FROM = FORMAT_GAP

STATUS_OF: Dict[str, int] = {
    R.PASS: OK,
    R.INFO: INFO,
    R.CEILING: CEILING,
    R.WARN: WARN,
    R.FORMAT_GAP: FORMAT_GAP,
    R.BLOCKED: BLOCKED,
    R.ERROR: ERROR,
}

#: Emitted on every document so a reader never needs this module to interpret it.
LEGEND: Dict[str, str] = {
    str(NOT_EVALUATED): "NOT_EVALUATED",
    str(OK): "OK",
    str(INFO): R.INFO,
    str(CEILING): R.CEILING,
    str(WARN): R.WARN,
    str(FORMAT_GAP): R.FORMAT_GAP,
    str(BLOCKED): R.BLOCKED,
    str(ERROR): R.ERROR,
}

#: Which severities count as a missing parameter, split the way the delivery
#: reports split them: vendor fault, capture-app gap, unevaluable.
MISSING_SEVERITIES: Tuple[str, ...] = (R.ERROR, R.BLOCKED, R.FORMAT_GAP)

# ---------------------------------------------------------------------------
# Column registry: check id -> parameter name. Order is the matrix column order
# and is part of the contract (see MATRIX_VERSION) -- append, never reorder.
#
# ``scope`` says whether the check is decided per chunk or once for the whole
# session; a session-scope finding is written across every row, because it is
# true of every chunk in that session.
# ---------------------------------------------------------------------------
_REGISTRY: Tuple[Tuple[str, str, str], ...] = (
    ("S1",    "metadata",                 "session"),
    ("S2",    "chunk_coherence",          "session"),
    ("S3",    "chunk_continuity",         "session"),
    ("B1.1",  "episode_uuid",             "session"),
    ("B1.2",  "operator_id",              "chunk"),
    ("B1.3",  "episode_start_time",       "chunk"),
    ("B1.4",  "device_identity",          "chunk"),
    ("B1.5",  "software_version",         "chunk"),
    ("B1.6",  "camera_configuration",     "chunk"),
    ("B1.7",  "task_description",         "chunk"),
    ("B1.8",  "task_category",            "chunk"),
    ("B1.9",  "environment_label",        "chunk"),
    ("B1.10", "indoor_outdoor",           "chunk"),
    ("B1.11", "episode_end_time",         "chunk"),
    ("B2.1",  "task_id",                  "chunk"),
    ("B2.2",  "environment_id",           "chunk"),
    ("B2.3",  "frame_timestamps",         "chunk"),
    ("B2.4",  "utc_reference",            "chunk"),
    ("B2.5",  "geo_location",             "chunk"),
    ("B2.6",  "subtask_annotations",      "chunk"),
    ("B2.7",  "instruction",              "chunk"),
    ("C1",    "frame_count_concordance",  "chunk"),
    ("C2",    "duration_concordance",     "chunk"),
    ("C3",    "imu_coverage",             "chunk"),
    ("C4",    "referenced_files",         "session"),
    ("C5",    "video_source",             "session"),
    ("C6",    "sensor_relation",          "chunk"),
    ("C7",    "payloads",                 "chunk"),
    ("C8",    "camera_intrinsics",        "chunk"),
    ("C9",    "depth_geometry",           "chunk"),
)

COLUMNS: List[str] = [name for _, name, _ in _REGISTRY]
COLUMN_CHECKS: List[str] = [cid for cid, _, _ in _REGISTRY]
COLUMN_SCOPE: List[str] = [scope for _, _, scope in _REGISTRY]

#: check id -> column index. Ids that decide the same parameter share a column:
#: B1.1u (delivery-level uuid uniqueness) is still the episode_uuid parameter,
#: and legacy D2 is the chunk-coherence rule S2 replaced.
_COLUMN_OF: Dict[str, int] = {cid: i for i, cid in enumerate(COLUMN_CHECKS)}
_COLUMN_OF["B1.1u"] = _COLUMN_OF["B1.1"]
_COLUMN_OF["D2"] = _COLUMN_OF["S2"]


def column_index(check_id: str) -> Optional[int]:
    """Matrix column a check id writes to, or None if it is not a parameter."""
    return _COLUMN_OF.get(check_id)


# ---------------------------------------------------------------------------
# Row labels
# ---------------------------------------------------------------------------
def _row_labels(report: Dict[str, Any]) -> List[str]:
    """The session's chunk labels, exactly as ``validate`` writes them.

    ``validate._collapse`` tags each per-chunk finding with ``chunk_name`` or
    ``#<index>``; reproducing that here is what lets a finding find its row. A
    report with no chunk information at all (a pre-session-refactor report) gets
    a single row named after the session.
    """
    names = report.get("chunk_names")
    if names:
        return [n if n else f"#{i}" for i, n in enumerate(names)]
    count = report.get("chunk_count") or 0
    if count:
        return [f"#{i}" for i in range(count)]
    return [str(report.get("package_id") or "session")]


def _detail(rec: Dict[str, Any], status: int, rows: List[str]) -> Dict[str, Any]:
    return {
        "check": rec["id"],
        "severity": rec["severity"],
        "status": status,
        "code": rec.get("code"),
        "message": rec.get("message") or "",
        "observed": rec.get("observed"),
        "expected": rec.get("expected"),
        "source_file": rec.get("source_file"),
        "spec_ref": rec.get("spec_ref"),
        "rows": rows,
    }


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------
def build_document(report: Dict[str, Any], *, session_id: Optional[str] = None,
                   source_uri: Optional[str] = None) -> Dict[str, Any]:
    """One session report -> one Mongo document keyed by session id.

    ``_id`` is the session id, which makes writes idempotent: re-validating a
    session replaces its document instead of accumulating duplicates.
    """
    sid = session_id or str(report.get("package_id") or "")
    rows = _row_labels(report)
    matrix: List[List[int]] = [[NOT_EVALUATED] * len(COLUMNS) for _ in rows]
    row_of = {label: i for i, label in enumerate(rows)}

    details: Dict[str, Dict[str, Any]] = {}
    parse_errors: List[Dict[str, Any]] = []
    unmapped: List[Dict[str, Any]] = []

    for rec in report.get("checks", []):
        col = _COLUMN_OF.get(rec["id"])
        if col is None:
            entry = {"check": rec["id"], "severity": rec["severity"],
                     "code": rec.get("code"), "message": rec.get("message") or "",
                     "source_file": rec.get("source_file")}
            (parse_errors if str(rec["id"]).startswith("PARSE:") else unmapped).append(entry)
            continue

        status = STATUS_OF.get(rec["severity"], NOT_EVALUATED)

        # A record naming chunks applies to those; one naming none is
        # session-scope and applies to every row. A named chunk the session
        # never declared still gets a row, so nothing is silently dropped.
        labels = [str(c) for c in (rec.get("chunks") or [])]
        for label in labels:
            if label not in row_of:
                row_of[label] = len(rows)
                rows.append(label)
                matrix.append([NOT_EVALUATED] * len(COLUMNS))
        targets = [row_of[label] for label in labels] if labels else list(range(len(rows)))

        for r in targets:
            if status > matrix[r][col]:
                matrix[r][col] = status

        if status >= MISSING_FROM:
            name = COLUMNS[col]
            hit = details.get(name)
            touched = [rows[r] for r in targets]
            if hit is None or status > hit["status"]:
                details[name] = _detail(rec, status, touched)
            elif status == hit["status"]:
                hit["rows"] = sorted(set(hit["rows"]) | set(touched))

    missing_by_row = [
        {"row": label, "index": i,
         "missing": [COLUMNS[c] for c in range(len(COLUMNS)) if matrix[i][c] >= MISSING_FROM]}
        for i, label in enumerate(rows)
    ]
    missing_parameters = sorted({p for r in missing_by_row for p in r["missing"]})
    by_severity = {
        sev: sorted(p for p in missing_parameters
                    if details.get(p, {}).get("severity") == sev)
        for sev in MISSING_SEVERITIES
    }

    doc: Dict[str, Any] = {
        "_id": sid,
        "session_id": sid,
        "episode_uuid": report.get("episode_uuid"),
        "episode_uuid_source": report.get("episode_uuid_source"),
        "verdict": report.get("verdict"),
        "chunk_count": report.get("chunk_count", len(rows)),
        "counts": report.get("counts") or {},

        # --- the matrix ---
        "rows": rows,
        "columns": list(COLUMNS),
        "column_checks": list(COLUMN_CHECKS),
        "column_scope": list(COLUMN_SCOPE),
        "matrix": matrix,
        "legend": dict(LEGEND),
        "missing_from": MISSING_FROM,

        # --- the same thing, denormalized for querying ---
        "missing_parameters": missing_parameters,
        "missing_count": len(missing_parameters),
        "missing_by_severity": by_severity,
        "missing_by_row": missing_by_row,
        "details": details,

        "parse_errors": parse_errors,
        "unmapped": unmapped,

        "validated_at": report.get("validated_at"),
        "pipeline_version": report.get("pipeline_version") or R.PIPELINE_VERSION,
        "spec_version": report.get("spec_version") or R.SPEC_VERSION,
        "matrix_version": MATRIX_VERSION,
    }
    if source_uri:
        doc["source_uri"] = source_uri
    return doc


def build_documents(result: Dict[str, Any], *,
                    source_uri: Optional[str] = None,
                    session_of: Optional[Callable[[str], Optional[str]]] = None
                    ) -> List[Dict[str, Any]]:
    """Every session in a delivery-level ``validate.run()`` result.

    The current pipeline reports one package per session, so the default is one
    document per report entry. ``session_of`` maps a package id onto a session
    id for reports that predate that -- a per-video report groups back into one
    document per session, whose rows are its videos (see :func:`merge_documents`).
    """
    docs = [build_document(rep, source_uri=source_uri)
            for rep in result.get("packages", [])]
    if session_of is None:
        return docs

    grouped: "OrderedDict[str, List[Dict[str, Any]]]" = OrderedDict()
    for doc in docs:
        grouped.setdefault(session_of(doc["_id"]) or doc["_id"], []).append(doc)
    return [members[0] if len(members) == 1 and members[0]["_id"] == sid
            else merge_documents(sid, members)
            for sid, members in grouped.items()]


_VERDICT_RANK = {R.VERDICT_ACCEPT: 0, R.VERDICT_REVIEW: 1, R.VERDICT_REJECT: 2}


def merge_documents(session_id: str, docs: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Fold several package documents into one session document.

    Rows are concatenated -- a package that contributed a single self-named row
    becomes one row named after the package, so every row still says which unit
    it came from. Cells, details and verdicts merge worst-wins, exactly as they
    do within a single document.
    """
    rows: List[str] = []
    matrix: List[List[int]] = []
    details: Dict[str, Dict[str, Any]] = {}
    parse_errors: List[Dict[str, Any]] = []
    unmapped: List[Dict[str, Any]] = []
    uuids: List[str] = []
    counts: Dict[str, int] = {}
    verdict = R.VERDICT_ACCEPT

    for doc in docs:
        member = doc["_id"]
        self_named = len(doc["rows"]) == 1 and doc["rows"][0] == member
        for i, label in enumerate(doc["rows"]):
            rows.append(member if self_named else f"{member}/{label}")
            matrix.append(list(doc["matrix"][i]))
        for name, detail in doc.get("details", {}).items():
            hit = details.get(name)
            if hit is None or detail["status"] > hit["status"]:
                merged = dict(detail)
                merged["rows"] = [member if self_named else f"{member}/{r}"
                                  for r in detail.get("rows", [])]
                details[name] = merged
        parse_errors += doc.get("parse_errors", [])
        unmapped += doc.get("unmapped", [])
        if doc.get("episode_uuid"):
            uuids.append(doc["episode_uuid"])
        for sev, n in (doc.get("counts") or {}).items():
            counts[sev] = counts.get(sev, 0) + n
        if _VERDICT_RANK.get(doc.get("verdict"), 0) > _VERDICT_RANK[verdict]:
            verdict = doc["verdict"]

    missing_by_row = [
        {"row": label, "index": i,
         "missing": [COLUMNS[c] for c in range(len(COLUMNS)) if matrix[i][c] >= MISSING_FROM]}
        for i, label in enumerate(rows)
    ]
    missing_parameters = sorted({p for r in missing_by_row for p in r["missing"]})
    distinct_uuids = sorted(set(uuids))
    head = docs[0]

    return {
        "_id": session_id,
        "session_id": session_id,
        "episode_uuid": distinct_uuids[0] if len(distinct_uuids) == 1 else None,
        "episode_uuids": distinct_uuids,
        "episode_uuid_source": head.get("episode_uuid_source"),
        "verdict": verdict,
        "chunk_count": len(rows),
        "members": [d["_id"] for d in docs],
        "counts": counts,

        "rows": rows,
        "columns": list(COLUMNS),
        "column_checks": list(COLUMN_CHECKS),
        "column_scope": list(COLUMN_SCOPE),
        "matrix": matrix,
        "legend": dict(LEGEND),
        "missing_from": MISSING_FROM,

        "missing_parameters": missing_parameters,
        "missing_count": len(missing_parameters),
        "missing_by_severity": {
            sev: sorted(p for p in missing_parameters
                        if details.get(p, {}).get("severity") == sev)
            for sev in MISSING_SEVERITIES
        },
        "missing_by_row": missing_by_row,
        "details": details,

        "parse_errors": parse_errors,
        "unmapped": unmapped,

        "validated_at": head.get("validated_at"),
        "pipeline_version": head.get("pipeline_version"),
        "spec_version": head.get("spec_version"),
        "matrix_version": MATRIX_VERSION,
        **({"source_uri": head["source_uri"]} if head.get("source_uri") else {}),
    }


# ---------------------------------------------------------------------------
# Slim shape: session id -> its missing parameters, and nothing else.
# ---------------------------------------------------------------------------
def slim_document(doc: Dict[str, Any]) -> Dict[str, Any]:
    """Reduce a full document to the bare key/value pair.

    The matrix is the working view -- which chunk failed which check, and how.
    This is the answer that view produces: one session, one list of parameters
    it is missing. Built from the full document rather than from the report, so
    both shapes always agree on what "missing" means.
    """
    return {"_id": doc["_id"], "missing_parameters": list(doc["missing_parameters"])}


def slim_documents(docs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [slim_document(d) for d in docs]


# ---------------------------------------------------------------------------
# Delivery rollup -- the column totals across a set of session documents.
# ---------------------------------------------------------------------------
def delivery_rollup(docs: List[Dict[str, Any]]) -> Dict[str, Any]:
    """How many sessions each parameter is missing from, worst first."""
    per_param = {name: 0 for name in COLUMNS}
    for doc in docs:
        for name in doc.get("missing_parameters", []):
            if name in per_param:
                per_param[name] += 1
    ranked = sorted(((n, c) for n, c in per_param.items() if c),
                    key=lambda kv: (-kv[1], kv[0]))
    return {
        "sessions": len(docs),
        "columns": list(COLUMNS),
        "missing_sessions_by_parameter": [{"parameter": n, "sessions": c} for n, c in ranked],
    }
