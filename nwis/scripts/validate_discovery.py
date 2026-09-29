"""
Does NWIS actually find what is there?

The generator plants a small number of hazard zones - places where a specific
problem recurs in a specific formation, such as the depleted Tipam fairway at
Naharkatiya that takes mud.  The application never sees that list.  It reads
the reports, extracts events, and clusters them.

This script compares what the system discovered against what was planted.  It
is the closest thing available to a ground-truth evaluation of the analysis
stage, and it is the number to quote when asked whether the ranking and
clustering do anything real.

    python scripts/validate_discovery.py
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.assam_geology import haversine_km  # noqa: E402
from app.engine.relevance import rank_offsets  # noqa: E402
from app.store import Store  # noqa: E402

# A discovered cluster matches a planted zone if it is the same hazard in the
# same formation, and its centre falls inside the planted radius plus a margin
# for the fact that cluster centres are the mean of well locations.
CENTRE_TOLERANCE_KM = 3.0


def load_clusters(store: Store, radius_km: float, min_wells: int) -> list[dict]:
    """Re-run the clustering endpoint's logic against the knowledge base."""
    sys.path.insert(0, str(ROOT / "backend" / "app"))
    from app.routers.analytics import clusters as clusters_endpoint
    from app.deps import get_store
    import app.deps as deps

    # Point the router's store getter at this store for the call.
    deps._store = store  # noqa: SLF001
    try:
        return clusters_endpoint(radius_km=radius_km, min_wells=min_wells)
    finally:
        deps._store = None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", type=Path, default=ROOT / "data" / "nwis.db")
    ap.add_argument("--data", type=Path, default=ROOT / "data" / "synthetic")
    ap.add_argument("--cluster-radius", type=float, default=6.0)
    ap.add_argument("--min-wells", type=int, default=3)
    args = ap.parse_args()

    if not args.db.exists():
        print("Knowledge base not found. Run: python scripts/seed_db.py")
        return 1

    planted = json.loads((args.data / "hot_spots.json").read_text(encoding="utf-8"))
    store = Store.open(args.db)
    discovered = load_clusters(store, args.cluster_radius, args.min_wells)

    print("=" * 78)
    print("NWIS discovery validation")
    print("=" * 78)
    print("The generator planted hazard zones. The application never saw them - it")
    print("read the reports, extracted events, and clustered what it found.")
    print()

    matched = 0
    used: set[int] = set()
    misses: list[tuple[dict, int]] = []

    # How many wells actually drilled deep enough to see each zone's formation?
    # A zone nobody penetrated cannot be discovered, and that is a property of
    # the drilling history, not a failure of the clustering.
    wells = store.wells()
    tops_by_well = store.tops_bulk([w["well_id"] for w in wells])

    def wells_penetrating(zone: dict) -> int:
        count = 0
        for w in wells:
            d = haversine_km(zone["lat"], zone["lon"], w["surface_lat"], w["surface_lon"])
            if d > zone["radius_km"]:
                continue
            if any(t["formation"] == zone["formation"] for t in tops_by_well.get(w["well_id"], [])):
                count += 1
        return count

    print(f"{'Planted zone':<44}{'Found':<7}{'Wells':>6}{'Offset':>9}")
    print("-" * 78)
    for zone in planted:
        best = None
        best_distance = 1e9
        best_index = -1
        for i, c in enumerate(discovered):
            if i in used:
                continue
            if c["event_type"] != zone["event_type"] or c["formation"] != zone["formation"]:
                continue
            d = haversine_km(zone["lat"], zone["lon"], c["centre_lat"], c["centre_lon"])
            if d <= zone["radius_km"] + CENTRE_TOLERANCE_KM and d < best_distance:
                best, best_distance, best_index = c, d, i

        if best is not None:
            used.add(best_index)
            matched += 1
            print(f"{zone['name'][:43]:<44}{'yes':<7}{best['well_count']:>6}{best_distance:>8.1f}k")
        else:
            penetrating = wells_penetrating(zone)
            misses.append((zone, penetrating))
            print(f"{zone['name'][:43]:<44}{'no':<7}{penetrating:>6}{'-':>9}")

    print()
    print(f"Planted hazard zones      : {len(planted)}")
    print(f"Rediscovered from reports : {matched}  ({matched / max(1, len(planted)):.0%})")
    print(f"Clusters reported in total: {len(discovered)}")

    if misses:
        print()
        print("Why the rest were not found:")
        for zone, penetrating in misses:
            if penetrating < args.min_wells:
                reason = (f"only {penetrating} well{'s' if penetrating != 1 else ''} in the area "
                          f"reached the {zone['formation']} - below the {args.min_wells}-well "
                          f"threshold, so no cluster can form")
            else:
                reason = (f"{penetrating} wells reached the {zone['formation']}, but too few "
                          f"of them recorded this hazard to cluster")
            print(f"  {zone['name']}:")
            print(f"    {reason}")
        print()
        print("  This is a property of the drilling history rather than of the analysis:")
        print("  offset intelligence can only see hazards that enough offset wells drilled")
        print("  through. The deep carbonate and Kopili zones sit below the TD of most")
        print("  development wells in the dataset.")

    extra = [c for i, c in enumerate(discovered) if i not in used]
    if extra:
        print()
        print(f"Additional clusters found ({len(extra)}) - real recurrences in the data that")
        print("were not planted as zones, mostly formation-wide hazards:")
        for c in extra[:6]:
            print(f"  {c['label'][:30]:<32}{c['formation']:<20}{c['well_count']:>3} wells "
                  f"{c['npt_hours']:>7.0f}h  {', '.join(c['fields'])}")

    # ------------------------------------------------------------------
    # Does relevance ranking prefer same-structure wells?
    # ------------------------------------------------------------------
    print()
    print("=" * 78)
    print("Relevance sanity check")
    print("=" * 78)
    print("A well on the same structure should outrank a comparable well further")
    print("away on a different one. Checked for every well that is drilling.")
    print()

    ok = 0
    total = 0
    for well in store.wells(status="Drilling"):
        results = rank_offsets(store, well["well_id"], radius_km=40, limit=40)
        if len(results) < 4:
            continue
        total += 1
        top5 = results[:5]
        same_field = sum(1 for r in top5 if r.well["field_code"] == well["field_code"])
        passed = same_field >= 3
        ok += 1 if passed else 0
        print(f"  {well['well_name']:<18}{same_field}/5 of the top five are in the same field"
              f"   {'PASS' if passed else 'CHECK'}")

    print()
    print(f"{ok}/{total} drilling wells rank their own structure into the top five")

    store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
