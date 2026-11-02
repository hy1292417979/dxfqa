# dxfqa Premium Pack — US$15

The free [dxfqa](https://github.com/hy1292417979/dxfqa) checker, plus the
parts built for people who process drawings in batches. One-time payment,
no subscription, runs fully offline.

## What Premium adds over the free version

1. **Offline web UI** — upload a DXF in a browser, get the HTML report,
   download the fixed DXF and a PDF report. Batch mode: upload a ZIP
   (max 50 files / 20 MB) and get a per-file summary.
2. **Batch CLI** — check a whole folder or ZIP in one run; writes
   `summary.csv` plus per-file JSON/HTML reports and `*_fixed.dxf` files.
   A corrupt file fails only its own row.
3. **DXF → GeoJSON export** — closed polylines → Polygon, lines →
   LineString, circles → polygon, points/inserts → Point with block
   attributes. No CRS is invented (DXF has none); the output says so.
4. **Two extra profiles** — `preissue` (stricter pre-submission sweep,
   0.02 close tolerance, BUILDING-inside-PARCEL containment) and
   `cad_drafting` (general CAD, no surveying block requirements).
5. **Quickstart guide** — install, first check, batch run, web UI,
   GeoJSON export, and how to write your own profile.

The free engine inside is unchanged (MIT, same code as the public repo):
18 rules — layers/styles, geometry defects, block attributes, polygon
topology (self-intersection, overlap, containment, duplicate vertices) —
with safe auto-fix. Your original file is never modified; flagged
geometry is reported, never silently reshaped.

Measured on synthetic fixtures/corpus: 26/26 planted defects detected,
16/16 safe fixes, 0 false positives on clean files; 23/23 on a
2,315-entity surveying-style corpus. Those numbers are from synthetic
data, not your drawings.

## Honest limits

- DXF only; no DWG input.
- No XREF / paper-space / title-block content checks. Topology is 2D
  only; bulge arcs are chord-approximated.
- Requires Python 3.10+ and `pip install ezdxf shapely`.

## Price and how to buy — US$15, one time

Pay with either rail, then email your payment proof:

**Option A — USDT (BNB Smart Chain / BEP20)**
Send exactly US$15 in USDT on the **BEP20 (BNB Smart Chain)** network to:

```
0x6986A933C08DBaf6e5b5a2D713F8aB7785C68FC4
```

Sending on the wrong network can lose the funds — double-check it says
BEP20 / BNB Smart Chain before confirming.

**Option B — PayPal**
Send US$15 to `hy861969148@gmail.com` and put `dxfqa premium` in the note.

**Delivery:** email the transaction hash (for USDT) or the PayPal
transaction screenshot/ID to `hy861969148@gmail.com`. The pack
(`dxfqa-premium-0.2.0.zip`, ~32 KB) is delivered by email within 24 hours
of the payment being visible.

**Refund:** if the pack does not run in your environment and we cannot
get it running, you get a full refund to the same rail you paid with.

## Prefer a done-for-you check instead?

The same QA run as a service — send one drawing, get the report and the
fixed copy back — is listed on LaborX for US$15:
https://laborx.com/gigs/i-will-qa-your-dxf-drawing-before-you-issue-it-defects-flagged-safe-fixes-applied-125435
