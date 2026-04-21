#!/usr/bin/env python
"""
Mantis Verdict Report Generator (brand invariant #5).

Reads the five engine state files and renders a dark-themed HTML document;
optionally shells out to docs/assets/render.js (puppeteer) for PDF rendering.

Zero runtime deps: stdlib only. Renderer deps (puppeteer) live in
docs/assets/package.json and are dev-only — never imported from plugin code.

CLI:
    python docs/architecture/generate.py [--out PATH] [--html-only]

When --out ends in .pdf, puppeteer is invoked unless --html-only is set.
When --html-only is set, the HTML is left at --out (or output/verdict-report.html).
"""
from __future__ import annotations

import argparse
import html as html_lib
import json
import math
import os
import shutil
import string
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
ARCH_DIR = Path(__file__).resolve().parent
TEMPLATE = ARCH_DIR / "template.html"
DEFAULT_OUTPUT = ARCH_DIR / "output" / "verdict-report.pdf"

STATE_FILES = {
    "verdict":  REPO_ROOT / "plugins" / "mantis-verdict"    / "state" / "verdict.jsonl",
    "flags":    REPO_ROOT / "plugins" / "mantis-core"       / "state" / "review-flags.jsonl",
    "sandbox":  REPO_ROOT / "plugins" / "mantis-sandbox"    / "state" / "run-log.jsonl",
    "kappa":    REPO_ROOT / "plugins" / "mantis-rubric"     / "state" / "kappa-log.jsonl",
    "prefs":    REPO_ROOT / "plugins" / "mantis-preference" / "state" / "learnings.json",
    "rubric_cfg": REPO_ROOT / "plugins" / "mantis-rubric" / "config" / "rubric-v1.json",
    "shared_learnings": REPO_ROOT / "shared" / "learnings.json",
}

M5_COLORS = {
    "confirmed":         "#ea6767",
    "timeout":           "#f2b77c",
    "no-bug":            "#5ed0c2",
    "platform-unsupported": "#8b93a7",
    "other":             "#a98ad6",
}

RUBRIC_AXES_DEFAULT = ["clarity", "correctness_at_glance", "idiom_fit", "testability", "simplicity"]


# --- I/O helpers -----------------------------------------------------------

def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def e(s) -> str:
    """HTML-escape a scalar."""
    return html_lib.escape("" if s is None else str(s), quote=True)


# --- Aggregators -----------------------------------------------------------

def aggregate_verdicts(records: list[dict]) -> dict:
    counts = Counter()
    confidence = Counter()
    for r in records:
        counts[r.get("verdict", "UNKNOWN")] += 1
        confidence[r.get("confidence", "unknown")] += 1
    return {
        "total": len(records),
        "deploy": counts.get("DEPLOY", 0),
        "hold":   counts.get("HOLD", 0),
        "fail":   counts.get("FAIL", 0),
        "confidence": dict(confidence),
    }


def aggregate_m1(flags: list[dict]) -> dict:
    by_rule = Counter()
    by_sev = Counter()
    for f in flags:
        by_rule[f.get("rule_id", "unknown")] += 1
        by_sev[f.get("severity", "UNKNOWN")] += 1
    return {"total": len(flags), "by_rule": dict(by_rule), "by_severity": dict(by_sev)}


def aggregate_m5(runs: list[dict]) -> dict:
    by_status = Counter()
    for r in runs:
        s = r.get("status", "other")
        if s not in M5_COLORS:
            s = "other"
        by_status[s] += 1
    return {"total": len(runs), "by_status": dict(by_status)}


def aggregate_m6(prefs: dict) -> dict:
    """prefs is expected to be {"posteriors": {"dev:rule": {"alpha": ..., "beta": ...}}}
    or an alternative shape with flat "{dev}/{rule}: {alpha, beta}" entries. Accept either."""
    means = []
    posteriors = prefs.get("posteriors") if isinstance(prefs, dict) else None
    if posteriors is None and isinstance(prefs, dict):
        posteriors = prefs
    if not isinstance(posteriors, dict):
        return {"total": 0, "histogram": [0] * 10, "means": []}

    for _key, val in posteriors.items():
        if not isinstance(val, dict):
            continue
        alpha = val.get("alpha")
        beta = val.get("beta")
        if not isinstance(alpha, (int, float)) or not isinstance(beta, (int, float)):
            continue
        denom = alpha + beta
        if denom <= 0:
            continue
        means.append(alpha / denom)

    # 10-bucket histogram over [0, 1]
    hist = [0] * 10
    for m in means:
        idx = min(9, max(0, int(m * 10)))
        hist[idx] += 1
    return {"total": len(means), "histogram": hist, "means": means}


def aggregate_m7(kappa_records: list[dict]) -> dict:
    """Session-mean score per axis + unstable-axis list."""
    by_axis_s1 = defaultdict(list)
    by_axis_s2 = defaultdict(list)
    unstable = []
    for r in kappa_records:
        kappa = r.get("kappa", {})
        for axis, v in kappa.items():
            by_axis_s1[axis].append(v.get("s1"))
            by_axis_s2[axis].append(v.get("s2"))
            if v.get("unstable"):
                unstable.append({
                    "file": r.get("file", "?"),
                    "axis": axis,
                    "s1": v.get("s1"),
                    "s2": v.get("s2"),
                    "agreement": v.get("agreement"),
                })
    axes_mean = {}
    for axis in set(list(by_axis_s1) + list(by_axis_s2)) or RUBRIC_AXES_DEFAULT:
        s1 = [x for x in by_axis_s1.get(axis, []) if isinstance(x, (int, float))]
        s2 = [x for x in by_axis_s2.get(axis, []) if isinstance(x, (int, float))]
        combined = s1 + s2
        axes_mean[axis] = sum(combined) / len(combined) if combined else 0.0
    return {
        "total": len(kappa_records),
        "axes_mean": axes_mean,
        "unstable": unstable,
        "all_rows": kappa_records,
    }


# --- HTML fragment builders -----------------------------------------------

def verdict_chip(v: str) -> str:
    cls = {"DEPLOY": "chip-deploy", "HOLD": "chip-hold", "FAIL": "chip-fail"}.get(v, "chip-engine")
    return f'<span class="chip {cls}">{e(v)}</span>'


def engine_chip(engine: str, status: str) -> str:
    s_norm = (status or "").lower()
    cls = "chip-engine"
    if s_norm == "ran":
        cls = "chip-engine ran"
    elif s_norm in ("unsupported", "platform-unsupported"):
        cls = "chip-engine unsupported"
    elif s_norm in ("failed", "fail"):
        cls = "chip-engine fail"
    return f'<span class="chip {cls}">{e(engine)}:{e(s_norm or "n/a")}</span>'


def render_verdict_rows(records: list[dict]) -> str:
    rows = []
    for r in records:
        file_path = r.get("file", "?")
        verdict = r.get("verdict", "UNKNOWN")
        conf = r.get("confidence", "unknown")
        engines = r.get("engines", [])
        engine_html = " ".join(engine_chip(en.get("engine", "?"), en.get("status", "?")) for en in engines)
        reasons = r.get("reasons", [])
        primary = reasons[0] if reasons else ""
        rows.append(
            f'<tr>'
            f'<td class="file">{e(file_path)}</td>'
            f'<td>{verdict_chip(verdict)}</td>'
            f'<td class="mono">{e(conf)}</td>'
            f'<td>{engine_html}</td>'
            f'<td class="reason">{e(primary)}</td>'
            f'</tr>'
        )
    if not rows:
        rows.append('<tr><td colspan="5" class="reason">No verdicts recorded.</td></tr>')
    return "\n      ".join(rows)


def render_confidence_bars(confidence_counts: dict) -> str:
    total = sum(confidence_counts.values()) or 1
    order = ["high", "preliminary", "reduced", "unknown"]
    seen = set()
    items = []
    for label in order + list(confidence_counts.keys()):
        if label in seen or label not in confidence_counts:
            continue
        seen.add(label)
        count = confidence_counts[label]
        pct = 100.0 * count / total
        tone = "bar-fill"
        if label == "reduced": tone = "bar-fill amber"
        elif label == "unknown": tone = "bar-fill clay"
        items.append(
            f'<div class="bar-row">'
            f'<div class="bar-label">{e(label)}</div>'
            f'<div class="bar-track"><div class="{tone}" style="width: {pct:.1f}%;"></div></div>'
            f'<div class="bar-count">{count}</div>'
            f'</div>'
        )
    return "\n    ".join(items) if items else '<div class="meta">(no data)</div>'


def render_rule_bars(by_rule: dict, tone: str = "bar-fill") -> str:
    if not by_rule:
        return '<div class="meta">(no flags)</div>'
    max_count = max(by_rule.values()) or 1
    items = []
    for rule, count in sorted(by_rule.items(), key=lambda kv: -kv[1]):
        pct = 100.0 * count / max_count
        items.append(
            f'<div class="bar-row">'
            f'<div class="bar-label">{e(rule)}</div>'
            f'<div class="bar-track"><div class="{tone}" style="width: {pct:.1f}%;"></div></div>'
            f'<div class="bar-count">{count}</div>'
            f'</div>'
        )
    return "\n      ".join(items)


def render_m5_pie(by_status: dict) -> tuple[str, str]:
    """SVG inline pie. Chose SVG over <canvas> because PDF rendering via
    print-to-pdf rasterizes <canvas> at screen DPI (fuzzy at A4); SVG is
    vector and prints crisp."""
    total = sum(by_status.values())
    size = 180
    cx = cy = size / 2
    r = size / 2 - 6
    if total == 0:
        svg = (
            f'<svg class="pie" viewBox="0 0 {size} {size}" width="{size}" height="{size}" '
            f'xmlns="http://www.w3.org/2000/svg">'
            f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="#1c2230" stroke="#262d3c"/>'
            f'<text x="{cx}" y="{cy+4}" text-anchor="middle" fill="#8b93a7" '
            f'font-family="ui-monospace" font-size="11">no runs</text></svg>'
        )
        return svg, '<div class="legend">(no M5 runs)</div>'

    paths = []
    legend_items = []
    start = -math.pi / 2
    for status, count in sorted(by_status.items(), key=lambda kv: -kv[1]):
        frac = count / total
        end = start + frac * 2 * math.pi
        large = 1 if frac > 0.5 else 0
        x1 = cx + r * math.cos(start)
        y1 = cy + r * math.sin(start)
        x2 = cx + r * math.cos(end)
        y2 = cy + r * math.sin(end)
        color = M5_COLORS.get(status, M5_COLORS["other"])
        if frac >= 0.9999:
            # Full circle edge case
            path = f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="{color}"/>'
        else:
            path = (
                f'<path d="M {cx:.2f} {cy:.2f} L {x1:.2f} {y1:.2f} '
                f'A {r:.2f} {r:.2f} 0 {large} 1 {x2:.2f} {y2:.2f} Z" '
                f'fill="{color}"/>'
            )
        paths.append(path)
        legend_items.append(
            f'<span><span class="legend-dot" style="background:{color};"></span>'
            f'{e(status)} ({count}, {100*frac:.0f}%)</span>'
        )
        start = end

    svg = (
        f'<svg class="pie" viewBox="0 0 {size} {size}" width="{size}" height="{size}" '
        f'xmlns="http://www.w3.org/2000/svg">'
        + "".join(paths)
        + '</svg>'
    )
    legend = '<div class="legend">' + "".join(legend_items) + '</div>'
    return svg, legend


def render_m6_histogram(histogram: list[int]) -> str:
    total = sum(histogram)
    if total == 0:
        return '<div class="meta">(no posteriors observed)</div>'
    max_bin = max(histogram) or 1
    bars = []
    for i, count in enumerate(histogram):
        lo, hi = i / 10, (i + 1) / 10
        pct = 100.0 * count / max_bin
        tone = "bar-fill" if (lo + hi) / 2 >= 0.5 else "bar-fill amber"
        bars.append(
            f'<div class="bar-row">'
            f'<div class="bar-label">{lo:.1f}&ndash;{hi:.1f}</div>'
            f'<div class="bar-track"><div class="{tone}" style="width: {pct:.1f}%;"></div></div>'
            f'<div class="bar-count">{count}</div>'
            f'</div>'
        )
    return "\n      ".join(bars)


def render_m7_radar(axes_mean: dict) -> str:
    """Inline SVG radar chart. 5-axis regular polygon; scale 0-5."""
    size = 220
    cx = cy = size / 2
    r_max = size / 2 - 30
    axes = list(axes_mean.keys()) or RUBRIC_AXES_DEFAULT
    n = len(axes)
    if n == 0:
        return '<div class="meta">(no rubric data)</div>'

    # grid: 5 concentric polygons
    grid = []
    for level in range(1, 6):
        pts = []
        rr = r_max * level / 5
        for i in range(n):
            ang = -math.pi / 2 + i * 2 * math.pi / n
            pts.append(f"{cx + rr*math.cos(ang):.2f},{cy + rr*math.sin(ang):.2f}")
        grid.append(
            f'<polygon points="{" ".join(pts)}" fill="none" stroke="#262d3c" stroke-width="1"/>'
        )

    # axis spokes + labels
    spokes = []
    labels = []
    for i, axis in enumerate(axes):
        ang = -math.pi / 2 + i * 2 * math.pi / n
        x = cx + r_max * math.cos(ang)
        y = cy + r_max * math.sin(ang)
        spokes.append(
            f'<line x1="{cx:.2f}" y1="{cy:.2f}" x2="{x:.2f}" y2="{y:.2f}" '
            f'stroke="#262d3c" stroke-width="1"/>'
        )
        lx = cx + (r_max + 14) * math.cos(ang)
        ly = cy + (r_max + 14) * math.sin(ang) + 3
        # shorten long axis names for legibility
        short = axis.replace("correctness_at_glance", "correctness").replace("_", " ")
        labels.append(
            f'<text x="{lx:.2f}" y="{ly:.2f}" text-anchor="middle" '
            f'fill="#8b93a7" font-family="ui-monospace" font-size="9">{e(short)}</text>'
        )

    # data polygon
    data_pts = []
    for i, axis in enumerate(axes):
        score = axes_mean.get(axis, 0)
        ang = -math.pi / 2 + i * 2 * math.pi / n
        rr = r_max * min(5, max(0, score)) / 5
        data_pts.append(f"{cx + rr*math.cos(ang):.2f},{cy + rr*math.sin(ang):.2f}")
    data_polygon = (
        f'<polygon points="{" ".join(data_pts)}" fill="rgba(94, 208, 194, 0.25)" '
        f'stroke="#5ed0c2" stroke-width="2"/>'
    )

    svg = (
        f'<svg class="radar" viewBox="0 0 {size} {size}" width="{size}" height="{size}" '
        f'xmlns="http://www.w3.org/2000/svg">'
        + "".join(grid) + "".join(spokes) + data_polygon + "".join(labels)
        + "</svg>"
    )
    return svg


def render_kappa_rows(records: list[dict]) -> str:
    rows = []
    shown = 0
    for r in records:
        file_path = r.get("file", "?")
        kappa = r.get("kappa", {})
        # Prefer unstable axes first, then any axis with non-1.0 agreement
        order = sorted(
            kappa.items(),
            key=lambda kv: (not kv[1].get("unstable", False), kv[1].get("agreement", 1.0))
        )
        for axis, v in order:
            agreement = v.get("agreement", 1.0)
            unstable = v.get("unstable", False) or (isinstance(agreement, (int, float)) and agreement < 0.4)
            if not unstable and agreement >= 0.9 and shown >= 8:
                continue  # de-emphasize fully-stable rows after a few shown
            status = '<span class="unstable">UNSTABLE</span>' if unstable else '<span class="stable">stable</span>'
            rows.append(
                f'<tr>'
                f'<td class="file">{e(file_path)}</td>'
                f'<td class="mono">{e(axis)}</td>'
                f'<td class="mono">{e(v.get("s1"))}</td>'
                f'<td class="mono">{e(v.get("s2"))}</td>'
                f'<td class="mono">{e(agreement)}</td>'
                f'<td>{status}</td>'
                f'</tr>'
            )
            shown += 1
    if not rows:
        rows.append('<tr><td colspan="6" class="reason">No kappa records.</td></tr>')
    return "\n      ".join(rows)


def render_learnings(shared_learnings: dict, limit: int = 8) -> tuple[str, int]:
    entries = []
    if isinstance(shared_learnings, dict):
        entries = shared_learnings.get("entries", []) or []
    elif isinstance(shared_learnings, list):
        entries = shared_learnings
    entries = [en for en in entries if isinstance(en, dict)]
    entries = entries[-limit:][::-1]
    if not entries:
        return '<li class="meta">No learnings recorded yet.</li>', 0
    items = []
    for en in entries:
        code = en.get("code", "F??")
        note = en.get("note") or en.get("hypothesis") or en.get("outcome") or ""
        date = en.get("date", "")
        items.append(
            f'<li><span class="fcode">{e(code)}</span>'
            f'<span class="meta">{e(date)}</span> &mdash; {e(note)}</li>'
        )
    return "\n    ".join(items), len(entries)


def render_verdict_dump(records: list[dict], limit_chars: int = 60000) -> str:
    dump = "\n".join(json.dumps(r, separators=(", ", ": "), ensure_ascii=False) for r in records)
    if len(dump) > limit_chars:
        dump = dump[:limit_chars] + f"\n... ({len(dump) - limit_chars} more chars truncated)"
    return e(dump)


# --- Main build ------------------------------------------------------------

def build_html(repo_root: Path) -> str:
    verdicts    = read_jsonl(STATE_FILES["verdict"])
    flags       = read_jsonl(STATE_FILES["flags"])
    sandbox     = read_jsonl(STATE_FILES["sandbox"])
    kappa       = read_jsonl(STATE_FILES["kappa"])
    prefs       = read_json(STATE_FILES["prefs"])
    rubric_cfg  = read_json(STATE_FILES["rubric_cfg"])
    shared_lrn  = read_json(STATE_FILES["shared_learnings"])

    v_agg = aggregate_verdicts(verdicts)
    m1 = aggregate_m1(flags)
    m5 = aggregate_m5(sandbox)
    m6 = aggregate_m6(prefs)
    m7 = aggregate_m7(kappa)

    m5_svg, m5_legend = render_m5_pie(m5["by_status"])
    learnings_html, learnings_count = render_learnings(shared_lrn)

    subs = {
        "repo_name": repo_root.name,
        "session_date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "generated_ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "rubric_version": str(rubric_cfg.get("version", "1.0")),
        "file_count": v_agg["total"],
        "deploy_count": v_agg["deploy"],
        "hold_count":   v_agg["hold"],
        "fail_count":   v_agg["fail"],
        "confidence_bars": render_confidence_bars(v_agg["confidence"]),
        "verdict_rows": render_verdict_rows(verdicts),
        "m1_total": m1["total"],
        "m1_rule_bars": render_rule_bars(m1["by_rule"]),
        "m5_total": m5["total"],
        "m5_pie_svg": m5_svg,
        "m5_legend": m5_legend,
        "m6_histogram": render_m6_histogram(m6["histogram"]),
        "m7_radar_svg": render_m7_radar(m7["axes_mean"]),
        "kappa_rows": render_kappa_rows(kappa),
        "learnings_items": learnings_html,
        "learnings_count": learnings_count,
        "verdict_dump": render_verdict_dump(verdicts),
    }

    tpl = string.Template(TEMPLATE.read_text(encoding="utf-8"))
    # safe_substitute so unset keys pass through; but we then validate nothing slipped.
    html_out = tpl.safe_substitute(subs)
    # Sanity: catch any stray ${...} placeholders we forgot to populate
    remaining = []
    i = 0
    while True:
        j = html_out.find("${", i)
        if j == -1:
            break
        k = html_out.find("}", j)
        if k == -1:
            break
        remaining.append(html_out[j:k+1])
        i = k + 1
    if remaining:
        sys.stderr.write(f"WARN: unresolved template placeholders: {sorted(set(remaining))}\n")
    return html_out


def render_pdf(html_path: Path, pdf_path: Path) -> int:
    """Shell out to docs/architecture/render.js via npx/node."""
    renderer = ARCH_DIR / "render.js"
    if not renderer.exists():
        sys.stderr.write(f"ERROR: renderer missing at {renderer}\n")
        return 2
    node = shutil.which("node")
    if not node:
        sys.stderr.write("ERROR: `node` not on PATH — install Node.js or use --html-only\n")
        return 3
    cmd = [node, str(renderer), str(html_path), str(pdf_path)]
    proc = subprocess.run(cmd, cwd=str(REPO_ROOT))
    return proc.returncode


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Mantis verdict-report PDF/HTML generator.")
    ap.add_argument("--out", default=str(DEFAULT_OUTPUT),
                    help="Output path (.pdf invokes puppeteer; .html writes HTML only).")
    ap.add_argument("--html-only", action="store_true",
                    help="Skip the puppeteer step; write HTML at --out (or sibling .html if --out is .pdf).")
    args = ap.parse_args(argv)

    out_path = Path(args.out).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    html_out = build_html(REPO_ROOT)

    if args.html_only or out_path.suffix.lower() == ".html":
        html_target = out_path if out_path.suffix.lower() == ".html" else out_path.with_suffix(".html")
        html_target.write_text(html_out, encoding="utf-8")
        print(f"wrote {html_target} ({len(html_out):,} bytes)")
        return 0

    # PDF mode: write HTML to a sibling, then render
    html_target = out_path.with_suffix(".html")
    html_target.write_text(html_out, encoding="utf-8")
    print(f"wrote {html_target} ({len(html_out):,} bytes)")
    rc = render_pdf(html_target, out_path)
    if rc != 0:
        sys.stderr.write("PDF render failed; HTML was written. Use --html-only to skip this step.\n")
        return rc
    print(f"wrote {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
