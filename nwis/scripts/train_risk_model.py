"""
Train and validate the hazard prediction models.

    python scripts/train_risk_model.py

Fits one logistic regression per hazard over every (well, formation) interval
in the knowledge base, validates it by leave-one-well-out, and writes the
fitted models plus their metrics to data/risk_model.json.

The number that matters is the AUC, and it is measured on wells the model had
never seen. A hazard whose AUC comes out near 0.5 is reported as such rather
than quietly dropped - knowing that offset evidence does not predict bit
balling is a finding, not a failure.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.assam_geology import EVENT_LABELS  # noqa: E402
from app.engine.model import train_and_validate  # noqa: E402
from app.store import Store  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", type=Path, default=ROOT / "data" / "nwis.db")
    ap.add_argument("--out", type=Path, default=ROOT / "data" / "risk_model.json")
    ap.add_argument("--radius-km", type=float, default=20.0)
    args = ap.parse_args()

    if not args.db.exists():
        print("Knowledge base not found. Run: python scripts/seed_db.py")
        return 1

    store = Store.open(args.db)
    started = time.perf_counter()
    print("Building features and running leave-one-well-out validation...")
    report = train_and_validate(
        store, radius_km=args.radius_km,
        progress=lambda h: print(f"  fitting {h} ...", flush=True))
    store.close()

    report["trained_seconds"] = round(time.perf_counter() - started, 1)
    args.out.write_text(json.dumps(report, indent=2), encoding="utf-8")

    print()
    print("=" * 76)
    print("NWIS hazard prediction - leave-one-well-out validation")
    print("=" * 76)
    print(f"Training rows        : {report['total_rows']:,}")
    print(f"Features             : {len(report['feature_names'])}")
    print(f"Offset radius        : {report['radius_km']:.0f} km")
    print(f"Wall time            : {report['trained_seconds']}s")
    print()
    print(f"{'Hazard':<24}{'Rows':>7}{'Pos':>6}{'Base':>8}{'AUC':>8}"
          f"{'Brier':>9}{'vs base':>9}")
    print("-" * 76)

    for hazard, m in sorted(report["hazards"].items()):
        label = EVENT_LABELS.get(hazard, hazard).split(" / ")[0]
        if "skipped" in m:
            print(f"{label:<24}{m['rows']:>7}{m['positives']:>6}"
                  f"{'':>8}{'skipped':>8}   {m['skipped']}")
            continue
        improvement = m["brier_baseline"] - m["brier"]
        print(f"{label:<24}{m['rows']:>7}{m['positives']:>6}{m['base_rate']:>8.2f}"
              f"{m['auc']:>8.3f}{m['brier']:>9.4f}{improvement:>+9.4f}")

    print()
    print("AUC 0.5 = no better than chance. 'vs base' is the Brier improvement")
    print("over always predicting the base rate; positive means the model adds")
    print("information beyond the prior.")

    # Show what each model actually learned to key off.
    print()
    print("What the models key on (largest standardised weights):")
    print("-" * 76)
    for hazard, m in sorted(report["hazards"].items()):
        if "skipped" in m:
            continue
        label = EVENT_LABELS.get(hazard, hazard).split(" / ")[0]
        drivers = ", ".join(
            f"{f['feature']} {f['weight']:+.2f}" for f in m["top_features"][:4])
        print(f"  {label:<22}{drivers}")

    print()
    print("Written to:", args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
