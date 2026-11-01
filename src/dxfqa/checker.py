"""DXF drawing QA checker: rule engine over ezdxf documents.

Every check returns structured Finding records (rule id, severity, entity
handle, message). The checker never modifies the document.

Scope decisions (documented, deliberate):
- Layer-placement rules (LAYER-ZERO / LAYER-DEFPOINTS) apply to layouts
  (model/paper space) only. Entities inside block definitions
  conventionally live on layer "0" by design, so flagging them there
  would be pure noise.
- Geometry / duplicate / text / attribute rules apply to layouts AND to
  named (non-anonymous) block definitions.
- System layers ("0", "DEFPOINTS") and system styles ("Standard") are
  never reported as unused.
- Topology checks (phase 2, `topology.py`, Shapely) run as part of
  run_checks when Shapely is importable; without it they are skipped
  and only the flat rules above apply.
"""
from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field

# entity types that conventionally sit on layer "0" as sub-entities
_SUB_ENTITY_TYPES = {"ATTRIB", "ATTDEF", "VERTEX", "SEQEND"}

SYSTEM_LAYERS = {"0", "DEFPOINTS"}
SYSTEM_STYLES = {"STANDARD"}  # compared upper-cased, covers "Standard"

ZERO_TOL = 1e-9


@dataclass
class Finding:
    rule_id: str
    severity: str  # "error" | "warning" | "info"
    handle: str | None
    message: str
    entity_type: str | None = None
    layer: str | None = None

    def to_dict(self) -> dict:
        return {
            "rule_id": self.rule_id,
            "severity": self.severity,
            "handle": self.handle,
            "entity_type": self.entity_type,
            "layer": self.layer,
            "message": self.message,
        }


@dataclass
class CheckConfig:
    close_tolerance: float = 0.05  # endpoint gap <= tol => polyline "should be closed"
    dup_decimal: int = 6           # coordinate rounding for duplicate signatures
    flag_layer_zero: bool = True
    required_attribs: dict = field(default_factory=dict)  # block name -> [tags]
    allowed_text_styles: set | None = None  # upper-cased; None disables the rule
    allowed_dim_styles: set | None = None
    # phase 2: topology (see topology.py)
    vertex_tolerance: float = 0.001        # consecutive vertices closer than this are duplicates
    overlap_area_tolerance: float = 1e-6   # same-layer overlap area above this is a defect
    containment_rules: list = field(default_factory=list)  # [{"inner": layer, "outer": layer}]
    # phase 2: profile-driven remapping (consumed by fixer.apply_fixes)
    layer_map: dict = field(default_factory=dict)      # old layer -> standard layer
    text_style_map: dict = field(default_factory=dict)  # old text style -> standard style
    dim_style_map: dict = field(default_factory=dict)   # old dim style -> standard style

    @classmethod
    def from_dict(cls, data: dict) -> "CheckConfig":
        cfg = cls()
        if "close_tolerance" in data:
            cfg.close_tolerance = float(data["close_tolerance"])
        if "flag_layer_zero" in data:
            cfg.flag_layer_zero = bool(data["flag_layer_zero"])
        if "required_attribs" in data:
            cfg.required_attribs = {k: list(v) for k, v in data["required_attribs"].items()}
        if data.get("allowed_text_styles") is not None:
            cfg.allowed_text_styles = {str(s).upper() for s in data["allowed_text_styles"]}
        if data.get("allowed_dim_styles") is not None:
            cfg.allowed_dim_styles = {str(s).upper() for s in data["allowed_dim_styles"]}
        if "vertex_tolerance" in data:
            cfg.vertex_tolerance = float(data["vertex_tolerance"])
        if "overlap_area_tolerance" in data:
            cfg.overlap_area_tolerance = float(data["overlap_area_tolerance"])
        if "containment_rules" in data:
            cfg.containment_rules = [dict(r) for r in data["containment_rules"]]
        for key in ("layer_map", "text_style_map", "dim_style_map"):
            if data.get(key):
                setattr(cfg, key, {str(k): str(v) for k, v in data[key].items()})
        return cfg

    def to_dict(self) -> dict:
        return {
            "close_tolerance": self.close_tolerance,
            "flag_layer_zero": self.flag_layer_zero,
            "required_attribs": self.required_attribs,
            "allowed_text_styles": sorted(self.allowed_text_styles) if self.allowed_text_styles else None,
            "allowed_dim_styles": sorted(self.allowed_dim_styles) if self.allowed_dim_styles else None,
            "vertex_tolerance": self.vertex_tolerance,
            "overlap_area_tolerance": self.overlap_area_tolerance,
            "containment_rules": self.containment_rules,
            "layer_map": self.layer_map,
            "text_style_map": self.text_style_map,
            "dim_style_map": self.dim_style_map,
        }


# ---------------------------------------------------------------- helpers

def dxf_get(entity, key, default=None):
    """Read an optional DXF attribute without raising."""
    try:
        return getattr(entity.dxf, key)
    except Exception:
        return default


def layout_containers(doc):
    """Model/paper space layouts (user-issued geometry)."""
    return list(doc.layouts)


def check_containers(doc):
    """Layouts plus named (non-anonymous) block definitions."""
    containers = layout_containers(doc)
    for block in doc.blocks:
        if not block.name.startswith("*"):
            containers.append(block)
    return containers


def all_entities(doc):
    """Every entity in the document, including anonymous blocks (usage scan)."""
    for block in doc.blocks:
        yield from block


def usage_counts(doc):
    """Return (layer_use, text_style_use, dim_style_use) Counters."""
    layer_use: Counter = Counter()
    text_style_use: Counter = Counter()
    dim_style_use: Counter = Counter()
    for e in all_entities(doc):
        layer = dxf_get(e, "layer", None)
        if layer is not None:
            layer_use[layer] += 1
        t = e.dxftype()
        if t in ("TEXT", "MTEXT", "ATTRIB", "ATTDEF"):
            text_style_use[dxf_get(e, "style", "Standard")] += 1
        elif t == "DIMENSION":
            dim_style_use[dxf_get(e, "dimstyle", "Standard") or "Standard"] += 1
    return layer_use, text_style_use, dim_style_use


def _pt_key(p, dec: int):
    x = round(float(p[0]), dec)
    y = round(float(p[1]), dec)
    z = round(float(p[2]), dec) if len(p) > 2 else 0.0
    return (x, y, z)


def entity_signature(e, dec: int = 6):
    """Geometric identity used for exact-duplicate detection.

    Returns None for entity types that are not duplicate-checked.
    """
    t = e.dxftype()
    layer = dxf_get(e, "layer", "0")
    if t == "LINE":
        a, b = _pt_key(e.dxf.start, dec), _pt_key(e.dxf.end, dec)
        return (t, layer, min(a, b), max(a, b))
    if t == "CIRCLE":
        return (t, layer, _pt_key(e.dxf.center, dec), round(float(e.dxf.radius), dec))
    if t == "ARC":
        return (t, layer, _pt_key(e.dxf.center, dec), round(float(e.dxf.radius), dec),
                round(float(e.dxf.start_angle), 4), round(float(e.dxf.end_angle), 4))
    if t == "LWPOLYLINE":
        pts = tuple(_pt_key(p, dec) for p in e.get_points("xy"))
        return (t, layer, pts, bool(e.closed))
    if t == "POINT":
        return (t, layer, _pt_key(e.dxf.location, dec))
    if t == "TEXT":
        return (t, layer, e.dxf.text, _pt_key(e.dxf.insert, dec),
                dxf_get(e, "style", "Standard"), round(float(dxf_get(e, "height", 0) or 0), dec))
    if t == "INSERT":
        return (t, layer, e.dxf.name, _pt_key(e.dxf.insert, dec))
    return None


def find_duplicates(doc, config: CheckConfig):
    """Return [(extra_entity, first_entity), ...] across check containers."""
    seen: dict = {}
    dups = []
    for container in check_containers(doc):
        for e in container:
            sig = entity_signature(e, config.dup_decimal)
            if sig is None:
                continue
            if sig in seen:
                dups.append((e, seen[sig]))
            else:
                seen[sig] = e
    return dups


# ---------------------------------------------------------------- checks

def run_checks(doc, config: CheckConfig | None = None) -> list[Finding]:
    config = config or CheckConfig()
    findings: list[Finding] = []
    layer_use, text_style_use, dim_style_use = usage_counts(doc)

    # --- table-level rule: empty / unused layers -------------------------
    for layer in doc.layers:
        name = layer.dxf.name
        if name.upper() in SYSTEM_LAYERS:
            continue
        if layer_use.get(name, 0) == 0:
            findings.append(Finding(
                "LAYER-EMPTY", "info", None,
                f"Layer '{name}' is defined but has no entities on it.",
                layer=name))

    # --- layer placement rules (layouts only, by design) -----------------
    for container in layout_containers(doc):
        for e in container:
            t = e.dxftype()
            if t in _SUB_ENTITY_TYPES:
                continue
            layer = dxf_get(e, "layer", "0")
            if layer and layer.upper() == "DEFPOINTS":
                findings.append(Finding(
                    "LAYER-DEFPOINTS", "error", e.dxf.handle,
                    f"{t} is on the DEFPOINTS layer; entities there do not plot.",
                    t, layer))
            elif layer == "0" and config.flag_layer_zero:
                findings.append(Finding(
                    "LAYER-ZERO", "warning", e.dxf.handle,
                    f"{t} is on layer '0' instead of a named layer.",
                    t, layer))

    # --- per-entity geometry / text / block rules ------------------------
    for container in check_containers(doc):
        for e in container:
            t = e.dxftype()
            layer = dxf_get(e, "layer", "0")
            handle = e.dxf.handle

            if t == "LINE":
                if e.dxf.start.distance(e.dxf.end) <= ZERO_TOL:
                    findings.append(Finding(
                        "GEOM-ZERO-LENGTH", "error", handle,
                        "LINE has zero length (start point == end point).", t, layer))

            elif t == "LWPOLYLINE":
                pts = [(float(p[0]), float(p[1])) for p in e.get_points("xy")]
                distinct = {(round(x, 9), round(y, 9)) for x, y in pts}
                if len(distinct) < 2:
                    findings.append(Finding(
                        "GEOM-ZERO-LENGTH", "error", handle,
                        "LWPOLYLINE has fewer than 2 distinct points (degenerate).", t, layer))
                elif not e.closed and len(distinct) >= 3:
                    gap = math.dist(pts[0], pts[-1])
                    if gap <= config.close_tolerance:
                        findings.append(Finding(
                            "PLINE-UNCLOSED", "warning", handle,
                            f"LWPOLYLINE is not flagged closed; endpoint gap is "
                            f"{gap:.4f} (close tolerance {config.close_tolerance}).", t, layer))

            elif t == "POLYLINE":
                pts = [(float(v.dxf.location.x), float(v.dxf.location.y)) for v in e.vertices]
                distinct = {(round(x, 9), round(y, 9)) for x, y in pts}
                if len(distinct) < 2:
                    findings.append(Finding(
                        "GEOM-ZERO-LENGTH", "error", handle,
                        "POLYLINE has fewer than 2 distinct vertices (degenerate).", t, layer))
                elif not e.is_closed and len(distinct) >= 3:
                    gap = math.dist(pts[0], pts[-1])
                    if gap <= config.close_tolerance:
                        findings.append(Finding(
                            "PLINE-UNCLOSED", "warning", handle,
                            f"POLYLINE is not flagged closed; endpoint gap is "
                            f"{gap:.4f} (close tolerance {config.close_tolerance}).", t, layer))

            elif t in ("CIRCLE", "ARC"):
                if float(e.dxf.radius) <= 0:
                    findings.append(Finding(
                        "GEOM-DEGENERATE", "error", handle,
                        f"{t} has non-positive radius ({e.dxf.radius}).", t, layer))

            elif t in ("TEXT", "MTEXT"):
                content = e.dxf.text if t == "TEXT" else e.text
                if not (content or "").strip():
                    findings.append(Finding(
                        "TEXT-EMPTY", "warning", handle,
                        f"{t} has no text content.", t, layer))
                if config.allowed_text_styles is not None:
                    style = dxf_get(e, "style", "Standard")
                    if style.upper() not in config.allowed_text_styles:
                        findings.append(Finding(
                            "STYLE-TEXT-NONSTD", "warning", handle,
                            f"{t} uses text style '{style}', which is not in the "
                            f"allowed style set.", t, layer))

            elif t == "DIMENSION":
                if config.allowed_dim_styles is not None:
                    style = dxf_get(e, "dimstyle", "Standard") or "Standard"
                    if style.upper() not in config.allowed_dim_styles:
                        findings.append(Finding(
                            "STYLE-DIM-NONSTD", "warning", handle,
                            f"DIMENSION uses dimension style '{style}', which is not "
                            f"in the allowed style set.", t, layer))

            elif t == "INSERT":
                attribs = list(e.attribs)
                tags = [a.dxf.tag for a in attribs]
                for tag, n in Counter(tags).items():
                    if n > 1:
                        findings.append(Finding(
                            "BLOCK-ATTR-DUP", "error", handle,
                            f"Insert of block '{e.dxf.name}' has {n} attributes "
                            f"with tag '{tag}'.", t, layer))
                for tag in config.required_attribs.get(e.dxf.name, []):
                    if tag not in tags:
                        findings.append(Finding(
                            "BLOCK-ATTR-MISSING", "error", handle,
                            f"Insert of block '{e.dxf.name}' is missing required "
                            f"attribute '{tag}'.", t, layer))
                for a in attribs:
                    if not (a.dxf.text or "").strip():
                        findings.append(Finding(
                            "BLOCK-ATTR-EMPTY", "warning", handle,
                            f"Attribute '{a.dxf.tag}' of block '{e.dxf.name}' "
                            f"insert is empty.", t, layer))

    # --- exact duplicate entities ----------------------------------------
    for extra, first in find_duplicates(doc, config):
        findings.append(Finding(
            "ENT-DUPLICATE", "warning", extra.dxf.handle,
            f"Exact duplicate of entity {first.dxf.handle} "
            f"({extra.dxftype()} on layer '{dxf_get(extra, 'layer', '0')}').",
            extra.dxftype(), dxf_get(extra, "layer", "0")))

    # --- unused styles -----------------------------------------------------
    for style in doc.styles:
        name = style.dxf.name
        if name.upper() in SYSTEM_STYLES or name.startswith("*"):
            continue
        if text_style_use.get(name, 0) == 0:
            findings.append(Finding(
                "STYLE-UNUSED", "info", None,
                f"Text style '{name}' is defined but never used."))
    for ds in doc.dimstyles:
        name = ds.dxf.name
        if name.upper() in SYSTEM_STYLES or "$" in name:
            continue
        if dim_style_use.get(name, 0) == 0:
            findings.append(Finding(
                "STYLE-UNUSED", "info", None,
                f"Dimension style '{name}' is defined but never used."))

    # --- topology checks (phase 2, Shapely) --------------------------------
    # Imported lazily: topology.py imports from this module, and a missing
    # Shapely install must degrade to "no topology findings", never break
    # the flat rule engine.
    try:
        from . import topology
        findings.extend(topology.run_topology_checks(doc, config))
    except ImportError:
        pass

    return findings
