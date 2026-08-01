"""Rendering for Mode B (diversity): JSON, HTML, console."""

from __future__ import annotations

import json
import os
from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple

from . import config as C
from . import htmlkit as H

_CARD_CLASS = {"PASS": "pass", "FAIL": "fail", "WARN": "warn",
               "INFO": "info", "NOT_COMPUTABLE": "nc"}

# How many slices a donut shows. Past this the categorical palette runs out, so
# the share chart switches to bars rather than folding the tail out of sight --
# no category is ever collapsed into "Other".
_SLICE_CAP = 5


def write_json(result: Dict[str, Any], outdir: str) -> str:
    path = os.path.join(outdir, "diversity_report.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(result, fh, indent=2, ensure_ascii=False)
    return path


def _offender_table(offenders: List[Dict[str, Any]]) -> str:
    if not offenders:
        return ""
    headers = list(offenders[0].keys())
    rows = [[H.esc(o.get(h)) for h in headers] for o in offenders]
    return "<div style='margin-top:8px'>" + H.table(headers, rows) + "</div>"


def _distribution_chart(check: Dict[str, Any]) -> str:
    """The L1 category split, drawn against its own single-category cap."""
    dist = (check.get("extra") or {}).get("distribution")
    if not dist:
        return ""
    items = [(d["label"], d["pct"], f"{d['label']} · {d['hours']:.2f}h · {d['pct']:.2f}%")
             for d in dist]
    tbl = H.table(["L1 category", "Hours", "Share"],
                  [[H.esc(d["label"]), f"{d['hours']:.2f}", f"{d['pct']:.2f}%"] for d in dist])
    bar = H.hbar(items, limit=C.ENV_L1_SINGLE_MAX_PCT,
                 limit_label=f"cap {C.ENV_L1_SINGLE_MAX_PCT:g}%")
    return ("<div style='margin-top:12px'>" + bar +
            f"<details class='tv'><summary>Table view</summary>"
            f"<div class='scrollx'>{tbl}</div></details></div>")


# ---------------------------------------------------------------------------
# Delivery composition -- the shape of the delivery, before the pass/fail calls.
# Every figure here is derived from the same per-episode rows the checks use.
# ---------------------------------------------------------------------------

def _hours_by(rows: List[Dict[str, Any]], field: str) -> List[Tuple[str, float]]:
    acc: Dict[str, float] = defaultdict(float)
    for r in rows:
        key = r.get(field)
        if key:
            acc[str(key)] += r["hours"]
    return sorted(acc.items(), key=lambda kv: -kv[1])


def _share_chart(title: str, caption: str, pairs: List[Tuple[str, float]],
                 total: float, noun: str) -> str:
    """Share of hours per category, with every category plotted.

    A donut while the palette can give each slice its own identity; beyond that
    a bar per category, which stays readable at any count and never hides the
    tail. Sorted by hours, so the long tail reads as a shape rather than a lump.
    """
    if not pairs:
        return ""
    tbl = H.table([noun, "Hours", "Share"],
                  [[H.esc(lbl), f"{h:.3f}", f"{_pct(h, total):.2f}%"] for lbl, h in pairs])
    plural = noun + ("s" if len(pairs) != 1 else "")
    slices = [(lbl, h, f"{lbl} · {h:.2f}h · {_pct(h, total):.1f}%") for lbl, h in pairs]
    cols = H._colors(len(pairs))
    body = H.donut(slices, center_value=str(len(pairs)), center_label=plural)
    body += H.legend([(c, H._trunc(lbl, 34), f"{_pct(h, total):.1f}%")
                      for c, (lbl, h) in zip(cols, pairs)])
    if len(pairs) > _SLICE_CAP + 1:
        # A hundred slices carry a share but not a ranking, so the same data is
        # also drawn as bars -- the tail is where the concentration story is.
        bars = [(lbl, _pct(h, total), tip) for (lbl, _, tip), (_, h) in zip(slices, pairs)]
        body += H.hbar(bars, label_w=330, label_chars=46)
        caption = f"{caption} All {len(pairs)} {plural.lower()}, no category folded away."
    return H.chart(title, caption, body, tbl, wide=len(pairs) > _SLICE_CAP + 1)


def _pct(v: float, total: float) -> float:
    return 100.0 * v / total if total else 0.0


def _env_label(code: str) -> str:
    """Decode an environment id, or hand it back untouched when unknown."""
    name = C.ENVIRONMENT_NAMES.get(code)
    return f"{name} ({code})" if name else code


def _environment_reference(rows: List[Dict[str, Any]]) -> str:
    """The environment vocabulary, and how much of it this delivery reaches.

    Two things a bare id list cannot say: which categories were never captured
    (the L1 distinct-count check is a direct function of this), and which ids
    the delivery uses that are not in the vocabulary at all.
    """
    if not C.ENVIRONMENT_NAMES:
        return ""
    used: Dict[str, float] = defaultdict(float)
    for r in rows:
        if r.get("environment_id"):
            used[str(r["environment_id"])] += r["hours"]
    total = sum(r["hours"] for r in rows) or 0.0

    body = []
    for code, name in C.ENVIRONMENT_NAMES.items():
        hours = used.get(code)
        body.append([H.esc(name), f"<span class='mono'>{H.esc(code)}</span>",
                     f"{hours:.2f} h" if hours else "<span class='sub' "
                     "style='margin:0'>not captured</span>",
                     f"{_pct(hours, total):.1f}%" if hours else "—"])
    unknown = sorted(c for c in used if c not in C.ENVIRONMENT_NAMES)
    for code in unknown:
        body.append(["<span class='warn' style='background:none;padding:0'>"
                     "not in the reference list</span>",
                     f"<span class='mono'>{H.esc(code)}</span>",
                     f"{used[code]:.2f} h", f"{_pct(used[code], total):.1f}%"])

    covered = sum(1 for c in C.ENVIRONMENT_NAMES if c in used)
    note = (f"{covered} of {len(C.ENVIRONMENT_NAMES)} reference environments appear in "
            f"this delivery. The L1 category check wants at least "
            f"{C.ENV_L1_MIN_DISTINCT:g} distinct categories, so the rows marked "
            f"<em>not captured</em> are the gap.")
    if unknown:
        note += (f" {len(unknown)} id(s) in the delivery are not in the reference list "
                 f"at all: they decode to nothing and are listed last.")
    return H.chart("Environment reference", note,
                   H.table(["Environment", "Environment ID", "In this delivery", "Share"],
                           body), wide=True)


def _composition(result: Dict[str, Any]) -> str:
    rows = result["rows"]
    total = sum(r["hours"] for r in rows) or 0.0
    cards: List[str] = []

    sites = [(_env_label(code), h) for code, h in _hours_by(rows, "environment_id")]
    if sites:
        cards.append(_share_chart(
            "Hours by environment", "Share of delivery hours per collection site.",
            sites, total, "Site"))

    tasks = _hours_by(rows, "task_id")
    if tasks:
        cards.append(_share_chart(
            "Hours by task", "Share of delivery hours per task id.",
            tasks, total, "Task"))

    # Difficulty: an ordered scale, so an ordinal ramp and a stacked ratio bar
    # measured against the hard floor -- not a pie of two live slices.
    diff = {k: v for k, v in _hours_by(rows, "difficulty")}
    known = sum(diff.values())
    if known > 0:
        order = [("hard", H.ORDINAL3[2]), ("medium", H.ORDINAL3[1]), ("easy", H.ORDINAL3[0])]
        segs = [(lbl, _pct(diff.get(lbl, 0.0), known), col,
                 f"{lbl} · {diff.get(lbl, 0.0):.2f}h · {_pct(diff.get(lbl, 0.0), known):.1f}%")
                for lbl, col in order]
        body = H.ratio_bar(segs, limit=C.DIFF_HARD_MIN_PCT,
                           limit_label=f"hard floor {C.DIFF_HARD_MIN_PCT:g}%")
        body += H.legend([(col, lbl, f"{pct:.1f}%") for lbl, pct, col, _ in segs])
        tbl = H.table(["Difficulty", "Hours", "Share of known"],
                      [[lbl, f"{diff.get(lbl, 0.0):.3f}", f"{pct:.2f}%"]
                       for lbl, pct, _, _ in segs])
        cards.append(H.chart(
            "Difficulty mix", f"Share of the {known:.2f}h that carry a difficulty "
            f"label. The rule is a floor on <em>hard</em>, so the bar is ordered "
            f"hard&rarr;easy and the hard segment has to reach the rule.", body, tbl))

    work = {k: v for k, v in _hours_by(rows, "worker_type")}
    wtotal = sum(work.values())
    if wtotal > 0:
        order = [("real", H.CATEGORICAL[0]), ("contractor", H.CATEGORICAL[1])]
        segs = [(lbl, _pct(work.get(lbl, 0.0), wtotal), col,
                 f"{lbl} · {work.get(lbl, 0.0):.2f}h · {_pct(work.get(lbl, 0.0), wtotal):.1f}%")
                for lbl, col in order]
        body = H.ratio_bar(segs, limit=C.WORKFORCE_REAL_MIN_PCT,
                           limit_label=f"real ≥ {C.WORKFORCE_REAL_MIN_PCT:g}%")
        body += H.legend([(col, lbl, f"{pct:.1f}%") for lbl, pct, col, _ in segs])
        tbl = H.table(["Worker type", "Hours", "Share"],
                      [[lbl, f"{work.get(lbl, 0.0):.3f}", f"{pct:.2f}%"]
                       for lbl, pct, _, _ in segs])
        cards.append(H.chart(
            "Workforce mix", "Real employees vs contractors by hours. The rule is a "
            "floor on real, so the real segment has to reach the rule.", body, tbl))

    ops = _hours_by(rows, "operator_id")
    if ops:
        items = [(lbl, _pct(h, total), f"{lbl} · {h:.2f}h · {_pct(h, total):.1f}%")
                 for lbl, h in ops]
        tbl = H.table(["Operator", "Hours", "Share"],
                      [[f"<span class='mono'>{H.esc(lbl)}</span>", f"{h:.3f}",
                        f"{_pct(h, total):.2f}%"] for lbl, h in ops])
        cards.append(H.chart(
            "Operator concentration",
            f"Share of delivery hours per operator, {len(ops)} in total.",
            H.hbar(items), tbl, wide=True))

    mins = [r["hours"] * 60 for r in rows if r["hours"] > 0]
    if mins:
        tbl = H.table(["Statistic", "Minutes"], [
            ["Episodes with a duration", f"{len(mins)}"],
            ["Shortest", f"{min(mins):.1f}"],
            ["Longest", f"{max(mins):.1f}"],
            ["Mean", f"{sum(mins) / len(mins):.1f}"],
        ])
        cards.append(H.chart(
            "Episode length distribution",
            "Episode count per length band, in minutes.",
            H.histogram(mins, unit=" min"), tbl, wide=True))

    cards.append(_environment_reference(rows))

    cards = [c for c in cards if c]
    if not cards:
        return ""
    return "<h2>Delivery composition</h2>" + H.charts(cards)


def write_html(result: Dict[str, Any], outdir: str) -> str:
    summ = result["summary"]
    sc = summ["status_counts"]
    parts = ["<h1>Diversity Report</h1>",
             f"<p class='sub'>Whole delivery &middot; {summ['episodes']} episodes &middot; "
             f"{summ['total_hours']:.2f} total hours &middot; factory cap: "
             f"{'dynamic (2h)' if summ['factory_dynamic'] else 'repetitive (1h)'}</p>"]

    parts.append(H.tiles([
        H.tile(summ["episodes"], "Episodes"),
        H.tile(f"{summ['total_hours']:.1f}", "Total hours"),
        H.tile(sc.get("PASS", 0), "Pass", "good"),
        H.tile(sc.get("FAIL", 0), "Fail", "bad" if sc.get("FAIL") else ""),
        H.tile(sc.get("WARN", 0), "Warn"),
        H.tile(sc.get("NOT_COMPUTABLE", 0), "Not computable"),
    ]))

    parts.append(_composition(result))

    parts.append("<h2>Checks</h2>")
    for c in result["checks"]:
        cls = _CARD_CLASS.get(c["status"], "nc")
        parts.append(
            f"<div class='card {cls}'><h3>{H.esc(c['name'])} &nbsp; {H.status_pill(c['status'])}</h3>"
            f"<div class='detail'>{H.esc(c['detail'])}</div>"
            + _distribution_chart(c) + _offender_table(c["offenders"]) + "</div>"
        )

    # Per-episode duration provenance
    parts.append("<h2>Per-episode durations</h2>")
    rows = []
    for r in result["rows"]:
        warn = "; ".join(r["warnings"])
        rows.append([
            f"<span class='mono'>{H.esc(r['stem'])}</span>",
            f"{r['hours']:.3f}",
            H.esc(r["duration_source"]),
            H.esc(r["difficulty"]),
            f"<span class='warn'>{H.esc(warn)}</span>" if warn else "",
        ])
    parts.append(H.table(["Episode", "Hours", "Source", "Difficulty", "Notes"], rows))

    path = os.path.join(outdir, "diversity_report.html")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(H.page("Diversity Report", "".join(parts)))
    return path


def console_summary(result: Dict[str, Any], quiet: bool = False) -> None:
    summ = result["summary"]
    print("=== Diversity ===")
    print(f"Episodes: {summ['episodes']}  total hours: {summ['total_hours']:.2f}")
    for c in result["checks"]:
        print(f"  [{c['status']:<14}] {c['name']}: {c['detail']}")
        if not quiet and c["offenders"]:
            for o in c["offenders"][:5]:
                print(f"       - {o}")
            if len(c["offenders"]) > 5:
                print(f"       ... {len(c['offenders']) - 5} more")
