"""
Command-line walkthrough of what NWIS produces for a well that is drilling.

    python scripts/demo_lookahead.py                 # the first active well
    python scripts/demo_lookahead.py NHK-072 --ahead 400 --radius 20

Useful on its own, and useful as a check that the engines agree with the API.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.engine.relevance import RelevanceWeights, rank_offsets  # noqa: E402
from app.engine.risk import look_ahead  # noqa: E402
from app.store import Store  # noqa: E402


def rule(char: str = "-", width: int = 78) -> None:
    print(char * width)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("well_id", nargs="?", default=None)
    ap.add_argument("--db", type=Path, default=ROOT / "data" / "nwis.db")
    ap.add_argument("--ahead", type=float, default=300.0)
    ap.add_argument("--radius", type=float, default=15.0)
    ap.add_argument("--top", type=int, default=6)
    args = ap.parse_args()

    store = Store.open(args.db)
    if store.is_empty():
        print("Knowledge base is empty. Run: python scripts/seed_db.py")
        return 1

    well_id = args.well_id
    if not well_id:
        drilling = store.wells(status="Drilling")
        if not drilling:
            print("No wells are currently drilling.")
            return 1
        well_id = drilling[0]["well_id"]

    well = store.well(well_id)
    if well is None:
        print(f"Unknown well: {well_id}")
        return 1

    rule("=")
    print(f"NWIS look-ahead  |  {well['well_name']} ({well_id})  |  {well['field_name']} field")
    rule("=")
    bit = well.get("current_bit_md_m") or well["td_md_m"]
    print(f"Status          : {well['status']}")
    print(f"Rig             : {well['rig']}")
    print(f"Profile         : {well['well_type']}, target {well['target_formation']}")
    print(f"Bit depth       : {bit:,.0f} m MD")
    print(f"Planned TD      : {(well.get('planned_td_md_m') or well['td_md_m']):,.0f} m MD")
    print()

    # ---------------------------------------------------------------- offsets
    offsets = rank_offsets(store, well_id, radius_km=args.radius,
                           weights=RelevanceWeights(), limit=args.top)
    print(f"RELEVANT OFFSET WELLS  (within {args.radius:.0f} km, ranked by combined similarity)")
    rule()
    print(f"{'Well':<18}{'Score':>7}{'Band':>9}{'Dist':>8}{'Events':>8}  Why")
    for o in offsets:
        why = o.supports[0] if o.supports else ""
        print(f"{o.well['well_name']:<18}{o.score:>7.3f}{o.band:>9}"
              f"{o.distance_km:>7.1f}k{o.event_count:>8}  {why[:34]}")
    print()

    if offsets:
        best = offsets[0]
        print(f"Breakdown for the top-ranked well, {best.well['well_name']}:")
        rule()
        for d in best.dimensions:
            bar = "#" * int(round(d.score * 24))
            print(f"  {d.label:<26}{d.score:>6.2f}  w={d.weight:.2f}  {bar:<24} {d.detail}")
        if best.caveats:
            print("  Caveats:")
            for c in best.caveats:
                print(f"    - {c}")
        print()

    # ------------------------------------------------------------------ risk
    result = look_ahead(store, well_id, lookahead_m=args.ahead, radius_km=args.radius)
    if result is None:
        print("No look-ahead result.")
        return 1

    print(f"LOOK-AHEAD  {result.window_md[0]:,.0f} - {result.window_md[1]:,.0f} m MD "
          f"({result.window_tvd[0]:,.0f} - {result.window_tvd[1]:,.0f} m TVD)")
    rule()
    print(f"Offsets considered: {result.offsets_considered}, used: {result.offsets_used}")
    if result.formations_ahead:
        print("Formations in the interval:")
        for f in result.formations_ahead:
            where = f"top at {f['top_md_m']:,.0f} m MD" if f["top_md_m"] else "currently drilling"
            print(f"  - {f['formation']:<22}{where:<28}({f['source']})")
    print()

    if result.signals:
        print("LIVE SIGNALS FROM THE CURRENT WELL")
        rule()
        for s in result.signals:
            print(f"  [{s.strength:.2f}] {s.label}: {s.note}")
        print()

    if not result.alerts:
        print("No offset-well evidence of trouble in the interval ahead.")
        store.close()
        return 0

    print(f"RISK ALERTS ({len(result.alerts)})")
    rule("=")
    for alert in result.alerts:
        print(f"[{alert.risk_band.upper():<6}] {alert.label}   risk {alert.risk_score:.2f}  "
              f"confidence {alert.confidence:.2f}")
        print(f"          {alert.predicted_md_from:,.0f} - {alert.predicted_md_to:,.0f} m MD "
              f"({alert.metres_ahead:,.0f} m ahead of the bit)"
              + (f"  |  {alert.formation}" if alert.formation else ""))
        for reason in alert.why:
            print(f"     - {reason}")
        print(f"     Evidence: {alert.well_count} well{'s' if alert.well_count != 1 else ''}, {len(alert.contributing)} events, "
              f"{alert.evidence_count} source references")
        for c in alert.contributing[:3]:
            cite = c.citations[0] if c.citations else None
            where = f"{cite['document_id']} p{cite['page']}" if cite else "no citation"
            print(f"        {c.well_name:<16} sev {c.severity}/5  "
                  f"{c.offset_md_m:>8,.0f} m MD -> {c.projected_md_m:>8,.0f} m here   [{where}]")
            if cite:
                print(f"           \"{cite['snippet'][:88]}\"")
        print("     Recommended:")
        for action in alert.recommended_actions[:4]:
            print(f"        - {action}")
        print()

    store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
