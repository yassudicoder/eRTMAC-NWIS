"""
A trained predictive model for drilling hazards.

The problem statement asks for predictive analytics models that identify
potential drilling risks from historical offset-well behaviour. The rest of
the risk engine answers that with evidence and arithmetic: which offsets hit
what, how relevant they are, how well the depths correlate. That is
explainable and it is what a driller will act on, but it is a hand-tuned
score, not a model - its weights were chosen, not learned.

This module learns them instead. For every formation each well drilled, it
builds a feature vector from what could have been known *before* that section
was drilled, and fits a logistic regression to predict whether a given hazard
occurred. The two are then presented side by side: the evidence score says
"three relevant wells lost returns here"; the model says "given the offset
incidence, the depletion and the pressure margin, this interval carries a
0.63 probability of losses". When they disagree, that disagreement is itself
information.

Three things make this honest rather than decorative:

  * **No leakage.** A well's own events never enter its own features. The
    model only ever sees what the offsets knew.
  * **Leave-one-well-out validation.** Every prediction scored below was made
    by a model that had never seen that well. Wells are the unit of
    independence, not rows - two intervals in one well are not independent.
  * **Published metrics.** ROC AUC, Brier score and a calibration table, per
    hazard, reported by scripts/train_risk_model.py and served at
    /api/model/metrics. A model whose AUC is 0.55 should be seen to be 0.55.

Implemented in plain Python. The maths is a page long and the point is that
it is inspectable; pulling in scikit-learn would hide the one part of the
system a reviewer should be able to read end to end.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

from ..assam_geology import FORMATION_BY_NAME, haversine_km

# Hazards worth modelling: the ones the problem statement names, plus the two
# that dominate non-productive time in this section.
MODELLED_HAZARDS: tuple[str, ...] = (
    "MUD_LOSS",
    "STUCK_PIPE",
    "KICK",
    "WELLBORE_INSTABILITY",
    "CEMENTING_ISSUE",
    "HIGH_TORQUE_DRAG",
)

# How far to look for the offset evidence that becomes a feature.
NEIGHBOUR_RADIUS_KM = 20.0

FEATURE_NAMES: tuple[str, ...] = (
    "offset_incidence",        # relevance-weighted fraction of offsets that saw it
    "offset_severity",         # weighted mean severity when they did
    "offset_count_log",        # log1p of how many offsets saw it
    "nearest_offset_km",       # distance to the closest offset that saw it
    "formation_base_rate",     # field-wide prior for this hazard in this unit
    "depth_tvd_km",            # how deep the interval is
    "overbalance_sg",          # mud weight carried less current pore pressure
    "depletion_sg",            # reservoir pressure drop, 0 for non-reservoirs
    "frac_margin_sg",          # fracture gradient less expected ECD
    "is_reservoir",
    "max_inclination_norm",    # hole angle, 0..1
    "porosity_norm",           # 0 when not a reservoir
    "log_permeability",        # 0 when not a reservoir
)


# --------------------------------------------------------------------------
# Logistic regression
# --------------------------------------------------------------------------


@dataclass
class LogisticModel:
    """L2-regularised logistic regression fitted by gradient descent."""

    feature_names: tuple[str, ...]
    weights: list[float] = field(default_factory=list)
    bias: float = 0.0
    mean: list[float] = field(default_factory=list)
    scale: list[float] = field(default_factory=list)
    n_train: int = 0
    n_positive: int = 0

    # -- fitting -----------------------------------------------------------

    def fit(self, rows: Sequence[Sequence[float]], labels: Sequence[int],
            epochs: int = 600, lr: float = 0.35, l2: float = 0.015) -> "LogisticModel":
        n, d = len(rows), len(self.feature_names)
        self.n_train = n
        self.n_positive = sum(labels)
        if n == 0:
            self.weights = [0.0] * d
            self.bias = 0.0
            self.mean = [0.0] * d
            self.scale = [1.0] * d
            return self

        # Standardise, so one feature measured in kilometres does not
        # dominate one measured in specific gravity.
        self.mean = [sum(r[j] for r in rows) / n for j in range(d)]
        self.scale = []
        for j in range(d):
            var = sum((r[j] - self.mean[j]) ** 2 for r in rows) / max(1, n - 1)
            self.scale.append(math.sqrt(var) if var > 1e-12 else 1.0)
        x = [[(r[j] - self.mean[j]) / self.scale[j] for j in range(d)] for r in rows]

        self.weights = [0.0] * d
        # Start the bias at the log-odds of the base rate so the optimiser
        # does not spend its first epochs finding the prior.
        rate = min(max(self.n_positive / n, 1e-4), 1 - 1e-4)
        self.bias = math.log(rate / (1 - rate))

        for epoch in range(epochs):
            grad_w = [0.0] * d
            grad_b = 0.0
            for i in range(n):
                p = _sigmoid(self.bias + sum(self.weights[j] * x[i][j] for j in range(d)))
                err = p - labels[i]
                grad_b += err
                xi = x[i]
                for j in range(d):
                    grad_w[j] += err * xi[j]
            step = lr / (1.0 + epoch / 220.0)
            self.bias -= step * grad_b / n
            for j in range(d):
                self.weights[j] -= step * (grad_w[j] / n + l2 * self.weights[j])
        return self

    # -- use ---------------------------------------------------------------

    def predict(self, features: Sequence[float]) -> float:
        if not self.weights:
            return 0.0
        z = self.bias + sum(
            self.weights[j] * (features[j] - self.mean[j]) / self.scale[j]
            for j in range(len(self.weights))
        )
        return _sigmoid(z)

    def contributions(self, features: Sequence[float]) -> list[dict]:
        """
        Per-feature contribution to the log-odds for one prediction.

        A probability nobody can interrogate is not usable on a rig floor, so
        every prediction can be broken down the same way the evidence score
        can.
        """
        out = []
        for j, name in enumerate(self.feature_names):
            if j >= len(self.weights):
                break
            z = self.weights[j] * (features[j] - self.mean[j]) / self.scale[j]
            out.append({
                "feature": name,
                "value": round(features[j], 4),
                "contribution": round(z, 4),
            })
        out.sort(key=lambda c: -abs(c["contribution"]))
        return out

    def as_dict(self) -> dict:
        return {
            "feature_names": list(self.feature_names),
            "weights": [round(w, 6) for w in self.weights],
            "bias": round(self.bias, 6),
            "mean": [round(m, 6) for m in self.mean],
            "scale": [round(s, 6) for s in self.scale],
            "n_train": self.n_train,
            "n_positive": self.n_positive,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "LogisticModel":
        m = cls(feature_names=tuple(data["feature_names"]))
        m.weights = list(data["weights"])
        m.bias = data["bias"]
        m.mean = list(data["mean"])
        m.scale = list(data["scale"])
        m.n_train = data.get("n_train", 0)
        m.n_positive = data.get("n_positive", 0)
        return m


def _sigmoid(z: float) -> float:
    if z >= 0:
        return 1.0 / (1.0 + math.exp(-min(z, 60.0)))
    e = math.exp(max(z, -60.0))
    return e / (1.0 + e)


# --------------------------------------------------------------------------
# Feature construction
# --------------------------------------------------------------------------


@dataclass
class TrainingRow:
    well_id: str
    formation: str
    hazard: str
    features: list[float]
    label: int


def _weighted(values: Sequence[tuple[float, float]]) -> float:
    """Weighted mean of (value, weight) pairs."""
    total = sum(w for _, w in values)
    if total <= 0:
        return 0.0
    return sum(v * w for v, w in values) / total


def build_training_rows(store: Any, radius_km: float = NEIGHBOUR_RADIUS_KM
                        ) -> list[TrainingRow]:
    """
    One row per (well, formation, hazard).

    The features describe what was knowable *before* drilling that section:
    the offsets' experience, the regional prior, the pressure regime and the
    well's own geometry. The label is whether that hazard then occurred.
    """
    wells = store.wells()
    by_id = {w["well_id"]: w for w in wells}
    ids = [w["well_id"] for w in wells]

    tops_by_well = store.tops_bulk(ids)
    events_by_well = store.events_bulk(ids)
    reservoir_by_well = store.reservoir_bulk(ids)

    # Which (well, formation) pairs had which hazards.
    occurred: dict[tuple[str, str], set[str]] = {}
    severity: dict[tuple[str, str, str], int] = {}
    for wid, evs in events_by_well.items():
        for e in evs:
            fm = e.get("formation")
            if not fm:
                continue
            occurred.setdefault((wid, fm), set()).add(e["event_type"])
            key = (wid, fm, e["event_type"])
            severity[key] = max(severity.get(key, 0), e["severity"])

    # Field-wide prior: how often each hazard shows up in each formation,
    # counted over wells that actually penetrated it.
    penetrated: dict[str, int] = {}
    hazard_hits: dict[tuple[str, str], int] = {}
    for wid in ids:
        for t in tops_by_well.get(wid, []):
            fm = t["formation"]
            penetrated[fm] = penetrated.get(fm, 0) + 1
            for hz in occurred.get((wid, fm), ()):  # type: ignore[arg-type]
                hazard_hits[(fm, hz)] = hazard_hits.get((fm, hz), 0) + 1

    # Precompute neighbours once.
    neighbours: dict[str, list[tuple[str, float]]] = {}
    for a in wells:
        near = []
        for b in wells:
            if a["well_id"] == b["well_id"] or b["status"] != "Completed":
                continue
            d = haversine_km(a["bottom_lat"], a["bottom_lon"],
                             b["bottom_lat"], b["bottom_lon"])
            if d <= radius_km:
                near.append((b["well_id"], d))
        near.sort(key=lambda p: p[1])
        neighbours[a["well_id"]] = near

    rows: list[TrainingRow] = []
    for well in wells:
        wid = well["well_id"]
        res_by_fm = {r["formation"]: r for r in reservoir_by_well.get(wid, [])}
        for t in tops_by_well.get(wid, []):
            fm_name = t["formation"]
            formation = FORMATION_BY_NAME.get(fm_name)
            if formation is None:
                continue
            res = res_by_fm.get(fm_name)

            current_pp = res["current_pressure_sg"] if res else formation.pore_pressure_sg
            mud_weight = min(formation.pore_pressure_sg
                             + (0.06 if formation.pore_pressure_sg < 1.2 else 0.10),
                             formation.frac_gradient_sg - 0.04)

            base = [
                # placeholders for the four offset features, filled per hazard
                0.0, 0.0, 0.0, 0.0,
                0.0,                                            # base rate, per hazard
                t["top_tvd_m"] / 1000.0,
                mud_weight - current_pp,
                (res["depletion_sg"] if res else 0.0),
                formation.frac_gradient_sg - (mud_weight + 0.03),
                1.0 if formation.is_reservoir else 0.0,
                (well.get("max_inclination_deg") or 0.0) / 90.0,
                (res["porosity_pct"] / 30.0) if res else 0.0,
                math.log1p(res["permeability_md"]) if res else 0.0,
            ]

            for hazard in MODELLED_HAZARDS:
                # --- offset evidence, excluding this well entirely ---------
                hits: list[tuple[float, float]] = []   # (severity, weight)
                weights_total = 0.0
                weights_hit = 0.0
                nearest = radius_km
                count = 0
                for other_id, dist in neighbours[wid]:
                    if fm_name not in {x["formation"] for x in tops_by_well.get(other_id, [])}:
                        continue
                    w = math.exp(-dist / 6.0)
                    weights_total += w
                    if hazard in occurred.get((other_id, fm_name), ()):  # type: ignore[arg-type]
                        weights_hit += w
                        hits.append((severity.get((other_id, fm_name, hazard), 3), w))
                        nearest = min(nearest, dist)
                        count += 1

                incidence = (weights_hit / weights_total) if weights_total > 0 else 0.0

                # The field-wide prior must exclude this well, or the label
                # leaks back into its own feature. With 61 wells one row is
                # only 1/61 of the rate, but leakage is leakage: it would
                # inflate every AUC below by an amount nobody could quantify.
                prior_n = penetrated.get(fm_name, 0) - 1
                prior_hits = hazard_hits.get((fm_name, hazard), 0) - (
                    1 if hazard in occurred.get((wid, fm_name), ()) else 0)  # type: ignore[arg-type]
                prior = (prior_hits / prior_n) if prior_n > 0 else 0.0

                features = list(base)
                features[0] = incidence
                features[1] = _weighted(hits) / 5.0
                features[2] = math.log1p(count)
                features[3] = nearest / radius_km
                features[4] = prior

                rows.append(TrainingRow(
                    well_id=wid,
                    formation=fm_name,
                    hazard=hazard,
                    features=features,
                    label=1 if hazard in occurred.get((wid, fm_name), ()) else 0,  # type: ignore[arg-type]
                ))
    return rows


# --------------------------------------------------------------------------
# Metrics
# --------------------------------------------------------------------------


def roc_auc(scores: Sequence[float], labels: Sequence[int]) -> float:
    """
    Area under the ROC curve, by rank, with ties handled properly.

    0.5 means the model is no better than guessing; that is exactly what a
    reader needs to be able to see.
    """
    pairs = sorted(zip(scores, labels), key=lambda p: p[0])
    n_pos = sum(labels)
    n_neg = len(labels) - n_pos
    if n_pos == 0 or n_neg == 0:
        return float("nan")

    # Average ranks over ties.
    ranks = [0.0] * len(pairs)
    i = 0
    while i < len(pairs):
        j = i
        while j + 1 < len(pairs) and pairs[j + 1][0] == pairs[i][0]:
            j += 1
        avg = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[k] = avg
        i = j + 1

    rank_sum = sum(r for r, (_, y) in zip(ranks, pairs) if y == 1)
    return (rank_sum - n_pos * (n_pos + 1) / 2.0) / (n_pos * n_neg)


def brier_score(scores: Sequence[float], labels: Sequence[int]) -> float:
    if not scores:
        return float("nan")
    return sum((p - y) ** 2 for p, y in zip(scores, labels)) / len(scores)


def calibration_table(scores: Sequence[float], labels: Sequence[int],
                      bins: int = 5) -> list[dict]:
    """Predicted probability against observed rate, bucketed."""
    out = []
    for b in range(bins):
        lo, hi = b / bins, (b + 1) / bins
        sel = [(p, y) for p, y in zip(scores, labels)
               if (lo <= p < hi) or (b == bins - 1 and p == 1.0)]
        if not sel:
            out.append({"bin": f"{lo:.1f}-{hi:.1f}", "n": 0,
                        "mean_predicted": None, "observed_rate": None})
            continue
        out.append({
            "bin": f"{lo:.1f}-{hi:.1f}",
            "n": len(sel),
            "mean_predicted": round(sum(p for p, _ in sel) / len(sel), 3),
            "observed_rate": round(sum(y for _, y in sel) / len(sel), 3),
        })
    return out


# --------------------------------------------------------------------------
# Training and validation
# --------------------------------------------------------------------------


def train_and_validate(store: Any, radius_km: float = NEIGHBOUR_RADIUS_KM,
                       progress=None) -> dict:
    """
    Fit one model per hazard, and score it by leave-one-well-out validation.

    The held-out unit is the *well*, not the row. Two intervals in the same
    well share its location, its rig and its mud system, so holding out rows
    would let the model see almost everything about a well it is being tested
    on and would flatter the result substantially.
    """
    rows = build_training_rows(store, radius_km=radius_km)
    by_hazard: dict[str, list[TrainingRow]] = {}
    for r in rows:
        by_hazard.setdefault(r.hazard, []).append(r)

    report: dict[str, Any] = {
        "feature_names": list(FEATURE_NAMES),
        "radius_km": radius_km,
        "validation": "leave-one-well-out",
        "total_rows": len(rows),
        "hazards": {},
        "models": {},
    }

    for hazard, hrows in sorted(by_hazard.items()):
        if progress:
            progress(hazard)
        labels = [r.label for r in hrows]
        positives = sum(labels)
        if positives < 8 or positives == len(labels):
            report["hazards"][hazard] = {
                "rows": len(hrows), "positives": positives,
                "skipped": "too few positive examples to model honestly",
            }
            continue

        well_ids = sorted({r.well_id for r in hrows})
        oof_scores: list[float] = []
        oof_labels: list[int] = []
        for held_out in well_ids:
            train = [r for r in hrows if r.well_id != held_out]
            test = [r for r in hrows if r.well_id == held_out]
            if not test or not any(r.label for r in train):
                continue
            fold = LogisticModel(FEATURE_NAMES).fit(
                [r.features for r in train], [r.label for r in train])
            for r in test:
                oof_scores.append(fold.predict(r.features))
                oof_labels.append(r.label)

        # The shipped model is fitted on everything; the metrics above are
        # what it is honestly worth.
        final = LogisticModel(FEATURE_NAMES).fit(
            [r.features for r in hrows], labels)

        base_rate = positives / len(labels)
        report["hazards"][hazard] = {
            "rows": len(hrows),
            "positives": positives,
            "base_rate": round(base_rate, 4),
            "auc": round(roc_auc(oof_scores, oof_labels), 4),
            "brier": round(brier_score(oof_scores, oof_labels), 4),
            "brier_baseline": round(
                brier_score([base_rate] * len(oof_labels), oof_labels), 4),
            "calibration": calibration_table(oof_scores, oof_labels),
            "top_features": sorted(
                ({"feature": n, "weight": round(w, 4)}
                 for n, w in zip(FEATURE_NAMES, final.weights)),
                key=lambda f: -abs(f["weight"]))[:6],
        }
        report["models"][hazard] = final.as_dict()

    return report


# --------------------------------------------------------------------------
# Serving
# --------------------------------------------------------------------------


# Feature construction walks every well pair, so it is far too slow to redo
# on each request. The knowledge base does not change while the server is up,
# so the index is built once per (database, radius) and reused.
_FEATURE_INDEX_CACHE: dict[tuple[str, float], dict] = {}


def feature_index(store: Any, radius_km: float = NEIGHBOUR_RADIUS_KM) -> dict:
    """{(well_id, formation, hazard): features}, built once and cached."""
    key = (str(getattr(store, "db_path", "memory")), radius_km)
    cached = _FEATURE_INDEX_CACHE.get(key)
    if cached is None:
        cached = {
            (r.well_id, r.formation, r.hazard): r.features
            for r in build_training_rows(store, radius_km=radius_km)
        }
        _FEATURE_INDEX_CACHE[key] = cached
    return cached


def clear_feature_cache() -> None:
    _FEATURE_INDEX_CACHE.clear()


class RiskModelBundle:
    """The trained models, loaded from disk and ready to score with."""

    def __init__(self, report: dict):
        self.report = report
        self.models = {
            hazard: LogisticModel.from_dict(data)
            for hazard, data in report.get("models", {}).items()
        }
        self.radius_km = report.get("radius_km", NEIGHBOUR_RADIUS_KM)

    @classmethod
    def load(cls, path: Path) -> "RiskModelBundle | None":
        if not path.exists():
            return None
        try:
            return cls(json.loads(path.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, KeyError):
            return None

    def metrics(self) -> dict:
        return {k: v for k, v in self.report.items() if k != "models"}

    def score(self, hazard: str, features: Sequence[float]) -> dict | None:
        model = self.models.get(hazard)
        if model is None:
            return None
        p = model.predict(features)
        stats = self.report.get("hazards", {}).get(hazard, {})
        return {
            "hazard": hazard,
            "probability": round(p, 4),
            "base_rate": stats.get("base_rate"),
            "lift": round(p / stats["base_rate"], 2)
            if stats.get("base_rate") else None,
            "auc": stats.get("auc"),
            "drivers": model.contributions(features)[:4],
        }
