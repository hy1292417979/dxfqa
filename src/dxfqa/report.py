"""Report writers: JSON + a simple standalone HTML report."""
from __future__ import annotations

import html
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


def build_report(source: str, findings, fixes, config_dict: dict | None = None,
                 areas: list | None = None) -> dict:
    report = {
        "source": source,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "config": config_dict or {},
        "summary": {
            "findings_total": len(findings),
            "by_severity": dict(Counter(f.severity for f in findings)),
            "by_rule": dict(Counter(f.rule_id for f in findings)),
            "fixes_total": len(fixes),
            "by_fix": dict(Counter(x.fix_id for x in fixes)),
        },
        "findings": [f.to_dict() for f in findings],
        "fixes": [x.to_dict() for x in fixes],
    }
    if areas is not None:
        report["polygon_areas"] = areas
        report["summary"]["polygons_total"] = len(areas)
        report["summary"]["polygon_area_total"] = round(
            sum(a["area"] for a in areas if a["area"] is not None), 4)
    return report


def write_json(report: dict, path) -> None:
    Path(path).write_text(json.dumps(report, indent=2, ensure_ascii=False),
                          encoding="utf-8")


def write_html(report: dict, path) -> None:
    esc = html.escape
    s = report["summary"]
    sev_rows = "".join(
        f"<tr><td>{esc(k)}</td><td>{v}</td></tr>" for k, v in s["by_severity"].items())
    finding_rows = "".join(
        "<tr><td>{}</td><td>{}</td><td>{}</td><td>{}</td><td>{}</td></tr>".format(
            esc(f["rule_id"]), esc(f["severity"]), esc(f["handle"] or "-"),
            esc(f["layer"] or "-"), esc(f["message"]))
        for f in report["findings"]) or '<tr><td colspan="5">No findings.</td></tr>'
    fix_rows = "".join(
        "<tr><td>{}</td><td>{}</td><td>{}</td></tr>".format(
            esc(x["fix_id"]), esc(x["handle"] or "-"), esc(x["description"]))
        for x in report["fixes"]) or '<tr><td colspan="3">No fixes applied.</td></tr>'
    area_rows = "".join(
        "<tr><td>{}</td><td>{}</td><td>{}</td><td>{}</td></tr>".format(
            esc(a["handle"]), esc(a["entity_type"]), esc(a["layer"]),
            f"{a['area']:.4f}" if a["area"] is not None else "invalid geometry")
        for a in report.get("polygon_areas", []))
    areas_section = ""
    if area_rows:
        areas_section = f"""
<h2>Closed-polyline areas</h2>
<table><tr><th>Handle</th><th>Type</th><th>Layer</th><th>Area</th></tr>
{area_rows}</table>"""

    doc = f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8">
<title>DXF QA Report - {esc(report['source'])}</title>
<style>
body {{ font-family: system-ui, sans-serif; margin: 2rem; color: #222; }}
table {{ border-collapse: collapse; margin: 1rem 0 2rem; min-width: 60%; }}
th, td {{ border: 1px solid #ccc; padding: 0.35rem 0.6rem; text-align: left; font-size: 0.9rem; }}
th {{ background: #f0f0f0; }}
.sev-error {{ color: #b00020; }} .sev-warning {{ color: #b26a00; }}
</style></head>
<body>
<h1>DXF QA Report</h1>
<p>Source: <code>{esc(report['source'])}</code><br>
Generated: {esc(report['generated_at'])}<br>
Findings: <strong>{s['findings_total']}</strong> &middot; Fixes applied: <strong>{s['fixes_total']}</strong></p>
<h2>Findings by severity</h2>
<table><tr><th>Severity</th><th>Count</th></tr>{sev_rows}</table>
<h2>Findings</h2>
<table><tr><th>Rule</th><th>Severity</th><th>Handle</th><th>Layer</th><th>Message</th></tr>
{finding_rows}</table>
<h2>Fix log</h2>
<table><tr><th>Fix</th><th>Handle</th><th>Description</th></tr>
{fix_rows}</table>{areas_section}
</body></html>
"""
    Path(path).write_text(doc, encoding="utf-8")
