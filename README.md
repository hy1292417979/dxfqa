# dxfqa — DXF drawing QA + safe auto-fix

Check a DXF drawing against a standards profile before you issue it:
layer/style rules, geometry defects, block-attribute integrity, and
polygon topology (self-intersection, overlap, containment, duplicate
vertices). Findings are reported as JSON and a standalone HTML report.
A conservative auto-fixer repairs only the mechanically safe subset —
duplicates, unclosed polylines within tolerance, layer/style remapping
to your standard, purging unused layers/styles — and writes the result
to a **new** file. Your input file is never modified.

DXF only. No DWG input, no accounts, no billing, no uploads: everything
runs locally from the command line.

## Install

Requires Python 3.10+.

```bash
pip install .
# or, for development:
pip install -e ".[dev]"
```

This installs the `dxfqa` command. Library use works too:

```python
import ezdxf
from dxfqa.checker import CheckConfig, run_checks

doc = ezdxf.readfile("drawing.dxf")
findings = run_checks(doc, CheckConfig())
for f in findings:
    print(f.rule, f.severity, f.message)
```

## Quick start

```bash
dxfqa check drawing.dxf \
    --config profiles/survey_basic.json \
    --report report.html --json report.json \
    [--fix drawing_fixed.dxf] [--strict]
```

- `--fix` refuses to write over the input file (exit code 2).
- `--strict` exits 2 when any error-severity finding exists (CI-friendly).
- `--config` takes a JSON profile: which rules are active, allowed text /
  dimension styles, required block attributes, layer/style remap maps,
  and topology tolerances. See `profiles/survey_basic.json` and
  `profiles/remap_example.json`.

## What it checks

### Flat rules (12 rule families)

| Rule | Severity | Meaning |
|---|---|---|
| `LAYER-EMPTY` | info | layer defined, zero entities anywhere |
| `LAYER-DEFPOINTS` | error | entity on DEFPOINTS (does not plot) |
| `LAYER-ZERO` | warning | entity on layer "0" (configurable) |
| `GEOM-ZERO-LENGTH` | error | zero-length LINE; polyline with < 2 distinct points |
| `GEOM-DEGENERATE` | error | CIRCLE/ARC with non-positive radius |
| `PLINE-UNCLOSED` | warning | polyline not flagged closed, endpoint gap ≤ tolerance, ≥ 3 distinct points |
| `ENT-DUPLICATE` | warning | exact duplicate entity (type + layer + geometry signature) |
| `BLOCK-ATTR-MISSING` | error | INSERT missing a profile-required attribute tag |
| `BLOCK-ATTR-DUP` | error | INSERT with the same attribute tag twice |
| `BLOCK-ATTR-EMPTY` | warning | attribute with empty value |
| `TEXT-EMPTY` | warning | TEXT/MTEXT with no content |
| `STYLE-TEXT-NONSTD` / `STYLE-DIM-NONSTD` | warning | style outside the profile's allowed set |
| `STYLE-UNUSED` | info | text/dimension style defined but never used |

### Topology rules (Shapely, on closed polylines)

| Rule | Severity | Meaning |
|---|---|---|
| `TOPO-SELF-INTERSECT` | error | closed-polyline boundary self-intersects |
| `TOPO-OVERLAP` | error | two valid polygons **on the same layer** overlap beyond tolerance |
| `TOPO-CONTAINMENT` | error | polygon on the profile's `inner` layer is not fully inside any polygon on the `outer` layer |
| `TOPO-DUP-VERTEX` | warning | closed polyline has consecutive (incl. closing) vertex pairs within tolerance |

Computed polygon areas are reported as data in the report's
`polygon_areas` section, not as findings; invalid polygons report
`area: null`.

### Safe auto-fixes only

`FIX-DUP` (delete exact duplicates), `FIX-CLOSE` (snap + close within
tolerance), `FIX-REMAP-LAYER` / `FIX-REMAP-TEXT-STYLE` /
`FIX-REMAP-DIM-STYLE` (per-profile maps; missing targets are created,
copying colour/linetype/font from the source), `FIX-PURGE-LAYER` /
`FIX-PURGE-STYLE` (unused only; never "0", DEFPOINTS, or "Standard").

**Flagged but never auto-fixed, deliberately:** zero-length/degenerate
geometry, all topology findings, and all attribute problems. Removing or
reshaping geometry, and completing attributes, are human decisions.

## Measured results (honest numbers, not benchmarks-to-be)

All figures below are from real runs of this code, on synthetic fixtures
and a generated surveying-style corpus. They prove the engine on those
inputs; they do **not** prove behaviour on real production drawings,
whose rule tuning (tolerances, layer standards) will differ.

### Fixture suite — 26/26 detected, 16/16 fixed, 0 false positives

`pytest tests/ -q` → **22 passed**. The fixture harness
(`fixtures/make_fixtures.py`, ground truth in `manifest.json`) plants
known defects in 6 drawings:

```
fixture                      planted  found  fixes residual-fixable  verdict
clean.dxf                          0      0      0                0  OK
defective_layers.dxf               6      6      3                0  OK
defective_geometry.dxf             5      5      3                0  OK
defective_blocks_text.dxf          6      6      0                0  OK
defective_topology.dxf             6      6      0                0  OK
remap.dxf                          3      3     10                0  OK
TOTAL: planted=26 detected=26 fixes_applied=16 false_positives_on_clean=0
```

Re-checking fixed files shows 0 residual fixable findings, and input
files are verified byte-identical (SHA-256) before and after every run.

### Realistic surveying-style corpus — 23/23, 0 false positives

A generated 2,300-entity drawing (80 edge-sharing parcels, 60 buildings
inside parcels, contours, roads, 900 survey-point inserts, dimensions,
labels — 2,315 entities clean / 2,339 defective):

- **Defective variant: 23/23 planted defects detected, per-rule counts
  exact, 0 missed, 0 extra** (10 error / 11 warning / 2 info), including
  bowtie self-intersection, a 300 sq-unit parcel overlap, 2 containment
  violations, and duplicate-vertex cases.
- **Clean variant: 0 findings, 0 fixes** — including the trap classes:
  edge-sharing parcels (touch, not overlap), buildings inside parcels,
  legitimately open contours/roads, block-internal geometry on layer "0".
- **Fixes: 6/6 expected** (3 duplicate removals, 1 close, 1 layer purge,
  1 style purge); re-check of the fixed file: **17 residual findings,
  all flag-only by design, 0 residual fixable findings**.
- Corpus check + fix: ~1.2 s warmed, ~64 MB peak RSS.

### Performance — ~1.7 s / 76 MB per 10,000-entity drawing

Synthetic 10,000-entity drawing (600 parcels + 300 buildings + lines,
points, texts, circles; 1.6 MB DXF): load 0.59 s, check 0.46 s, fix
0.13 s, save 0.54 s — **total 1.72 s, peak RSS 76 MB**. Topology
findings: 0 on that drawing (600 edge-sharing parcels, 300 contained
buildings — no false positives at that scale either).

## Known limitations

- **DXF only.** No DWG input (would need an external converter).
- Duplicate detection is exact-signature only; near-duplicates are not
  detected. (Two identical parcels read as a 100% overlap instead.)
- `PLINE-UNCLOSED` only fires for gaps ≤ tolerance; deciding which
  polylines *must* close needs a profile rule, not geometry alone.
- Topology: no gap/sliver detection between adjacent polygons, bulge
  arcs approximated by chords, 2D only (Z ignored).
- Remapping: dimension-style targets are created with defaults (source
  dim-style variables are not copied); dimension render blocks are not
  regenerated after a remap; no attribute auto-completion.
- No XREF, paper-space-viewport, or title-block content checks.
- Performance measured at 10k entities / 1.6 MB only; very large files
  (>100 MB) have not been stress-tested.

## Demo

`demo/index.html` is a standalone static page (no tracking, no uploads)
showing a sample report from the corpus run above.

## Licence

MIT — see `LICENSE`. Note: the copyright-holder line in `LICENSE` is a
placeholder until the publisher fills it in before release.
