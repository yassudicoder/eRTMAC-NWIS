"""
Measure how well the NLP extractor reads the drilling reports.

The generator knows exactly which events it wrote into the corpus.  The
extractor never sees that list - it only reads the prose.  Comparing the two
gives an honest precision / recall figure for the information-extraction
stage, which is the number a reviewer should ask for.

    python scripts/score_extraction.py
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.ingest.pipeline import ingest_corpus  # noqa: E402

DEPTH_TOLERANCE_M = 30.0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", type=Path, default=ROOT / "data" / "synthetic")
    ap.add_argument("--json-out", type=Path, default=None)
    args = ap.parse_args()

    truth = json.loads((args.data / "ground_truth_events.json").read_text(encoding="utf-8"))
    result = ingest_corpus(args.data)

    # Index ground truth by well so matching stays cheap.
    truth_by_well: dict[str, list[dict]] = defaultdict(list)
    for t in truth:
        truth_by_well[t["well_id"]].append(dict(t, _matched=False))

    tp: list[tuple[dict, object]] = []
    fp = []
    for ev in result.events:
        candidates = [
            t for t in truth_by_well.get(ev.well_id, [])
            if not t["_matched"]
            and t["event_type"] == ev.event_type
            and ev.md_m is not None
            and abs(t["md_m"] - ev.md_m) <= DEPTH_TOLERANCE_M
        ]
        if candidates:
            best = min(candidates, key=lambda t: abs(t["md_m"] - (ev.md_m or 0)))
            best["_matched"] = True
            tp.append((best, ev))
        else:
            fp.append(ev)

    fn = [t for rows in truth_by_well.values() for t in rows if not t["_matched"]]

    precision = len(tp) / max(1, len(tp) + len(fp))
    recall = len(tp) / max(1, len(tp) + len(fn))
    f1 = 2 * precision * recall / max(1e-9, precision + recall)

    depth_errors = [abs(t["md_m"] - e.md_m) for t, e in tp if e.md_m is not None]
    sev_exact = sum(1 for t, e in tp if t["severity"] == e.severity)
    sev_close = sum(1 for t, e in tp if abs(t["severity"] - e.severity) <= 1)
    fm_ok = sum(1 for t, e in tp if e.formation == t["formation"])

    print("=" * 68)
    print("NWIS information-extraction scorecard")
    print("=" * 68)
    s = result.stats
    print(f"Documents ingested     : {s.documents}  ({s.pages} pages, {s.lines:,} lines)")
    print(f"Raw mentions found     : {s.raw_events}")
    print(f"After evidence merge   : {s.merged_events}")
    print(f"Flagged for review     : {s.needs_review}")
    print(f"Wall time              : {s.seconds} s")
    print()
    print(f"Ground-truth events    : {len(truth)}")
    print(f"True positives         : {len(tp)}")
    print(f"False positives        : {len(fp)}")
    print(f"False negatives        : {len(fn)}")
    print()
    print(f"Precision              : {precision:6.1%}")
    print(f"Recall                 : {recall:6.1%}")
    print(f"F1                     : {f1:6.1%}")
    print()
    if depth_errors:
        mae = sum(depth_errors) / len(depth_errors)
        print(f"Depth MAE (matched)    : {mae:5.2f} m")
    print(f"Severity exact match   : {sev_exact / max(1, len(tp)):6.1%}")
    print(f"Severity within +/-1   : {sev_close / max(1, len(tp)):6.1%}")
    print(f"Formation correct      : {fm_ok / max(1, len(tp)):6.1%}")
    print()

    per_type: dict[str, dict[str, int]] = defaultdict(lambda: {"tp": 0, "fp": 0, "fn": 0})
    for t, _ in tp:
        per_type[t["event_type"]]["tp"] += 1
    for e in fp:
        per_type[e.event_type]["fp"] += 1
    for t in fn:
        per_type[t["event_type"]]["fn"] += 1

    print(f"{'Event type':<24}{'TP':>5}{'FP':>5}{'FN':>5}{'Prec':>8}{'Rec':>8}")
    print("-" * 68)
    for name in sorted(per_type):
        c = per_type[name]
        p = c["tp"] / max(1, c["tp"] + c["fp"])
        r = c["tp"] / max(1, c["tp"] + c["fn"])
        print(f"{name:<24}{c['tp']:>5}{c['fp']:>5}{c['fn']:>5}{p:>8.1%}{r:>8.1%}")
    print()

    if fn[:5]:
        print("Sample misses (first 5):")
        for t in fn[:5]:
            print(f"  {t['well_id']:<10}{t['event_type']:<24}{t['md_m']:>9,.0f} m MD  {t['formation']}")
    if fp[:5]:
        print()
        print("Sample spurious extractions (first 5):")
        for e in fp[:5]:
            cite = e.citations[0] if e.citations else None
            where = f"{cite.document_id} p{cite.page}" if cite else "?"
            print(f"  {e.well_id:<10}{e.event_type:<24}{(e.md_m or 0):>9,.0f} m MD  {where}")
            if cite:
                print(f"      \"{cite.snippet[:96]}\"")

    if args.json_out:
        args.json_out.write_text(json.dumps({
            "precision": precision, "recall": recall, "f1": f1,
            "true_positives": len(tp), "false_positives": len(fp), "false_negatives": len(fn),
            "stats": result.stats.as_dict(),
        }, indent=2), encoding="utf-8")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
