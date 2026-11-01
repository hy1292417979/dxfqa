"""Topology checks for closed polylines, built on Shapely.

Phase 2 (the C1/C4 differentiator): geometric relationship checks that a
plain attribute/rule scan cannot express — self-intersecting boundaries,
overlapping polygons, containment rules between layers, duplicate
vertices — plus computed polygon areas for reporting.

Scope decisions (documented, deliberate):
- Layouts only (model/paper space). Block-definition geometry is symbol
  content, not parcels; running topology inside blocks would be noise.
- Polygon candidates are LWPOLYLINE/POLYLINE entities flagged closed
  with >= 3 distinct vertices. A geometrically closed but unflagged
  polyline stays a PLINE-UNCLOSED warning in the flat checker; whether
  it *must* be a polygon is a profile decision, not a geometry one.
- Bulge arcs are approximated by straight segments between vertices
  (Shapely works on the extracted vertex ring). Self-intersection and
  area results are exact for straight-segment boundaries, approximate
  for arc-bulged ones.
- TOPO-OVERLAP compares polygons on the SAME layer only (the cadastral
  rule "parcels on the parcel layer must not overlap"). Cross-layer
  relationships are governed by the containment rules instead, so a
  building inside its parcel is never an "overlap".
- Invalid (self-intersecting) polygons are reported via
  TOPO-SELF-INTERSECT and excluded from overlap/containment tests —
  their geometry cannot be trusted until repaired.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from shapely.geometry import LineString, Polygon
from shapely.strtree import STRtree
from shapely.validation import explain_validity

from .checker import Finding, dxf_get, layout_containers

TOPO_RULES = ("TOPO-SELF-INTERSECT", "TOPO-OVERLAP",
              "TOPO-CONTAINMENT", "TOPO-DUP-VERTEX")


@dataclass
class PolyRecord:
    handle: str
    entity_type: str
    layer: str
    coords: list  # [(x, y), ...] as authored (no repeated closing point added)
    polygon: Polygon | None = None
    valid: bool = False

    @property
    def distinct_count(self) -> int:
        return len({(round(x, 9), round(y, 9)) for x, y in self.coords})


def _entity_coords(e) -> list | None:
    t = e.dxftype()
    if t == "LWPOLYLINE":
        if not e.closed:
            return None
        return [(float(p[0]), float(p[1])) for p in e.get_points("xy")]
    if t == "POLYLINE":
        if not e.is_closed:
            return None
        return [(float(v.dxf.location.x), float(v.dxf.location.y))
                for v in e.vertices]
    return None


def extract_polygons(doc) -> list[PolyRecord]:
    """All closed-polyline polygon candidates in layouts, document order."""
    records: list[PolyRecord] = []
    for container in layout_containers(doc):
        for e in container:
            coords = _entity_coords(e)
            if coords is None or len(coords) < 3:
                continue
            rec = PolyRecord(e.dxf.handle, e.dxftype(),
                             dxf_get(e, "layer", "0"), coords)
            if rec.distinct_count >= 3:
                ring = list(coords)
                if ring[0] != ring[-1]:
                    ring.append(ring[0])
                rec.polygon = Polygon(ring)
                rec.valid = bool(rec.polygon.is_valid)
            records.append(rec)
    return records


def polygon_areas(doc) -> list[dict]:
    """Computed area per closed polyline (>= 3 distinct vertices).

    Reported as data, not as findings: an area is not a defect, and
    emitting one finding per polygon would drown the defect list (and
    break the zero-false-positive contract for clean drawings).
    `area` is None for invalid (self-intersecting) polygons — Shapely
    returns 0.0 / lobe arithmetic for those, which would be a lie.
    """
    out = []
    for rec in extract_polygons(doc):
        if rec.distinct_count < 3:
            continue
        out.append({
            "handle": rec.handle,
            "entity_type": rec.entity_type,
            "layer": rec.layer,
            "valid": rec.valid,
            "area": float(rec.polygon.area) if rec.valid else None,
        })
    return out


def _dup_vertex_pairs(rec: PolyRecord, tol: float) -> int:
    """Consecutive vertex pairs (incl. the closing pair) within `tol`.

    Exception: when the last vertex exactly repeats the first, that
    closing pair is ring-closure notation, not a duplicate vertex.
    extract_polygons treats such a ring as already closed (it appends
    no extra point), and FIX-CLOSE produces exactly this form when it
    snaps a polyline's endpoints together. Counting the pair would
    turn every FIX-CLOSE into a fresh TOPO-DUP-VERTEX finding on the
    re-checked file. A merely short (but nonzero) closing edge still
    counts, as does any mid-ring duplicate.
    """
    pts = rec.coords
    n = len(pts)
    count = 0
    for i in range(n):
        if i == n - 1 and pts[-1] == pts[0]:
            continue  # repeated closing point: ring notation, not a dup
        if math.dist(pts[i], pts[(i + 1) % n]) <= tol:
            count += 1
    return count


def _is_self_intersecting(rec: PolyRecord) -> bool:
    ring = list(rec.coords)
    if ring[0] != ring[-1]:
        ring.append(ring[0])
    if not LineString(ring).is_simple:
        return True
    # A ring can be simple yet the polygon invalid for other reasons
    # (e.g. a spike). Only the self-intersection class maps to this rule.
    return (not rec.valid) and "self-intersection" in explain_validity(
        rec.polygon).lower()


def run_topology_checks(doc, config) -> list[Finding]:
    findings: list[Finding] = []
    records = [r for r in extract_polygons(doc) if r.distinct_count >= 3]

    # --- per-polygon rules ------------------------------------------------
    for rec in records:
        bad = _dup_vertex_pairs(rec, config.vertex_tolerance)
        if bad:
            findings.append(Finding(
                "TOPO-DUP-VERTEX", "warning", rec.handle,
                f"Closed {rec.entity_type} has {bad} duplicate/near-duplicate "
                f"vertex pair(s) within tolerance {config.vertex_tolerance} "
                f"on layer '{rec.layer}'.",
                rec.entity_type, rec.layer))
        if _is_self_intersecting(rec):
            findings.append(Finding(
                "TOPO-SELF-INTERSECT", "error", rec.handle,
                f"Closed {rec.entity_type} boundary self-intersects "
                f"({explain_validity(rec.polygon)}); the polygon is invalid "
                f"and was excluded from overlap/containment checks.",
                rec.entity_type, rec.layer))

    valid = [r for r in records if r.valid]

    # --- pairwise overlap (same layer only) -------------------------------
    if len(valid) >= 2:
        tree = STRtree([r.polygon for r in valid])
        for i, rec in enumerate(valid):
            for j in tree.query(rec.polygon, predicate="intersects"):
                j = int(j)
                if j <= i:
                    continue
                other = valid[j]
                if other.layer.upper() != rec.layer.upper():
                    continue
                area = rec.polygon.intersection(other.polygon).area
                if area > config.overlap_area_tolerance:
                    findings.append(Finding(
                        "TOPO-OVERLAP", "error", other.handle,
                        f"Polygon overlaps entity {rec.handle} on layer "
                        f"'{rec.layer}' by {area:.4f} square units "
                        f"(tolerance {config.overlap_area_tolerance}).",
                        other.entity_type, other.layer))

    # --- containment rules (profile-driven) --------------------------------
    for rule in config.containment_rules:
        inner_name = str(rule.get("inner", ""))
        outer_name = str(rule.get("outer", ""))
        inners = [r for r in valid if r.layer.upper() == inner_name.upper()]
        outers = [r for r in valid if r.layer.upper() == outer_name.upper()]
        if not inners:
            continue
        tree = STRtree([r.polygon for r in outers]) if outers else None
        for rec in inners:
            contained = False
            if tree is not None:
                for j in tree.query(rec.polygon, predicate="intersects"):
                    if outers[int(j)].polygon.covers(rec.polygon):
                        contained = True
                        break
            if not contained:
                findings.append(Finding(
                    "TOPO-CONTAINMENT", "error", rec.handle,
                    f"{rec.entity_type} on layer '{rec.layer}' is not fully "
                    f"inside any polygon on layer '{outer_name}' "
                    f"(profile containment rule).",
                    rec.entity_type, rec.layer))

    return findings
