"""Check-record construction, severity->category mapping, and verdict logic.

Every check -- pass or fail -- emits a record with the same shape (PRD §5.2,
§11.6: report checks even when they pass, so a check that did not run is
distinguishable from one that passed). Pure and I/O-free.
"""

from __future__ import annotations

from collections import OrderedDict
from typing import Any, Dict, List, Optional

from . import ruleset as R

# Severity -> the three-way outcome class the PRD insists on distinguishing
# (data failure vs spec/format gap vs hardware ceiling), plus the benign ones.
CATEGORY = {
    R.ERROR: "DATA_FAILURE",
    R.WARN: "WARNING",
    R.INFO: "INFO",
    R.BLOCKED: "SPEC_GAP",
    R.CEILING: "HARDWARE_CEILING",
    R.FORMAT_GAP: "FORMAT_GAP",
    R.PASS: "PASS",
}


def check(cid: str, name: str, severity: str, *,
          code: Optional[str] = None,
          observed: Any = None,
          expected: Any = None,
          source_file: Optional[str] = None,
          spec_ref: Optional[str] = None,
          message: str = "") -> Dict[str, Any]:
    """Build one check record. ``spec_ref`` is emitted on every record (§11.4)."""
    return {
        "id": cid,
        "name": name,
        "severity": severity,
        "category": CATEGORY.get(severity, severity),
        "code": code,
        "observed": observed,
        "expected": expected,
        "source_file": source_file,
        "spec_ref": spec_ref,
        "message": message,
    }


def passed(cid: str, name: str, **kwargs) -> Dict[str, Any]:
    """A PASS record (kwargs mirror :func:`check`, minus severity/code)."""
    kwargs.setdefault("message", "ok")
    return check(cid, name, R.PASS, **kwargs)


# ---------------------------------------------------------------------------
# Aggregation over a package's check records
# ---------------------------------------------------------------------------
def count_severities(records: List[Dict[str, Any]]) -> "OrderedDict[str, int]":
    counts: "OrderedDict[str, int]" = OrderedDict((s, 0) for s in R.SEVERITIES)
    for rec in records:
        counts[rec["severity"]] = counts.get(rec["severity"], 0) + 1
    return counts


def verdict(records: List[Dict[str, Any]]) -> str:
    """Delivery verdict for one package (PRD §5.1).

    Any ERROR rejects. Otherwise any BLOCKED holds for review. FORMAT_GAP,
    CEILING and WARN never block and are not vendor data errors.
    """
    severities = {rec["severity"] for rec in records}
    if severities & R.BLOCKING:
        return R.VERDICT_REJECT
    if severities & R.REVIEW:
        return R.VERDICT_REVIEW
    return R.VERDICT_ACCEPT
