"""Pytest suite for the DXF QA engine (fixtures are the ground truth)."""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path

import ezdxf
import pytest

import make_fixtures
from dxfqa.checker import CheckConfig, run_checks
from dxfqa.fixer import apply_fixes

ROOT = Path(__file__).parent.parent
FIXTURES = ROOT / "fixtures"


@pytest.fixture(scope="session", autouse=True)
def fixtures_ready():
    make_fixtures.main()


@pytest.fixture(scope="session")
def manifest():
    return json.loads((FIXTURES / "manifest.json").read_text())


@pytest.fixture(scope="session")
def config(manifest):
    return CheckConfig.from_dict(manifest["config"])


def test_clean_file_has_zero_findings(config):
    findings = run_checks(ezdxf.readfile(FIXTURES / "clean.dxf"), config)
    assert findings == [], f"false positives on clean file: {findings}"


@pytest.mark.parametrize("name", [
    "defective_layers.dxf", "defective_geometry.dxf", "defective_blocks_text.dxf"])
def test_detection_matches_manifest(name, manifest, config):
    findings = run_checks(ezdxf.readfile(FIXTURES / name), config)
    assert Counter(f.rule_id for f in findings) == Counter(manifest["fixtures"][name]["findings"])


@pytest.mark.parametrize("name", [
    "clean.dxf", "defective_layers.dxf", "defective_geometry.dxf",
    "defective_blocks_text.dxf"])
def test_fix_counts_match_manifest(name, manifest, config):
    doc = ezdxf.readfile(FIXTURES / name)
    fixes = apply_fixes(doc, config)
    assert Counter(x.fix_id for x in fixes) == Counter(manifest["fixtures"][name]["fixes"])


def test_fixed_file_rechecks_clean_of_fixable_rules(config, tmp_path):
    doc = ezdxf.readfile(FIXTURES / "defective_geometry.dxf")
    apply_fixes(doc, config)
    out = tmp_path / "fixed.dxf"
    doc.saveas(out)
    after = Counter(f.rule_id for f in run_checks(ezdxf.readfile(out), config))
    assert after.get("ENT-DUPLICATE", 0) == 0
    assert after.get("PLINE-UNCLOSED", 0) == 0
    # zero-length geometry is flagged but deliberately NOT auto-deleted
    assert after.get("GEOM-ZERO-LENGTH", 0) == 2


def test_input_file_never_modified(config, tmp_path):
    src = FIXTURES / "defective_layers.dxf"
    digest = hashlib.sha256(src.read_bytes()).hexdigest()
    doc = ezdxf.readfile(src)
    run_checks(doc, config)
    apply_fixes(doc, config)
    doc.saveas(tmp_path / "elsewhere.dxf")
    assert hashlib.sha256(src.read_bytes()).hexdigest() == digest


def test_finding_structure(config):
    findings = run_checks(ezdxf.readfile(FIXTURES / "defective_geometry.dxf"), config)
    for f in findings:
        d = f.to_dict()
        assert set(d) == {"rule_id", "severity", "handle", "entity_type", "layer", "message"}
        assert d["severity"] in ("error", "warning", "info")


def test_cli_end_to_end(tmp_path):
    html_out = tmp_path / "report.html"
    json_out = tmp_path / "report.json"
    fixed_out = tmp_path / "fixed.dxf"
    proc = subprocess.run(
        [sys.executable, str(ROOT / "cli.py"), "check",
         str(FIXTURES / "defective_geometry.dxf"),
         "--config", str(ROOT / "profiles" / "survey_basic.json"),
         "--report", str(html_out), "--json", str(json_out),
         "--fix", str(fixed_out)],
        capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    report = json.loads(json_out.read_text())
    assert report["summary"]["findings_total"] == 5
    assert report["summary"]["fixes_total"] == 3
    assert "<table" in html_out.read_text()
    assert fixed_out.exists()


def test_cli_refuses_to_overwrite_input(tmp_path):
    src = FIXTURES / "clean.dxf"
    proc = subprocess.run(
        [sys.executable, str(ROOT / "cli.py"), "check", str(src), "--fix", str(src)],
        capture_output=True, text=True)
    assert proc.returncode == 2


# ------------------------------------------------------------------ phase 2

def _merged_config(manifest, name):
    """Base fixture profile + the fixture's own overrides (e.g. remap maps)."""
    data = dict(manifest["config"])
    data.update(manifest["fixtures"][name].get("config", {}))
    return CheckConfig.from_dict(data)


@pytest.mark.parametrize("name", ["defective_topology.dxf", "remap.dxf"])
def test_phase2_detection_matches_manifest(name, manifest):
    config = _merged_config(manifest, name)
    findings = run_checks(ezdxf.readfile(FIXTURES / name), config)
    assert Counter(f.rule_id for f in findings) == Counter(
        manifest["fixtures"][name]["findings"])


@pytest.mark.parametrize("name", ["defective_topology.dxf", "remap.dxf"])
def test_phase2_fix_counts_match_manifest(name, manifest):
    config = _merged_config(manifest, name)
    doc = ezdxf.readfile(FIXTURES / name)
    fixes = apply_fixes(doc, config)
    assert Counter(x.fix_id for x in fixes) == Counter(
        manifest["fixtures"][name]["fixes"])


def test_topology_areas_match_manifest(manifest):
    from dxfqa.topology import polygon_areas
    areas = polygon_areas(ezdxf.readfile(FIXTURES / "defective_topology.dxf"))
    measured = sorted(round(a["area"], 1) for a in areas if a["area"] is not None)
    assert measured == manifest["fixtures"]["defective_topology.dxf"]["areas"]
    # the self-intersecting bowtie reports no area rather than a fake number
    assert any(a["area"] is None and not a["valid"] for a in areas)


def test_topology_findings_carry_structure(manifest):
    config = _merged_config(manifest, "defective_topology.dxf")
    findings = run_checks(ezdxf.readfile(FIXTURES / "defective_topology.dxf"), config)
    topo = [f for f in findings if f.rule_id.startswith("TOPO-")]
    assert len(topo) == 6
    for f in topo:
        assert f.handle and f.layer and f.entity_type == "LWPOLYLINE"


def test_remap_actually_moves_entities(manifest, tmp_path):
    config = _merged_config(manifest, "remap.dxf")
    doc = ezdxf.readfile(FIXTURES / "remap.dxf")
    apply_fixes(doc, config)
    out = tmp_path / "remapped.dxf"
    doc.saveas(out)
    doc2 = ezdxf.readfile(out)
    layers_used = {e.dxf.layer for b in doc2.blocks for e in b}
    assert "OLD_ROAD" not in layers_used
    assert "ROAD" in layers_used and "ANNO" in layers_used
    texts = [e for e in doc2.modelspace() if e.dxftype() == "TEXT"]
    assert {t.dxf.style for t in texts} == {"STD"}
    dims = [e for e in doc2.modelspace() if e.dxftype() == "DIMENSION"]
    assert dims and all(d.dxf.dimstyle == "STD_DIM" for d in dims)
    # fixer created the target layer that did not exist in the source
    assert "ROAD" in doc2.layers


def test_remap_creates_missing_targets_and_copies_layer_props():
    doc = ezdxf.new("R2010")
    doc.layers.add("LEGACY")
    doc.layers.get("LEGACY").dxf.color = 3
    msp = doc.modelspace()
    msp.add_line((0, 0), (1, 1), dxfattribs={"layer": "LEGACY"})
    cfg = CheckConfig.from_dict({"layer_map": {"LEGACY": "STD_ROAD"}})
    fixes = apply_fixes(doc, cfg)
    assert Counter(x.fix_id for x in fixes)["FIX-REMAP-LAYER"] == 1
    assert "STD_ROAD" in doc.layers
    assert doc.layers.get("STD_ROAD").dxf.color == 3  # copied from source
    assert [e.dxf.layer for e in msp] == ["STD_ROAD"]


def test_cli_report_includes_polygon_areas(tmp_path):
    json_out = tmp_path / "report.json"
    proc = subprocess.run(
        [sys.executable, str(ROOT / "cli.py"), "check",
         str(FIXTURES / "defective_topology.dxf"),
         "--config", str(ROOT / "profiles" / "survey_basic.json"),
         "--json", str(json_out)],
        capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    report = json.loads(json_out.read_text())
    # survey_basic.json defines no containment rules -> only overlap,
    # self-intersection and duplicate-vertex findings fire (4, not 6)
    assert report["summary"]["findings_total"] == 4
    assert report["summary"]["by_rule"].get("TOPO-CONTAINMENT", 0) == 0
    assert report["summary"]["polygons_total"] == 9
    assert report["summary"]["polygon_area_total"] == pytest.approx(
        1572.00125, abs=0.01)
