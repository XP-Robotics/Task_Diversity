"""Rendering for the Layer B + C episode validator: JSON, HTML, console.

The JSON is the machine contract (PRD §5.2); the HTML and console views are for
humans. Every check is shown, pass or fail (PRD §11.6).
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List

from . import htmlkit as H
from . import ruleset as R

# Severity display order (most actionable first).
_SEV_ORDER = [R.ERROR, R.BLOCKED, R.FORMAT_GAP, R.CEILING, R.WARN, R.INFO, R.PASS]

_VERDICT_CLS = {R.VERDICT_REJECT: "bad", R.VERDICT_REVIEW: "warn", R.VERDICT_ACCEPT: "good"}


def write_json(result: Dict[str, Any], outdir: str) -> str:
    path = os.path.join(outdir, "schema_report.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(result, fh, indent=2, ensure_ascii=False)
    return path


def _sev_sort_key(rec: Dict[str, Any]):
    order = _SEV_ORDER.index(rec["severity"]) if rec["severity"] in _SEV_ORDER else len(_SEV_ORDER)
    return (order, rec["id"])


def _counts_tiles(counts: Dict[str, int]) -> str:
    cls = {R.ERROR: "bad", R.PASS: "good"}
    tiles = [H.tile(counts.get(sev, 0), sev, cls.get(sev, ""))
             for sev in _SEV_ORDER if counts.get(sev, 0)]
    return H.tiles(tiles) if tiles else ""


def _check_rows(checks: List[Dict[str, Any]]) -> str:
    rows = []
    for c in sorted(checks, key=_sev_sort_key):
        rows.append([
            f"<span class='mono'>{H.esc(c['id'])}</span>",
            H.sev_pill(c["severity"]),
            f"<span class='mono'>{H.esc(c['name'])}</span>",
            H.esc(c.get("code") or "-"),
            H.esc(c.get("message") or ""),
            f"<span class='mono'>{H.esc(c.get('spec_ref') or '-')}</span>",
        ])
    return H.table(["ID", "Severity", "Check", "Code", "Message", "Spec"], rows)


def write_html(result: Dict[str, Any], outdir: str) -> str:
    agg = result["aggregate"]
    parts = ["<h1>Episode Validation Report — Layer B + C</h1>",
             f"<p class='sub'>Spec: {H.esc(R.SPEC_VERSION)} · "
             f"pipeline {H.esc(R.PIPELINE_VERSION)}</p>"]

    parts.append(H.tiles([
        H.tile(agg["packages_total"], "Packages"),
        H.tile(agg["packages_accepted"], "Accepted", "good"),
        H.tile(agg["packages_review"], "Review", "warn" if agg["packages_review"] else ""),
        H.tile(agg["packages_rejected"], "Rejected", "bad" if agg["packages_rejected"] else ""),
    ]))

    if agg["severity_totals"]:
        parts.append("<h2>Findings by severity (delivery)</h2>")
        parts.append(_counts_tiles(agg["severity_totals"]))

    if agg["top_findings"]:
        parts.append("<h2>Top finding codes</h2>")
        parts.append(H.table(["Code", "Count"],
                             [[f"<span class='mono'>{H.esc(f['code'])}</span>", str(f["count"])]
                              for f in agg["top_findings"]]))

    for rep in result["packages"]:
        vcls = _VERDICT_CLS.get(rep["verdict"], "")
        parts.append(f"<h2>{H.esc(rep['package_id'])} "
                     f"<span class='{vcls}'>[{H.esc(rep['verdict'])}]</span></h2>")
        parts.append(f"<p class='sub'>episode_uuid: "
                     f"<span class='mono'>{H.esc(rep['episode_uuid'] or '—')}</span></p>")
        parts.append(_counts_tiles(rep["counts"]))
        parts.append(_check_rows(rep["checks"]))

    path = os.path.join(outdir, "schema_report.html")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(H.page("Episode Validation Report", "".join(parts)))
    return path


def console_summary(result: Dict[str, Any], quiet: bool = False) -> None:
    agg = result["aggregate"]
    print("=== Episode validation (Layer B + C) ===")
    print(f"Packages: {agg['packages_total']}  "
          f"accepted: {agg['packages_accepted']}  "
          f"review: {agg['packages_review']}  "
          f"rejected: {agg['packages_rejected']}")
    sev = agg["severity_totals"]
    if sev:
        print("Findings: " + "  ".join(f"{s}={sev[s]}" for s in _SEV_ORDER if sev.get(s)))
    if quiet:
        return
    for rep in result["packages"]:
        print(f"\n{rep['package_id']}  [{rep['verdict']}]  "
              f"uuid={rep['episode_uuid'] or '—'}")
        for c in sorted(rep["checks"], key=_sev_sort_key):
            if c["severity"] == R.PASS:
                continue
            print(f"  {c['severity']:<10} {c['id']:<7} {c['name']}: {c['message']}")
