"""Command line interface.

Usage:
    dxfqa check input.dxf --report out.html [--json out.json]
                          [--fix fixed.dxf] [--config profile.json] [--strict]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import ezdxf

from .checker import CheckConfig, run_checks
from .fixer import apply_fixes
from .report import build_report, write_html, write_json


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="dxfqa", description="DXF drawing QA checker with safe auto-fix.")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("check", help="check a DXF file")
    p.add_argument("input", help="input DXF file (never modified)")
    p.add_argument("--report", help="write an HTML report to this path")
    p.add_argument("--json", dest="json_path", help="write a JSON report to this path")
    p.add_argument("--fix", dest="fix_path",
                   help="apply safe auto-fixes and write the result to this NEW path")
    p.add_argument("--config", help="JSON check profile (rules configuration)")
    p.add_argument("--strict", action="store_true",
                   help="exit with code 2 if any error-severity finding exists")
    args = parser.parse_args(argv)

    config = CheckConfig()
    if args.config:
        config = CheckConfig.from_dict(
            json.loads(Path(args.config).read_text(encoding="utf-8")))

    doc = ezdxf.readfile(args.input)
    findings = run_checks(doc, config)

    areas = None
    try:
        from . import topology
        areas = topology.polygon_areas(doc)
    except ImportError:
        pass  # Shapely unavailable: report simply has no area section

    fixes = []
    if args.fix_path:
        if Path(args.fix_path).resolve() == Path(args.input).resolve():
            print("error: --fix output must be a different file than the input",
                  file=sys.stderr)
            return 2
        fixes = apply_fixes(doc, config)
        doc.saveas(args.fix_path)

    report = build_report(args.input, findings, fixes, config.to_dict(), areas)
    if args.json_path:
        write_json(report, args.json_path)
    if args.report:
        write_html(report, args.report)

    s = report["summary"]
    print(f"{args.input}: {s['findings_total']} finding(s) "
          f"{s['by_severity']} | fixes applied: {s['fixes_total']}")
    if areas:
        print(f"  polygons: {s['polygons_total']} closed, "
              f"total area {s['polygon_area_total']}")
    for f in findings:
        print(f"  [{f.severity:<7}] {f.rule_id:<18} "
              f"handle={f.handle or '-'}  {f.message}")
    for x in fixes:
        print(f"  [fix    ] {x.fix_id:<18} handle={x.handle or '-'}  {x.description}")

    if args.strict and s["by_severity"].get("error"):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
