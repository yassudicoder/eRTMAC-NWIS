"""
Tests for the NWIS engines.

Run with:  python -m pytest backend/tests -q
      or:  python backend/tests/test_nwis.py      (no pytest needed)

These cover the parts where a silent error would produce a plausible-looking
but wrong alert: depth mapping, relevance scoring, extraction, and the
look-ahead window.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.assam_geology import (  # noqa: E402
    STRATIGRAPHY,
    formation_at_depth,
    haversine_km,
    normalise_formation,
    predicted_column,
)
from app.engine.correlate import build_correlation, column_similarity  # noqa: E402
from app.engine.geometry import Trajectory  # noqa: E402
from app.engine.relevance import RelevanceWeights  # noqa: E402
from app.engine.signals import analyse  # noqa: E402
from app.ingest.extract import (  # noqa: E402
    Citation,
    ExtractedEvent,
    _classify,
    _extract_depths,
    _extract_magnitude,
    _infer_severity,
    merge_events,
)


# --------------------------------------------------------------------------
# Geology
# --------------------------------------------------------------------------


def test_formation_column_is_monotonic_everywhere():
    """Tops must never invert, or every depth lookup downstream is wrong."""
    for lat, lon in [(27.29, 95.34), (27.18, 94.92), (27.52, 95.47), (27.36, 95.32)]:
        column = predicted_column(lat, lon)
        depths = [top for _, top in column]
        assert depths == sorted(depths), f"inverted column at {lat},{lon}: {column}"
        assert len(column) == len(STRATIGRAPHY)


def test_structure_deepens_down_dip():
    """The shelf dips to the south-east, so tops must deepen that way."""
    north_west = dict(predicted_column(27.55, 94.95))["Barail Arenaceous"]
    south_east = dict(predicted_column(27.15, 95.60))["Barail Arenaceous"]
    assert south_east > north_west


def test_formation_alias_normalisation():
    assert normalise_formation("Tipam Sst.") == "Tipam Sandstone"
    assert normalise_formation("BARAIL ARENACEOUS") == "Barail Arenaceous"
    assert normalise_formation("Baragolai") == "Barail Arenaceous"
    assert normalise_formation("Dupi Tila") == "Namsang"
    assert normalise_formation("nothing at all") is None


def test_formation_at_depth():
    column = [("Dihing", 0.0), ("Namsang", 200.0), ("Girujan Clay", 600.0)]
    assert formation_at_depth(column, 50) == "Dihing"
    assert formation_at_depth(column, 200) == "Namsang"
    assert formation_at_depth(column, 5000) == "Girujan Clay"


def test_haversine_known_distance():
    # Duliajan to Naharkatiya is about 7.5 km.
    d = haversine_km(27.3600, 95.3200, 27.2930, 95.3360)
    assert 6.5 < d < 9.0


# --------------------------------------------------------------------------
# Trajectory
# --------------------------------------------------------------------------


def test_vertical_trajectory_md_equals_tvd():
    t = Trajectory(kop_md=None, build_rate=0, max_inc=0)
    for md in (0, 500, 2500, 4000):
        assert abs(t.tvd_at(md) - md) < 0.5


def test_deviated_trajectory_tvd_lags_md():
    t = Trajectory(kop_md=800, build_rate=2.5, max_inc=40, azimuth=90)
    assert abs(t.tvd_at(800) - 800) < 1.0        # vertical above the KOP
    assert t.tvd_at(3000) < 3000                  # deviated below it
    assert t.displacement_at(3000) > 500


def test_md_for_tvd_round_trips():
    t = Trajectory(kop_md=1000, build_rate=2.0, max_inc=35, azimuth=45)
    for tvd in (500, 1500, 2500, 3200):
        md = t.md_for_tvd(tvd)
        assert abs(t.tvd_at(md) - tvd) < 2.0, f"round trip failed at {tvd}"


# --------------------------------------------------------------------------
# Correlation
# --------------------------------------------------------------------------


def _tops(pairs):
    return [{"formation": n, "top_tvd_m": d, "top_md_m": d, "lithology": ""} for n, d in pairs]


def test_correlation_maps_through_a_constant_shift():
    """An offset 200 m downthrown should map depths 200 m deeper."""
    ref = _tops([("Girujan Clay", 600), ("Tipam Sandstone", 1200), ("Barail Coal Shale", 2500)])
    off = _tops([("Girujan Clay", 800), ("Tipam Sandstone", 1400), ("Barail Coal Shale", 2700)])
    corr = build_correlation("A", ref, "B", off)

    assert len(corr.ties) == 3
    assert abs(corr.mean_shift_m - 200) < 1
    assert corr.shift_spread_m < 1
    assert corr.quality > 0.4
    # A depth halfway between two ties maps with the same shift.
    assert abs(corr.to_target(1850) - 2050) < 5
    # And the inverse brings it back.
    assert abs(corr.to_reference(corr.to_target(1850)) - 1850) < 5


def test_correlation_handles_differential_thickening():
    ref = _tops([("Girujan Clay", 600), ("Tipam Sandstone", 1200), ("Barail Coal Shale", 2500)])
    off = _tops([("Girujan Clay", 620), ("Tipam Sandstone", 1400), ("Barail Coal Shale", 3100)])
    corr = build_correlation("A", ref, "B", off)
    # Tops still map exactly, even though the section between them thickens.
    assert abs(corr.to_target(1200) - 1400) < 1
    assert abs(corr.to_target(2500) - 3100) < 1
    # Quality drops because the shifts disagree.
    assert corr.shift_spread_m > 100
    assert corr.quality < 0.6


def test_correlation_with_no_shared_tops_is_identity():
    corr = build_correlation("A", _tops([("Dihing", 0)]), "B", _tops([("Langpar", 4000)]))
    assert corr.ties == []
    assert corr.quality == 0.0
    assert corr.to_target(1234) == 1234


def test_column_similarity_rewards_conformance():
    ref = _tops([("Girujan Clay", 600), ("Tipam Sandstone", 1200), ("Barail Coal Shale", 2500)])
    parallel = _tops([("Girujan Clay", 700), ("Tipam Sandstone", 1300), ("Barail Coal Shale", 2600)])
    erratic = _tops([("Girujan Clay", 700), ("Tipam Sandstone", 1900), ("Barail Coal Shale", 2550)])

    good, _ = column_similarity(ref, parallel)
    bad, _ = column_similarity(ref, erratic)
    assert good > bad
    assert good > 0.85


# --------------------------------------------------------------------------
# Extraction
# --------------------------------------------------------------------------


def test_classify_finds_the_right_hazard():
    assert _classify("Partial mud losses of 8.5 m3/hr observed")[0][0] == "MUD_LOSS"
    assert _classify("String became stuck at 2,270 m MD")[0][0] == "STUCK_PIPE"
    assert _classify("Well kicked at 3,100 m MD")[0][0] == "KICK"
    assert _classify("Excessive cavings over the shakers")[0][0] == "WELLBORE_INSTABILITY"


def test_kick_off_point_is_not_a_kick():
    """The classic false positive: a directional milestone, not well control."""
    assert _classify("Kick Off Point         : 1,841.8 m MD") == []


def test_depth_extraction_prefers_md_and_ignores_decoys():
    md, tvd, end = _extract_depths(
        "Partial mud losses at 1,432 m MD (1,398 m TVD) while drilling Tipam Sandstone.")
    assert md == 1432 and tvd == 1398

    # A free point is not the event depth.
    md, _, _ = _extract_depths("Pipe stuck at 730 m MD. Free point established at 611.9 m.")
    assert md == 730

    md, _, end = _extract_depths("Hole instability and sloughing from 2,640 m to 2,670 m MD.")
    assert md == 2640 and end == 2670


def test_magnitude_and_severity_inference():
    text = "Lost circulation at 4,100 m MD. Loss rate recorded as 52.0 m3/hr."
    mag = _extract_magnitude("MUD_LOSS", text)
    assert mag["loss_rate_m3_hr"] == 52.0
    assert _infer_severity("MUD_LOSS", text, mag) == 5

    text = "Seepage losses (3.1 m3/hr) noted at 900 m MD."
    mag = _extract_magnitude("MUD_LOSS", text)
    assert _infer_severity("MUD_LOSS", text, mag) == 2

    # Total losses are severity 5 even with no rate quoted.
    assert _infer_severity("MUD_LOSS", "Total loss of returns at 4,200 m MD.", {}) == 5


def test_event_and_remedy_in_one_sentence_still_yields_the_event():
    """
    Reports routinely state the problem and the fix in one sentence. Treating
    the whole line as a remedial continuation loses the event entirely, which
    is exactly what happened to four kicks before this was fixed.
    """
    from app.ingest.extract import _is_remedial, extract_events
    from app.ingest.ocr import DocumentText, Line

    text = ("Influx taken at 3,450 m MD in the Barail Arenaceous. "
            "Well shut in, SIDPP 73.2 ksc; killed by drillers method.")
    assert _is_remedial(text), "the line does contain a remedial cue"

    doc = DocumentText(
        document_id="NHK-070-WCR", source_path="", page_count=1,
        extraction_method="text",
        lines=[
            Line(1, 1, "5.4  Barail Arenaceous - 05-05-2022"),
            Line(1, 2, f"     {text}"),
        ],
    )
    events = extract_events(doc, "NHK-070", "WCR")
    assert len(events) == 1, f"expected the kick to survive, got {events}"

    kick = events[0]
    assert kick.event_type == "KICK"
    assert kick.md_m == 3450
    assert kick.formation == "Barail Arenaceous"
    assert kick.magnitude["sidpp_ksc"] == 73.2
    assert kick.severity == 5
    assert kick.remedial_action is not None


def test_pure_remedial_line_attaches_rather_than_creating_an_event():
    from app.ingest.extract import extract_events
    from app.ingest.ocr import DocumentText, Line

    doc = DocumentText(
        document_id="NHK-060-WCR", source_path="", page_count=1,
        extraction_method="text",
        lines=[
            Line(1, 1, "5.2  Barail Coal Shale - 07-06-2024"),
            Line(1, 2, "     Wellbore instability in Barail Coal Shale from 2,700 m MD. "
                       "Excessive cavings (9.9 m3) over the shakers."),
            Line(1, 3, "     Circulated hi-vis sweeps and back-reamed the interval before continuing."),
        ],
    )
    events = extract_events(doc, "NHK-060", "WCR")
    # The back-reaming line must not become a separate tight-hole event.
    assert len(events) == 1, f"remedial line created a spurious event: {events}"
    assert events[0].event_type == "WELLBORE_INSTABILITY"
    assert events[0].remedial_action is not None
    assert len(events[0].citations) == 2, "the remedial line should be kept as evidence"


def test_merge_pools_citations_and_raises_confidence():
    """The same event in three reports becomes one record with three sources."""
    def make(doc, conf):
        return ExtractedEvent(
            well_id="NHK-001", event_type="MUD_LOSS", severity=3, md_m=1500.0,
            tvd_m=1480.0, md_end_m=None, formation="Tipam Sandstone",
            event_date="2019-05-24", npt_hours=9.5, magnitude={"loss_rate_m3_hr": 8.5},
            confidence=conf,
            citations=[Citation(doc, doc.split("-")[-1], 2, 40, "Partial mud losses...")],
        )

    merged = merge_events([
        make("NHK-001-WCR", 0.80),
        make("NHK-001-DDR", 0.72),
        make("NHK-001-MUDLOG", 0.65),
    ])
    assert len(merged) == 1
    assert len(merged[0].citations) == 3
    assert merged[0].confidence > 0.80        # corroboration raised it
    assert merged[0].needs_review is False


def test_merge_keeps_events_far_apart_separate():
    def make(md):
        return ExtractedEvent(
            well_id="NHK-001", event_type="MUD_LOSS", severity=3, md_m=md, tvd_m=md,
            md_end_m=None, formation="Tipam Sandstone", event_date=None,
            npt_hours=None, magnitude={}, confidence=0.7, citations=[],
        )

    assert len(merge_events([make(1500.0), make(2400.0)])) == 2


# --------------------------------------------------------------------------
# Live signals
# --------------------------------------------------------------------------


def _log(n=30, **overrides):
    rows = []
    for i in range(n):
        md = 2000 + i * 10
        row = {
            "md_m": md, "tvd_m": md, "formation": "Barail Coal Shale",
            "rop_m_hr": 8.0, "wob_t": 14.0, "rpm": 110, "torque_knm": 12.0,
            "flow_lpm": 1800, "spp_ksc": 110.0, "mud_weight_sg": 1.24,
            "ecd_sg": 1.30, "gas_units": 20.0,
        }
        for key, fn in overrides.items():
            row[key] = fn(i)
        rows.append(row)
    return rows


def test_rising_torque_is_detected_and_implicates_sticking():
    signals = analyse(_log(torque_knm=lambda i: 12.0 + i * 0.35))
    torque = next(s for s in signals if s.key == "torque_rising")
    assert torque.strength > 0.5
    assert "STUCK_PIPE" in torque.implicates


def test_steady_parameters_raise_nothing():
    assert analyse(_log()) == []


def test_narrow_ecd_margin_implicates_losses():
    # Barail Coal Shale fractures near 1.70 sg.
    signals = analyse(_log(ecd_sg=lambda i: 1.64))
    ecd = next(s for s in signals if s.key == "ecd_margin_narrow")
    assert "MUD_LOSS" in ecd.implicates


# --------------------------------------------------------------------------
# Relevance weights
# --------------------------------------------------------------------------


def test_weights_normalise_to_one():
    w = RelevanceWeights(geology=2.0, distance=2.0).normalised()
    assert abs(sum(w.values()) - 1.0) < 1e-9
    assert abs(w["geology"] - w["distance"]) < 1e-9


def test_weights_from_partial_dict_keeps_defaults():
    w = RelevanceWeights.from_dict({"geology": 0.5})
    assert w.geology == 0.5
    assert w.distance == RelevanceWeights().distance


# --------------------------------------------------------------------------
# End-to-end, only if the knowledge base has been built
# --------------------------------------------------------------------------

DB = ROOT / "data" / "nwis.db"


def test_look_ahead_end_to_end():
    if not DB.exists():
        print("  (skipped: run scripts/seed_db.py first)")
        return

    from app.engine.risk import look_ahead
    from app.store import Store

    store = Store.open(DB)
    try:
        drilling = store.wells(status="Drilling")
        assert drilling, "the dataset should contain wells that are still drilling"

        well = drilling[0]
        result = look_ahead(store, well["well_id"], lookahead_m=400, radius_km=20)
        assert result is not None

        # The window starts at the bit and runs ahead, never behind.
        assert result.window_md[0] == result.bit_md_m
        assert result.window_md[1] > result.window_md[0]
        assert result.window_tvd[1] >= result.window_tvd[0]

        for alert in result.alerts:
            assert 0.0 <= alert.risk_score <= 1.0
            assert 0.0 <= alert.confidence <= 1.0
            assert alert.risk_band in {"High", "Medium", "Low"}
            # Alerts must sit inside the interval being analysed.
            assert alert.predicted_md_from >= result.window_md[0] - 1
            assert alert.predicted_md_to <= result.window_md[1] + 1
            # And every one must carry evidence.
            assert alert.contributing, f"{alert.alert_id} has no contributing events"
            assert alert.evidence_count > 0, f"{alert.alert_id} cites no source"
            assert alert.why, f"{alert.alert_id} has no explanation"
            for c in alert.contributing:
                assert c.citations, "a contributing event lost its citations"
    finally:
        store.close()


def test_every_stored_event_has_a_citation():
    if not DB.exists():
        print("  (skipped: run scripts/seed_db.py first)")
        return

    from app.store import Store

    store = Store.open(DB)
    try:
        orphans = store.conn.execute(
            "SELECT COUNT(*) FROM events e "
            "WHERE NOT EXISTS (SELECT 1 FROM citations c WHERE c.event_id = e.event_id)"
        ).fetchone()[0]
        assert orphans == 0, f"{orphans} events have no source reference"
    finally:
        store.close()


def test_offsets_are_ranked_and_explained():
    if not DB.exists():
        print("  (skipped: run scripts/seed_db.py first)")
        return

    from app.engine.relevance import rank_offsets
    from app.store import Store

    store = Store.open(DB)
    try:
        well = store.wells(status="Drilling")[0]
        results = rank_offsets(store, well["well_id"], radius_km=20, limit=10)
        assert results

        scores = [r.score for r in results]
        assert scores == sorted(scores, reverse=True), "results are not ranked"
        for r in results:
            assert 0.0 <= r.score <= 1.0
            assert len(r.dimensions) == 7
            assert abs(sum(d.weight for d in r.dimensions) - 1.0) < 1e-6
            assert abs(sum(d.contribution for d in r.dimensions) - r.score) < 1e-6
            assert r.supports or r.caveats, "a ranked well came back with no explanation"
    finally:
        store.close()


# --------------------------------------------------------------------------


def _main() -> int:
    """Minimal runner so the suite works without pytest installed."""
    tests = [(name, fn) for name, fn in sorted(globals().items())
             if name.startswith("test_") and callable(fn)]
    failures = []
    for name, fn in tests:
        try:
            fn()
            print(f"  PASS  {name}")
        except AssertionError as exc:
            failures.append((name, exc))
            print(f"  FAIL  {name}: {exc}")
        except Exception as exc:  # noqa: BLE001
            failures.append((name, exc))
            print(f"  ERROR {name}: {type(exc).__name__}: {exc}")

    print()
    print(f"{len(tests) - len(failures)}/{len(tests)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(_main())
