"""Tiny self-contained HTML helpers shared by both report renderers.

No external assets: every page carries its own inline CSS, and every chart is
inline SVG -- no scripts, no chart library, nothing fetched at open time. A
report stays a single file you can mail to someone.
"""

from __future__ import annotations

import html
import math
from typing import Any, Iterable, List, Optional, Sequence, Tuple

# Shared inline stylesheet (kept intentionally small and dependency-free).
CSS = """
:root { color-scheme: light; }
* { box-sizing: border-box; }
body { font-family: -apple-system, Segoe UI, Roboto, Helvetica, Arial, sans-serif;
       margin: 0; padding: 24px; color: #1a1f29; background: #f4f6fa; }
h1 { font-size: 22px; margin: 0 0 4px; }
h2 { font-size: 16px; margin: 28px 0 10px; }
.sub { color: #5a6472; margin: 0 0 20px; font-size: 13px; }
.tiles { display: flex; flex-wrap: wrap; gap: 12px; margin-bottom: 8px; }
.tile { background: #fff; border: 1px solid #e2e7ef; border-radius: 10px;
        padding: 14px 18px; min-width: 120px; box-shadow: 0 1px 2px rgba(0,0,0,.03); }
.tile .n { font-size: 24px; font-weight: 700; }
.tile .l { font-size: 12px; color: #6b7280; text-transform: uppercase; letter-spacing: .04em; }
table { border-collapse: collapse; width: 100%; background: #fff; border-radius: 10px;
        overflow: hidden; box-shadow: 0 1px 2px rgba(0,0,0,.03); font-size: 13px; }
th, td { text-align: left; padding: 8px 12px; border-bottom: 1px solid #eef1f6; }
th { background: #eef2f8; font-weight: 600; }
tr:last-child td { border-bottom: none; }
.mono { font-family: ui-monospace, SFMono-Regular, Consolas, monospace; }
.good { color: #0a7d34; }
.bad { color: #b4231c; }
.pill { display: inline-block; padding: 2px 9px; border-radius: 999px; font-size: 12px;
        font-weight: 600; }
.pass { background: #dff3e4; color: #0a7d34; }
.fail { background: #fbe0de; color: #b4231c; }
.warn { background: #fdf1d6; color: #8a5a00; }
.info { background: #e2ecfb; color: #23508a; }
.nc   { background: #e7eaef; color: #55606f; }
.card { background: #fff; border: 1px solid #e2e7ef; border-left-width: 5px;
        border-radius: 10px; padding: 14px 18px; margin-bottom: 12px; }
.card.pass { border-left-color: #23a55a; } .card.fail { border-left-color: #d9382e; }
.card.warn { border-left-color: #e0a300; } .card.info { border-left-color: #3f74c9; }
.card.nc   { border-left-color: #9aa4b2; }
.card h3 { margin: 0 0 4px; font-size: 15px; }
.card .detail { color: #47505f; font-size: 13px; margin-bottom: 8px; }
.bar { height: 8px; background: #eceff4; border-radius: 4px; overflow: hidden; min-width: 80px; }
.bar > span { display: block; height: 100%; }
footer { margin-top: 28px; color: #98a2b3; font-size: 12px; }

/* -- charts ------------------------------------------------------------- */
.charts { display: grid; grid-template-columns: repeat(auto-fit, minmax(420px, 1fr));
          gap: 12px; align-items: start; }
.chart { background: #fff; border: 1px solid #e2e7ef; border-radius: 10px;
         padding: 14px 18px 16px; box-shadow: 0 1px 2px rgba(0,0,0,.03); min-width: 0; }
.chart.wide { grid-column: 1 / -1; }
.chart h3 { margin: 0 0 2px; font-size: 14px; }
.chart .cap { color: #6b7280; font-size: 12px; margin: 0 0 12px; line-height: 1.45; }
.chart svg { width: 100%; height: auto; display: block; overflow: visible; }
/* Dim the field on hover so the mark under the cursor reads as the focus.    */
.chart svg:hover .mark { opacity: .5; }
.chart svg .mark:hover { opacity: 1; }
.chart svg .mark { transition: opacity .1s linear; }
.donutwrap { max-width: 210px; margin: 0 auto; }
.lg { display: flex; flex-wrap: wrap; gap: 7px 16px; margin-top: 12px;
      font-size: 12px; color: #47505f; }
.lg span { white-space: nowrap; }
.lg i { width: 10px; height: 10px; border-radius: 3px; display: inline-block;
        margin-right: 6px; vertical-align: -1px; }
.lg b { font-weight: 600; font-variant-numeric: tabular-nums; }
details.tv { margin-top: 12px; }
details.tv > summary { cursor: pointer; font-size: 12px; color: #6b7280;
                       list-style: none; user-select: none; }
details.tv > summary::before { content: '\\25b8 '; }
details.tv[open] > summary::before { content: '\\25be '; }
details.tv[open] > summary { margin-bottom: 8px; }
details.tv table { font-size: 12px; box-shadow: none; border: 1px solid #eef1f6; }
.scrollx { overflow-x: auto; }
"""

# --- chart palette ---------------------------------------------------------
# Validated as a set against the #fff card surface (lightness band, chroma
# floor, adjacent CVD separation, normal-vision floor). Three slots sit below
# 3:1 contrast, so every chart here ships direct labels *and* a table view --
# colour never carries a value on its own.
SERIES = "#2a78d6"   # single hue: every bar in a one-series magnitude chart
CATEGORICAL = ("#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300")
# Difficulty is an *ordered* scale, so it gets a one-hue ordinal ramp (monotone
# lightness) rather than categorical hues -- the order is part of the meaning.
ORDINAL3 = ("#86b6ef", "#2a78d6", "#104281")
TAIL = "#9aa4b2"     # the folded "Other" slice -- always last, always grey
LIMIT = "#d03b3b"    # status:critical, reserved for threshold rules
GRID = "#e1e0d9"
AXIS_C = "#c3c2b7"
MUTED = "#6b7280"
SURFACE = "#ffffff"
GAP = 2.0            # the surface gap that separates touching marks

_SVG_OPEN = ("<svg viewBox='0 0 {w} {h}' xmlns='http://www.w3.org/2000/svg' "
             "font-family='inherit' role='img'>")


def esc(v: Any) -> str:
    return html.escape("" if v is None else str(v))


def page(title: str, body: str) -> str:
    return (
        "<!DOCTYPE html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        f"<title>{esc(title)}</title><style>{CSS}</style></head><body>{body}"
        "<footer>Generated by the egocentric-episode QA tool.</footer>"
        "</body></html>"
    )


def tile(number: Any, label: str, cls: str = "") -> str:
    ncls = f" {cls}" if cls else ""
    return f"<div class='tile'><div class='n{ncls}'>{esc(number)}</div><div class='l'>{esc(label)}</div></div>"


def tiles(items: Iterable[str]) -> str:
    return "<div class='tiles'>" + "".join(items) + "</div>"


def status_pill(status: str) -> str:
    cls = {"PASS": "pass", "FAIL": "fail", "WARN": "warn",
           "INFO": "info", "NOT_COMPUTABLE": "nc"}.get(status, "nc")
    return f"<span class='pill {cls}'>{esc(status)}</span>"


def table(headers: List[str], rows: List[List[str]]) -> str:
    if not rows:
        return "<p class='sub'>none</p>"
    head = "".join(f"<th>{esc(h)}</th>" for h in headers)
    body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


# ---------------------------------------------------------------------------
# Charts
#
# Every chart below is inline SVG built from the same handful of rules: one hue
# for a one-series magnitude chart (never a value-ramp across nominal
# categories), the fixed categorical order for part-to-whole with the tail
# folded into "Other" rather than a ninth colour, 4px rounded data-ends, a 2px
# surface gap between touching marks, hairline solid gridlines, selective
# direct labels, and a <title> on every mark so hover reads the exact value.
#
# Each chart is paired with a collapsed table view: the palette has slots below
# 3:1 against the card surface, so no value is ever reachable by colour alone.
# ---------------------------------------------------------------------------

Item = Tuple[str, float, str]  # (label, value, tooltip)


def _fmt(v: float, dec: Optional[int] = None) -> str:
    if dec is None:
        dec = 0 if abs(v) >= 10 else (1 if abs(v) >= 1 else 2)
    return f"{v:,.{dec}f}"


def _trunc(s: str, n: int) -> str:
    return s if len(s) <= n else s[: n - 1] + "…"


def _ink_on(fill: str) -> str:
    """Ink or white for a label set inside a coloured fill, by luminance."""
    r, g, b = (int(fill[i:i + 2], 16) / 255 for i in (1, 3, 5))
    lin = [c / 12.92 if c <= .04045 else ((c + .055) / 1.055) ** 2.4 for c in (r, g, b)]
    return "#0b0b0b" if .2126 * lin[0] + .7152 * lin[1] + .0722 * lin[2] > .45 else "#ffffff"


def _rrect(x: float, y: float, w: float, h: float, rl: float = 0, rr: float = 0) -> str:
    """Rect path with independently rounded left and right ends (r=0 -> square)."""
    rl, rr = min(rl, w / 2, h / 2), min(rr, w / 2, h / 2)
    return (f"M{x + rl:.2f},{y:.2f} H{x + w - rr:.2f} A{rr:.2f},{rr:.2f} 0 0 1 "
            f"{x + w:.2f},{y + rr:.2f} V{y + h - rr:.2f} A{rr:.2f},{rr:.2f} 0 0 1 "
            f"{x + w - rr:.2f},{y + h:.2f} H{x + rl:.2f} A{rl:.2f},{rl:.2f} 0 0 1 "
            f"{x:.2f},{y + h - rl:.2f} V{y + rl:.2f} A{rl:.2f},{rl:.2f} 0 0 1 "
            f"{x + rl:.2f},{y:.2f} Z")


def _col(x: float, y: float, w: float, h: float, r: float = 4) -> str:
    """Column path: 4px rounded cap at the data-end, square on the baseline."""
    r = min(r, w / 2, h)
    return (f"M{x:.2f},{y + h:.2f} V{y + r:.2f} A{r:.2f},{r:.2f} 0 0 1 {x + r:.2f},{y:.2f} "
            f"H{x + w - r:.2f} A{r:.2f},{r:.2f} 0 0 1 {x + w:.2f},{y + r:.2f} "
            f"V{y + h:.2f} Z")


def _nice_axis(vmax: float, target: int = 4) -> Tuple[float, int]:
    """Round the axis up to a clean top and a whole number of clean steps.

    Ticks a reader can do arithmetic with (0/20/40/60) rather than whatever
    1.06x the largest bar happened to be (0/7.9/15.8/23.8).
    """
    if vmax <= 0:
        return 1.0, target
    raw = vmax / target
    mag = 10.0 ** math.floor(math.log10(raw))
    step = next((m * mag for m in (1, 2, 2.5, 5, 10) if raw <= m * mag), 10 * mag)
    n = max(int(math.ceil(vmax / step)), 1)
    return step * n, n


def _colors(n: int) -> List[str]:
    """Categorical slots in fixed order, extended by generated hues past the last.

    The fixed palette is validated, so it is used verbatim whenever it fits. Past
    it, hues are walked by the golden angle (never adjacent twice) with lightness
    and chroma cycled so neighbouring slices stay separable. Colour is decorative
    at these counts -- the tooltip, the direct labels and the table view carry the
    values -- but every category still gets its own fill instead of a grey lump.
    """
    if n <= len(CATEGORICAL):
        return list(CATEGORICAL[:n])
    out = []
    for i in range(n):
        hue = (i * 137.508) % 360           # golden angle: maximal hue spacing
        light = (46, 62, 54)[i % 3]
        sat = (68, 52, 80)[i % 3]
        out.append(f"hsl({hue:.1f} {sat}% {light}%)")
    return out


def legend(pairs: Sequence[Tuple[str, str, str]]) -> str:
    """pairs: (colour, label, value). The dependable identity channel."""
    return "<div class='lg'>" + "".join(
        f"<span><i style='background:{c}'></i>{esc(lbl)} <b>{esc(val)}</b></span>"
        for c, lbl, val in pairs) + "</div>"


def chart(title: str, caption: str, body: str, table_html: str = "",
          wide: bool = False) -> str:
    tv = (f"<details class='tv'><summary>Table view</summary>"
          f"<div class='scrollx'>{table_html}</div></details>") if table_html else ""
    return (f"<div class='chart{' wide' if wide else ''}'><h3>{esc(title)}</h3>"
            f"<p class='cap'>{caption}</p>{body}{tv}</div>")


def charts(cards: Iterable[str]) -> str:
    return "<div class='charts'>" + "".join(cards) + "</div>"


def hbar(items: Sequence[Item], *, suffix: str = "%", limit: Optional[float] = None,
         limit_label: str = "", width: int = 720, label_w: int = 190,
         bar_h: int = 18, gap: int = 10, label_chars: int = 24) -> str:
    """Horizontal bars, one hue, value direct-labelled at each tip.

    ``limit`` draws a single threshold rule so a reader sees the cap and the
    bars on the same scale instead of having to hold the number in their head.
    """
    if not items:
        return "<p class='sub'>none</p>"
    val_w, x0 = 58, label_w
    plot_w = width - label_w - val_w
    vmax, ticks = _nice_axis(max([v for _, v, _ in items] + ([limit] if limit else [0])))
    # One decimal count per number role -- mixing "0.00%" with "16%" down one
    # axis reads as two different scales. Ticks follow the step; the direct
    # labels keep a decimal so near-equal bars stay distinguishable.
    step = vmax / ticks
    tick_dec = 0 if abs(step - round(step)) < 1e-9 else 1
    top = 24 if limit is not None else 8
    axis_y = top + len(items) * bar_h + (len(items) - 1) * gap + 10
    height = axis_y + 22

    def sx(v: float) -> float:
        return x0 + (v / vmax) * plot_w

    p = [_SVG_OPEN.format(w=width, h=height)]
    for i in range(ticks + 1):
        f = i / ticks
        gx = x0 + f * plot_w
        p.append(f"<line x1='{gx:.1f}' y1='{top}' x2='{gx:.1f}' y2='{axis_y}' "
                 f"stroke='{GRID}' stroke-width='1'/>")
        p.append(f"<text x='{gx:.1f}' y='{axis_y + 15}' fill='{MUTED}' font-size='11' "
                 f"text-anchor='middle' style='font-variant-numeric:tabular-nums'>"
                 f"{_fmt(f * vmax, tick_dec)}{esc(suffix)}</text>")

    for i, (lbl, val, tip) in enumerate(items):
        y = top + i * (bar_h + gap)
        w = max(sx(val) - x0, 0.0)
        p.append(f"<text x='{x0 - 10}' y='{y + bar_h / 2 + 4:.1f}' fill='#47505f' "
                 f"font-size='12' text-anchor='end'>{esc(_trunc(lbl, label_chars))}"
                 f"<title>{esc(lbl)}</title></text>")
        if w > .5:
            p.append(f"<path class='mark' d='{_rrect(x0, y, w, bar_h, 0, 4)}' "
                     f"fill='{SERIES}'><title>{esc(tip)}</title></path>")
        p.append(f"<text x='{x0 + w + 8:.1f}' y='{y + bar_h / 2 + 4:.1f}' fill='#47505f' "
                 f"font-size='12' style='font-variant-numeric:tabular-nums'>"
                 f"{_fmt(val, 1)}{esc(suffix)}</text>")

    p.append(f"<line x1='{x0}' y1='{top}' x2='{x0}' y2='{axis_y}' "
             f"stroke='{AXIS_C}' stroke-width='1'/>")
    if limit is not None:
        lx = sx(limit)
        p.append(f"<line x1='{lx:.1f}' y1='{top - 6}' x2='{lx:.1f}' y2='{axis_y}' "
                 f"stroke='{LIMIT}' stroke-width='1'/>")
        p.append(f"<text x='{lx:.1f}' y='{top - 11}' fill='{LIMIT}' font-size='11' "
                 f"font-weight='600' text-anchor='middle'>{esc(limit_label)}</text>")
    p.append("</svg>")
    return "".join(p)


def donut(items: Sequence[Item], *, center_value: str = "", center_label: str = "",
          size: int = 200) -> str:
    """Part-to-whole at a glance, one slice per category -- nothing is folded.

    Past the validated palette the ring grows and the separating gap shrinks to a
    fraction of each slice, so a hundred thin slices still render as a hundred
    slices instead of vanishing into the padding.
    """
    total = sum(v for _, v, _ in items)
    if total <= 0:
        return "<p class='sub'>no hours to plot</p>"
    if len(items) > len(CATEGORICAL):
        size = max(size, min(460, 200 + 2 * len(items)))
    cx = cy = size / 2
    ro, ri = size / 2 - 4, size / 2 - 32
    cols = _colors(len(items))
    p = [_SVG_OPEN.format(w=size, h=size)]

    if len(items) == 1:
        p.append(f"<circle class='mark' cx='{cx}' cy='{cy}' r='{(ro + ri) / 2:.2f}' "
                 f"fill='none' stroke='{cols[0]}' stroke-width='{ro - ri:.2f}'>"
                 f"<title>{esc(items[0][2])}</title></circle>")
    else:
        gap_a = GAP / ((ro + ri) / 2) / 2   # the 2px surface gap, expressed as an angle
        a = -math.pi / 2
        for (lbl, val, tip), col in zip(items, cols):
            span = 2 * math.pi * (val / total)
            # never let the separator eat the slice it is separating
            pad = min(gap_a, span * 0.2)
            a0, a1 = a + pad, a + span - pad
            a += span
            if a1 <= a0:
                continue
            large = 1 if (a1 - a0) > math.pi else 0
            pts = [(cx + r * math.cos(ang), cy + r * math.sin(ang))
                   for r, ang in ((ro, a0), (ro, a1), (ri, a1), (ri, a0))]
            d = (f"M{pts[0][0]:.2f},{pts[0][1]:.2f} A{ro:.2f},{ro:.2f} 0 {large} 1 "
                 f"{pts[1][0]:.2f},{pts[1][1]:.2f} L{pts[2][0]:.2f},{pts[2][1]:.2f} "
                 f"A{ri:.2f},{ri:.2f} 0 {large} 0 {pts[3][0]:.2f},{pts[3][1]:.2f} Z")
            p.append(f"<path class='mark' d='{d}' fill='{col}'>"
                     f"<title>{esc(tip)}</title></path>")

    if center_value:
        p.append(f"<text x='{cx}' y='{cy + 2}' fill='#1a1f29' font-size='24' "
                 f"font-weight='700' text-anchor='middle'>{esc(center_value)}</text>")
        p.append(f"<text x='{cx}' y='{cy + 20}' fill='{MUTED}' font-size='11' "
                 f"text-anchor='middle' letter-spacing='.04em'>"
                 f"{esc(center_label.upper())}</text>")
    p.append("</svg>")
    return f"<div class='donutwrap'>{''.join(p)}</div>"


def ratio_bar(segments: Sequence[Tuple[str, float, str, str]], *,
              limit: Optional[float] = None, limit_label: str = "",
              width: int = 560, bar_h: int = 38) -> str:
    """A 100% stacked bar: one ratio measured against one limit.

    segments: (label, pct, colour, tooltip). Preferred over a two-slice pie --
    the limit rule lands on the same scale as the data.
    """
    segments = [s for s in segments if s[1] > 0]
    if not segments:
        return "<p class='sub'>none</p>"
    top, height = (26 if limit is not None else 6), (26 if limit is not None else 6) + bar_h + 24
    p = [_SVG_OPEN.format(w=width, h=height)]
    x = 0.0
    for i, (lbl, pct, col, tip) in enumerate(segments):
        w = width * pct / 100
        first, last = i == 0, i == len(segments) - 1
        x1 = x if first else x + GAP / 2
        w1 = max(w - (0 if first else GAP / 2) - (0 if last else GAP / 2), 0)
        if w1 <= .5:
            x += w
            continue
        p.append(f"<path class='mark' d='{_rrect(x1, top, w1, bar_h, 4 if first else 0, 4 if last else 0)}' "
                 f"fill='{col}'><title>{esc(tip)}</title></path>")
        # Only label inside the segment when the text actually fits with padding.
        txt = f"{_fmt(pct, 1)}%"
        if w1 >= len(txt) * 7.2 + 16:
            p.append(f"<text x='{x1 + w1 / 2:.1f}' y='{top + bar_h / 2 + 5:.1f}' "
                     f"fill='{_ink_on(col)}' font-size='13' font-weight='600' "
                     f"text-anchor='middle'>{esc(txt)}</text>")
        x += w
    if limit is not None:
        lx = width * limit / 100
        p.append(f"<line x1='{lx:.1f}' y1='{top - 8}' x2='{lx:.1f}' y2='{top + bar_h + 8}' "
                 f"stroke='{LIMIT}' stroke-width='1'/>")
        anchor = "end" if limit > 70 else ("start" if limit < 30 else "middle")
        p.append(f"<text x='{lx:.1f}' y='{top - 13}' fill='{LIMIT}' font-size='11' "
                 f"font-weight='600' text-anchor='{anchor}'>{esc(limit_label)}</text>")
    p.append("</svg>")
    return "".join(p)


def histogram(values: Sequence[float], *, bins: int = 12, unit: str = "",
              width: int = 720, height: int = 170) -> str:
    """Column chart of a value distribution -- one hue, shape is the message."""
    vals = [v for v in values if v is not None]
    if not vals:
        return "<p class='sub'>none</p>"
    lo, hi = min(vals), max(vals)
    if hi <= lo:
        hi = lo + 1.0
    step = (hi - lo) / bins
    counts = [0] * bins
    for v in vals:
        counts[min(int((v - lo) / step), bins - 1)] += 1
    cmax = max(counts) or 1
    left, top, base = 30, 14, height - 30
    plot_w = width - left - 8
    slot = plot_w / bins
    bw = min(slot - GAP, 24)

    p = [_SVG_OPEN.format(w=width, h=height)]
    for f in (0, .5, 1):
        gy = base - f * (base - top)
        p.append(f"<line x1='{left}' y1='{gy:.1f}' x2='{width - 8}' y2='{gy:.1f}' "
                 f"stroke='{GRID}' stroke-width='1'/>")
        p.append(f"<text x='{left - 7}' y='{gy + 4:.1f}' fill='{MUTED}' font-size='11' "
                 f"text-anchor='end' style='font-variant-numeric:tabular-nums'>"
                 f"{_fmt(f * cmax, 0)}</text>")  # counts are whole numbers
    for i, c in enumerate(counts):
        h = (c / cmax) * (base - top)
        x = left + i * slot + (slot - bw) / 2
        tip = f"{c} episode(s) · {_fmt(lo + i * step, 1)}–{_fmt(lo + (i + 1) * step, 1)}{unit}"
        if h > .5:
            p.append(f"<path class='mark' d='{_col(x, base - h, bw, h)}' "
                     f"fill='{SERIES}'><title>{esc(tip)}</title></path>")
    p.append(f"<line x1='{left}' y1='{base}' x2='{width - 8}' y2='{base}' "
             f"stroke='{AXIS_C}' stroke-width='1'/>")
    for f in (0, .5, 1):
        tx = left + f * plot_w
        p.append(f"<text x='{tx:.1f}' y='{base + 16:.1f}' fill='{MUTED}' font-size='11' "
                 f"text-anchor='{'start' if f == 0 else ('end' if f == 1 else 'middle')}' "
                 f"style='font-variant-numeric:tabular-nums'>"
                 f"{_fmt(lo + f * (hi - lo), 1)}{esc(unit)}</text>")
    p.append("</svg>")
    return "".join(p)
