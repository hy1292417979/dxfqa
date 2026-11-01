"""Auto-fix for the safely mechanical subset of findings.

Safety contract:
- Fixes are applied to an in-memory document; the caller saves the result
  to a NEW file. The input file is never touched by this module.
- The only geometry ever deleted is an exact duplicate (an identical copy
  remains in the drawing). Zero-length / degenerate entities are flagged
  by the checker but deliberately NOT auto-deleted: removing geometry is
  a human decision.
- Purges only remove layers/styles that have zero references anywhere in
  the document (recomputed after duplicate removal), and never touch the
  system layer "0", DEFPOINTS, the current layer, or "Standard" styles.
- Remapping (phase 2) only rewrites layer/style *references* named in the
  profile maps; geometry is never altered. Scope matches the checker's
  entity scope (layouts + named block definitions + INSERT attributes).
  Anonymous blocks (dimension render artifacts, *D*) are skipped: real
  CAD regenerates them, so a remapped dimension's source layer can
  survive the purge via its rendered block content — documented, not
  hidden.
- Every applied fix is returned as a FixRecord (the fix log).
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from .checker import (SYSTEM_LAYERS, SYSTEM_STYLES, CheckConfig,
                      check_containers, dxf_get, entity_signature, usage_counts)


@dataclass
class FixRecord:
    fix_id: str
    description: str
    handle: str | None = None

    def to_dict(self) -> dict:
        return {"fix_id": self.fix_id, "handle": self.handle,
                "description": self.description}


def _fix_duplicates(doc, config: CheckConfig, fixes: list[FixRecord]) -> None:
    seen: dict = {}
    for container in check_containers(doc):
        for e in list(container):  # snapshot: we mutate the container
            sig = entity_signature(e, config.dup_decimal)
            if sig is None:
                continue
            if sig in seen:
                handle = e.dxf.handle
                kept = seen[sig].dxf.handle
                e.destroy()
                fixes.append(FixRecord(
                    "FIX-DUP",
                    f"Removed duplicate {sig[0]} on layer '{sig[1]}' "
                    f"(kept entity {kept}).", handle))
            else:
                seen[sig] = e


def _fix_close_polylines(doc, config: CheckConfig, fixes: list[FixRecord]) -> None:
    for container in check_containers(doc):
        for e in container:
            t = e.dxftype()
            if t == "LWPOLYLINE" and not e.closed:
                pts = list(e.get_points("xyseb"))
                xy = [(float(p[0]), float(p[1])) for p in pts]
                distinct = {(round(x, 9), round(y, 9)) for x, y in xy}
                if len(distinct) < 3:
                    continue
                gap = math.dist(xy[0], xy[-1])
                if gap <= config.close_tolerance:
                    if gap > 0:
                        last = list(pts[-1])
                        last[0], last[1] = pts[0][0], pts[0][1]
                        pts[-1] = tuple(last)
                        e.set_points(pts)
                    e.close()
                    fixes.append(FixRecord(
                        "FIX-CLOSE",
                        f"Closed LWPOLYLINE by snapping endpoints "
                        f"(gap was {gap:.4f}).", e.dxf.handle))
            elif t == "POLYLINE" and not e.is_closed:
                verts = list(e.vertices)
                if len(verts) < 3:
                    continue
                gap = verts[0].dxf.location.distance(verts[-1].dxf.location)
                if gap <= config.close_tolerance:
                    if gap > 0:
                        verts[-1].dxf.location = verts[0].dxf.location
                    e.close()
                    fixes.append(FixRecord(
                        "FIX-CLOSE",
                        f"Closed POLYLINE by snapping endpoints "
                        f"(gap was {gap:.4f}).", e.dxf.handle))


def _fix_purges(doc, config: CheckConfig, fixes: list[FixRecord]) -> None:
    layer_use, text_style_use, dim_style_use = usage_counts(doc)
    try:
        current_layer = doc.header.get("$CLAYER")
    except Exception:
        current_layer = None

    for layer in list(doc.layers):
        name = layer.dxf.name
        if name.upper() in SYSTEM_LAYERS or name == current_layer:
            continue
        if layer_use.get(name, 0) == 0:
            try:
                doc.layers.remove(name)
                fixes.append(FixRecord(
                    "FIX-PURGE-LAYER", f"Purged unused layer '{name}'."))
            except Exception:
                pass  # referenced somewhere we do not scan; leave it alone

    for style in list(doc.styles):
        name = style.dxf.name
        if name.upper() in SYSTEM_STYLES or name.startswith("*"):
            continue
        if text_style_use.get(name, 0) == 0:
            try:
                doc.styles.remove(name)
                fixes.append(FixRecord(
                    "FIX-PURGE-STYLE", f"Purged unused text style '{name}'."))
            except Exception:
                pass

    for ds in list(doc.dimstyles):
        name = ds.dxf.name
        if name.upper() in SYSTEM_STYLES or "$" in name:
            continue
        if dim_style_use.get(name, 0) == 0:
            try:
                doc.dimstyles.remove(name)
                fixes.append(FixRecord(
                    "FIX-PURGE-STYLE", f"Purged unused dimension style '{name}'."))
            except Exception:
                pass


def _map_lookup(mapping: dict) -> dict:
    """Case-insensitive view of a remap profile section."""
    return {str(k).upper(): str(v) for k, v in mapping.items()}


def _actual_name(table, upper: str):
    """The table entry's real-cased name matching `upper`, or None."""
    for entry in table:
        if entry.dxf.name.upper() == upper:
            return entry.dxf.name
    return None


def _ensure_layer(doc, target: str, source: str | None) -> None:
    if _actual_name(doc.layers, target.upper()) is not None:
        return
    layer = doc.layers.add(target)
    if source is not None:
        src = doc.layers.get(source)
        for prop in ("color", "linetype"):
            try:
                setattr(layer.dxf, prop, getattr(src.dxf, prop))
            except Exception:
                pass


def _ensure_text_style(doc, target: str, source: str | None) -> None:
    if _actual_name(doc.styles, target.upper()) is not None:
        return
    font = "txt.shx"
    if source is not None:
        try:
            font = doc.styles.get(source).dxf.font or font
        except Exception:
            pass
    doc.styles.new(target, dxfattribs={"font": font})


def _ensure_dim_style(doc, target: str, source: str | None) -> None:
    if _actual_name(doc.dimstyles, target.upper()) is not None:
        return
    # A fresh dim style gets ezdxf defaults; copying a source style's
    # ~40 dimension variables is deliberately out of prototype scope.
    doc.dimstyles.new(target)


def _remap_one(e, layer_map, text_map, dim_map, fixes) -> None:
    """Apply any matching remap to a single entity (or ATTRIB)."""
    t = e.dxftype()
    handle = e.dxf.handle
    layer = dxf_get(e, "layer", None)
    if layer is not None and layer.upper() in layer_map:
        dst = layer_map[layer.upper()]
        e.dxf.layer = dst
        fixes.append(FixRecord(
            "FIX-REMAP-LAYER",
            f"Remapped layer '{layer}' -> '{dst}' for {t}.", handle))
    if t in ("TEXT", "MTEXT", "ATTRIB", "ATTDEF"):
        style = dxf_get(e, "style", None)
        if style is not None and style.upper() in text_map:
            dst = text_map[style.upper()]
            e.dxf.style = dst
            fixes.append(FixRecord(
                "FIX-REMAP-TEXT-STYLE",
                f"Remapped text style '{style}' -> '{dst}' for {t}.", handle))
    elif t == "DIMENSION":
        ds = dxf_get(e, "dimstyle", None)
        if ds is not None and ds.upper() in dim_map:
            dst = dim_map[ds.upper()]
            e.dxf.dimstyle = dst
            fixes.append(FixRecord(
                "FIX-REMAP-DIM-STYLE",
                f"Remapped dimension style '{ds}' -> '{dst}'.", handle))


def _fix_remap(doc, config: CheckConfig, fixes: list[FixRecord]) -> None:
    layer_map = _map_lookup(config.layer_map)
    text_map = _map_lookup(config.text_style_map)
    dim_map = _map_lookup(config.dim_style_map)
    if not (layer_map or text_map or dim_map):
        return
    # Create targets up-front so a chain (A->B, B->C) cannot cascade
    # within one pass: each entity is remapped once, from its original
    # value, because mappings are matched against the pre-pass value.
    for src_u, dst in layer_map.items():
        _ensure_layer(doc, dst, _actual_name(doc.layers, src_u))
    for src_u, dst in text_map.items():
        _ensure_text_style(doc, dst, _actual_name(doc.styles, src_u))
    for src_u, dst in dim_map.items():
        _ensure_dim_style(doc, dst, _actual_name(doc.dimstyles, src_u))

    for container in check_containers(doc):
        for e in container:
            _remap_one(e, layer_map, text_map, dim_map, fixes)
            if e.dxftype() == "INSERT":
                for a in e.attribs:
                    _remap_one(a, layer_map, text_map, dim_map, fixes)


def apply_fixes(doc, config: CheckConfig | None = None) -> list[FixRecord]:
    """Apply all safe fixes to `doc` in memory; return the fix log."""
    config = config or CheckConfig()
    fixes: list[FixRecord] = []
    _fix_duplicates(doc, config, fixes)
    _fix_close_polylines(doc, config, fixes)
    _fix_remap(doc, config, fixes)
    _fix_purges(doc, config, fixes)  # after deletions/remaps: usage is recomputed
    return fixes
