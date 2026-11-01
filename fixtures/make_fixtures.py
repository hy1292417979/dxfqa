"""Generate synthetic DXF fixtures with KNOWN planted defects.

The manifest (manifest.json) is the ground truth: for each fixture it
records exactly how many findings of each rule the checker must produce
and how many fixes of each kind the fixer must apply. Tests compare
measured results against this manifest, so detection quality is a number,
not an opinion.

A fixture entry may also carry:
- "config": per-fixture profile overrides merged over the base config
  (used by the remap fixture to supply its remapping maps);
- "areas": expected computed areas of its valid closed polylines
  (rounded to 1 decimal, sorted) for the topology area report;
- "fixable_rules": the rule ids that must show zero residuals after the
  fix pass (defaults to the phase-1 set in run_tests.py).
"""
from __future__ import annotations

import json
from pathlib import Path

import ezdxf

HERE = Path(__file__).parent

# Check profile used for all fixture measurements (mirrors
# profiles/survey_basic.json in the project root).
CONFIG = {
    "close_tolerance": 0.05,
    "flag_layer_zero": True,
    "required_attribs": {"SURVEY_POINT": ["PT_ID", "ELEV"]},
    "allowed_text_styles": ["STD"],
    "allowed_dim_styles": ["STD_DIM"],
    "vertex_tolerance": 0.001,
    "overlap_area_tolerance": 1e-6,
    "containment_rules": [{"inner": "BUILDING", "outer": "PARCEL"}],
}

MANIFEST = {
    "config": CONFIG,
    "fixtures": {
        "clean.dxf": {
            "findings": {},
            "fixes": {},
        },
        "defective_layers.dxf": {
            "findings": {
                "LAYER-EMPTY": 1,     # JUNK_EMPTY defined, never used
                "LAYER-DEFPOINTS": 1,  # one LINE on Defpoints
                "LAYER-ZERO": 2,      # one LINE + one CIRCLE on layer "0"
                "STYLE-UNUSED": 2,    # UNUSED_STYLE + UNUSED_DIM
            },
            "fixes": {"FIX-PURGE-LAYER": 1, "FIX-PURGE-STYLE": 2},
        },
        "defective_geometry.dxf": {
            "findings": {
                "GEOM-ZERO-LENGTH": 2,  # zero-length LINE + degenerate LWPOLYLINE
                "ENT-DUPLICATE": 2,     # 3 identical LINEs -> 2 extras
                "PLINE-UNCLOSED": 1,    # endpoint gap 0.02 <= tol 0.05
            },
            "fixes": {"FIX-DUP": 2, "FIX-CLOSE": 1},
        },
        "defective_blocks_text.dxf": {
            "findings": {
                "BLOCK-ATTR-MISSING": 1,  # insert without ELEV
                "BLOCK-ATTR-DUP": 1,      # insert with PT_ID twice
                "BLOCK-ATTR-EMPTY": 1,    # insert with ELEV = ""
                "STYLE-TEXT-NONSTD": 1,   # TEXT in style ROGUE
                "STYLE-DIM-NONSTD": 1,    # DIMENSION in style ROGUE_DIM
                "TEXT-EMPTY": 1,          # empty MTEXT
            },
            "fixes": {},  # none of these are safely auto-fixable
        },
        "defective_topology.dxf": {
            "findings": {
                "TOPO-OVERLAP": 1,        # parcel C overlaps parcel A by 100 sq units
                "TOPO-SELF-INTERSECT": 1,  # bowtie boundary
                "TOPO-CONTAINMENT": 2,    # building outside all parcels + building crossing a parcel edge
                "TOPO-DUP-VERTEX": 2,     # one exact duplicate vertex + one near-duplicate (0.0005)
            },
            "fixes": {},  # topology findings are flag-only
            # valid polygons only (the bowtie is invalid and reports no area):
            # parcels 400/400/400, buildings 36/36/100, dup-vertex parcels 100/100
            "areas": [36.0, 36.0, 100.0, 100.0, 100.0, 400.0, 400.0, 400.0],
        },
        "remap.dxf": {
            "config": {
                "layer_map": {"OLD_ROAD": "ROAD", "OLD_ANNO": "ANNO"},
                "text_style_map": {"OLD_TXT": "STD"},
                "dim_style_map": {"OLD_DIM": "STD_DIM"},
            },
            "findings": {
                "STYLE-TEXT-NONSTD": 1,  # TEXT in OLD_TXT
                "STYLE-DIM-NONSTD": 1,   # DIMENSION in OLD_DIM
                "STYLE-UNUSED": 1,       # STD_DIM defined, not yet used (pre-fix)
            },
            "fixes": {
                "FIX-REMAP-LAYER": 5,      # 3 on OLD_ROAD + TEXT + DIMENSION on OLD_ANNO
                "FIX-REMAP-TEXT-STYLE": 1,
                "FIX-REMAP-DIM-STYLE": 1,
                # OLD_ANNO is NOT purged: the dimension's rendered *D block
                # content still references it (render artifact, see fixer docs).
                "FIX-PURGE-LAYER": 1,      # OLD_ROAD only
                "FIX-PURGE-STYLE": 2,      # OLD_TXT + OLD_DIM
            },
            "fixable_rules": ["ENT-DUPLICATE", "PLINE-UNCLOSED", "LAYER-EMPTY",
                              "STYLE-UNUSED", "STYLE-TEXT-NONSTD", "STYLE-DIM-NONSTD"],
        },
    },
}


def base_doc(layers=(), std_text=False, std_dim=False):
    doc = ezdxf.new("R2010")
    if std_text:
        doc.styles.new("STD", dxfattribs={"font": "txt.shx"})
    if std_dim:
        doc.dimstyles.new("STD_DIM")
    for name in layers:
        doc.layers.add(name)
    return doc


def add_survey_point_block(doc):
    blk = doc.blocks.new("SURVEY_POINT")
    blk.add_circle((0, 0), 0.3)  # block-internal geometry: layer "0" by design
    blk.add_attdef("PT_ID", (0, 0.6), dxfattribs={"style": "STD", "height": 0.35})
    blk.add_attdef("ELEV", (0, -0.9), dxfattribs={"style": "STD", "height": 0.35})


def add_insert(doc, point, attribs):
    ins = doc.modelspace().add_blockref(
        "SURVEY_POINT", point, dxfattribs={"layer": "POINTS"})
    for tag, text in attribs:
        ins.add_attrib(tag, text)
    return ins


def add_dim(doc, dimstyle, base, p1, p2, layer="DIMS"):
    # NOTE: passing "dimstyle" via dxfattribs is silently ignored by ezdxf
    # (DimStyleOverride keeps the base entity on the default style). The
    # style must be set on the underlying DIMENSION entity before render.
    ov = doc.modelspace().add_linear_dim(
        base=base, p1=p1, p2=p2, dxfattribs={"layer": layer})
    ov.dimension.dxf.dimstyle = dimstyle
    ov.render()
    return ov


# ------------------------------------------------------------------ files

def build_clean():
    doc = base_doc(["WALLS", "BOUNDARY", "TEXT_L", "DIMS", "POINTS"],
                   std_text=True, std_dim=True)
    add_survey_point_block(doc)
    msp = doc.modelspace()
    # closed boundary (flag set)
    msp.add_lwpolyline([(0, 0), (10, 0), (10, 10), (0, 10)],
                       close=True, dxfattribs={"layer": "BOUNDARY"})
    # legitimately open polyline with a large endpoint gap: must NOT be flagged
    msp.add_lwpolyline([(0, 20), (6, 20), (6, 24)], dxfattribs={"layer": "WALLS"})
    msp.add_line((0, -5), (10, -5), dxfattribs={"layer": "WALLS"})
    msp.add_circle((20, -5), 1.5, dxfattribs={"layer": "WALLS"})
    msp.add_text("Room 101", dxfattribs={"layer": "TEXT_L", "style": "STD",
                                         "height": 0.5}).set_placement((1, 1))
    msp.add_mtext("General notes", dxfattribs={"layer": "TEXT_L",
                                               "style": "STD"}).set_location((1, 3))
    add_dim(doc, "STD_DIM", base=(0, 18), p1=(0, 15), p2=(8, 15))
    add_insert(doc, (20, 20), [("PT_ID", "P001"), ("ELEV", "12.50")])
    doc.saveas(HERE / "clean.dxf")


def build_defective_layers():
    doc = base_doc(["WALLS", "TEXT_L", "DIMS"], std_text=True, std_dim=True)
    doc.layers.add("JUNK_EMPTY")                                    # LAYER-EMPTY
    doc.styles.new("UNUSED_STYLE", dxfattribs={"font": "txt.shx"})  # STYLE-UNUSED
    doc.dimstyles.new("UNUSED_DIM")                                 # STYLE-UNUSED
    msp = doc.modelspace()
    msp.add_line((0, 0), (5, 0), dxfattribs={"layer": "WALLS"})
    msp.add_text("OK text", dxfattribs={"layer": "TEXT_L", "style": "STD",
                                        "height": 0.5}).set_placement((1, 1))
    add_dim(doc, "STD_DIM", base=(0, 18), p1=(0, 15), p2=(8, 15))
    msp.add_line((0, 5), (5, 5), dxfattribs={"layer": "Defpoints"})  # LAYER-DEFPOINTS
    msp.add_line((0, 8), (5, 8), dxfattribs={"layer": "0"})          # LAYER-ZERO
    msp.add_circle((20, 20), 2, dxfattribs={"layer": "0"})           # LAYER-ZERO
    doc.saveas(HERE / "defective_layers.dxf")


def build_defective_geometry():
    doc = base_doc(["WALLS", "BOUNDARY"])
    msp = doc.modelspace()
    msp.add_line((0, 30), (10, 30), dxfattribs={"layer": "WALLS"})
    msp.add_lwpolyline([(40, 0), (50, 0), (50, 10), (40, 10)],
                       close=True, dxfattribs={"layer": "BOUNDARY"})
    # planted defects
    msp.add_line((5, 5), (5, 5), dxfattribs={"layer": "WALLS"})       # zero-length
    msp.add_lwpolyline([(7, 7), (7, 7), (7, 7)],
                       dxfattribs={"layer": "WALLS"})                 # degenerate
    for _ in range(3):
        msp.add_line((0, 0), (4, 0), dxfattribs={"layer": "WALLS"})   # 2 duplicates
    msp.add_lwpolyline([(20, 0), (30, 0), (30, 10), (20, 0.02)],
                       dxfattribs={"layer": "BOUNDARY"})              # gap 0.02
    doc.saveas(HERE / "defective_geometry.dxf")


def build_defective_blocks_text():
    doc = base_doc(["TEXT_L", "DIMS", "POINTS"], std_text=True, std_dim=True)
    doc.styles.new("ROGUE", dxfattribs={"font": "txt.shx"})  # used below: not "unused"
    doc.dimstyles.new("ROGUE_DIM")
    add_survey_point_block(doc)
    add_insert(doc, (0, 0), [("PT_ID", "P101")])                              # missing ELEV
    add_insert(doc, (10, 0), [("PT_ID", "P102"), ("PT_ID", "P102B"),
                              ("ELEV", "9.90")])                              # dup tag
    add_insert(doc, (20, 0), [("PT_ID", "P103"), ("ELEV", "")])               # empty value
    msp = doc.modelspace()
    msp.add_text("Rogue style text", dxfattribs={"layer": "TEXT_L", "style": "ROGUE",
                                                 "height": 0.5}).set_placement((1, 5))
    msp.add_mtext("", dxfattribs={"layer": "TEXT_L",
                                  "style": "STD"}).set_location((1, 8))       # empty text
    add_dim(doc, "STD_DIM", base=(0, 30), p1=(0, 27), p2=(8, 27))
    add_dim(doc, "ROGUE_DIM", base=(0, 25), p1=(0, 22), p2=(8, 22))           # nonstd dim
    doc.saveas(HERE / "defective_blocks_text.dxf")


def build_topology():
    """Parcels (PARCEL) and buildings (BUILDING) with planted topo defects.

    Traps that must NOT fire: parcels sharing an edge (touching is not
    overlapping), a building fully inside its parcel, and cross-layer
    overlap (building vs parcel is governed by containment, not overlap).
    """
    doc = base_doc(["PARCEL", "BUILDING"])
    msp = doc.modelspace()

    def rect(x0, y0, x1, y1, layer):
        return msp.add_lwpolyline([(x0, y0), (x1, y0), (x1, y1), (x0, y1)],
                                  close=True, dxfattribs={"layer": layer})

    rect(0, 0, 20, 20, "PARCEL")        # parcel A (area 400)
    rect(30, 0, 50, 20, "PARCEL")       # parcel B (area 400)
    rect(10, 10, 30, 30, "PARCEL")      # parcel C: overlaps A by 100, touches B along x=30
    rect(2, 2, 8, 8, "BUILDING")        # inside A: OK
    rect(18, 2, 24, 8, "BUILDING")      # crosses A's edge x=20: TOPO-CONTAINMENT
    rect(100, 100, 110, 110, "BUILDING")  # outside every parcel: TOPO-CONTAINMENT
    # bowtie boundary: TOPO-SELF-INTERSECT (crossing at (70, 10))
    msp.add_lwpolyline([(60, 0), (80, 20), (80, 0), (60, 20)],
                       close=True, dxfattribs={"layer": "PARCEL"})
    # exact duplicate consecutive vertex: TOPO-DUP-VERTEX (area still 100)
    msp.add_lwpolyline([(60, 40), (70, 40), (70, 40), (70, 50), (60, 50)],
                       close=True, dxfattribs={"layer": "PARCEL"})
    # near-duplicate vertex (gap ~0.00054 <= vertex_tolerance 0.001)
    msp.add_lwpolyline([(90, 40), (100, 40), (100, 45), (100.0005, 45.0002),
                        (100, 50), (90, 50)],
                       close=True, dxfattribs={"layer": "PARCEL"})
    doc.saveas(HERE / "defective_topology.dxf")


def build_remap():
    """Entities on legacy layers/styles that a remap profile standardises."""
    doc = base_doc(["OLD_ROAD", "OLD_ANNO", "KEEP"], std_text=True, std_dim=True)
    doc.styles.new("OLD_TXT", dxfattribs={"font": "txt.shx"})
    doc.dimstyles.new("OLD_DIM")
    msp = doc.modelspace()
    msp.add_line((0, 0), (10, 0), dxfattribs={"layer": "OLD_ROAD"})
    msp.add_line((0, 2), (10, 2), dxfattribs={"layer": "OLD_ROAD"})
    msp.add_circle((20, 5), 2, dxfattribs={"layer": "OLD_ROAD"})
    msp.add_text("Legacy note", dxfattribs={"layer": "OLD_ANNO",
                                            "style": "OLD_TXT",
                                            "height": 0.5}).set_placement((1, 10))
    add_dim(doc, "OLD_DIM", base=(0, 24), p1=(0, 21), p2=(8, 21), layer="OLD_ANNO")
    # control entity: already standard, must stay untouched by the remap
    msp.add_text("Standard note", dxfattribs={"layer": "KEEP", "style": "STD",
                                              "height": 0.5}).set_placement((1, 14))
    doc.saveas(HERE / "remap.dxf")


def main():
    build_clean()
    build_defective_layers()
    build_defective_geometry()
    build_defective_blocks_text()
    build_topology()
    build_remap()
    (HERE / "manifest.json").write_text(
        json.dumps(MANIFEST, indent=2), encoding="utf-8")
    print(f"fixtures written to {HERE}")


if __name__ == "__main__":
    main()
