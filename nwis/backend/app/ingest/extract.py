"""
Information extraction over drilling reports.

Turns the prose of a WCR / DDR / mud log back into structured, citable
drilling events.  This is the step that makes historical knowledge
searchable: everything downstream - relevance ranking, correlation, risk
alerts - consumes the output of this module, never the generator's internal
state.

The approach is a rule-based cascade rather than a trained model, for three
reasons that matter in this domain:

  1. There is no labelled corpus of OIL drilling reports to train on.
  2. Drilling report language is highly formulaic, so patterns get most of
     the way there.
  3. Every extraction has to be explainable and traceable to a line on a
     page.  A rule tells you *why* it fired; an embedding does not.

The lexicon and matcher structure map one-to-one onto a spaCy ``Matcher``
pipeline, which is the production path once OIL-labelled data exists.  Keeping
it in plain Python means the prototype has no model download step.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, Sequence

from ..assam_geology import EVENT_TYPES, normalise_formation
from .ocr import DocumentText, Line

# --------------------------------------------------------------------------
# Lexicon
# --------------------------------------------------------------------------

# Each trigger carries a weight: how strongly it implies its event type.
# Multi-word triggers are checked before single words so that "lost
# circulation" beats a stray "circulation".
TRIGGERS: dict[str, list[tuple[str, float]]] = {
    "MUD_LOSS": [
        ("total loss of returns", 1.0), ("complete loss of circulation", 1.0),
        ("loss of returns", 0.95), ("lost circulation", 0.95),
        ("loss of circulation", 0.95), ("seepage loss", 0.9),
        ("partial mud loss", 0.95), ("mud loss", 0.9), ("mud losses", 0.9),
        ("losses of", 0.85), ("loss rate", 0.85), ("no returns", 0.9),
        ("returns reduced", 0.8), ("lcm pill", 0.6), ("taking whole mud", 0.85),
        ("losses", 0.6),
    ],
    "STUCK_PIPE": [
        ("differential sticking", 1.0), ("became stuck", 0.95),
        ("pipe stuck", 0.95), ("string stuck", 0.95), ("stuck pipe", 0.95),
        ("free point", 0.7), ("overpull", 0.65), ("jarring", 0.6),
        ("fishing assembly", 0.7), ("back-off", 0.6), ("stuck", 0.55),
    ],
    "KICK": [
        ("well kicked", 1.0), ("gas influx", 0.95), ("influx taken", 0.95),
        ("influx observed", 0.95), ("well control", 0.8), ("pit gain", 0.85),
        ("sidpp", 0.8), ("shut in", 0.6), ("drillers method", 0.7),
        ("kill mud", 0.7), ("influx", 0.7), ("kick", 0.7),
    ],
    "WELLBORE_INSTABILITY": [
        ("wellbore instability", 1.0), ("hole instability", 1.0),
        ("hole collapse", 0.95), ("sloughing", 0.9), ("cavings", 0.9),
        ("caving", 0.85), ("excessive cavings", 0.95), ("over the shakers", 0.6),
    ],
    "TIGHT_HOLE": [
        ("tight hole", 1.0), ("pack-off", 0.9), ("packoff", 0.9),
        ("reaming pass", 0.8), ("back-reamed", 0.7), ("tight spot", 0.9),
    ],
    "HIGH_TORQUE_DRAG": [
        ("torque and drag", 1.0), ("high torque", 0.95), ("peak torque", 0.9),
        ("torque increased", 0.9),
    ],
    "BIT_BALLING": [
        ("bit balling", 1.0), ("balling up", 0.95), ("bha balling", 0.95),
        ("balled", 0.7),
    ],
    "WASHOUT": [
        ("hole washout", 1.0), ("washout", 0.9), ("over gauge", 0.75),
        ("caliper", 0.5),
    ],
    "LOW_ROP": [
        ("low penetration rate", 1.0), ("rop dropped", 0.95),
        ("low rop", 0.95), ("very low penetration", 1.0),
    ],
    "CEMENTING_ISSUE": [
        ("while cementing", 1.0), ("cementing problem", 1.0),
        ("cement column fell short", 1.0), ("cementing issue", 1.0),
        ("losses observed while cementing", 1.0),
        ("cement slurry lost", 1.0), ("cement job", 0.9),
        ("top of cement", 0.95), ("cement bond log", 0.95), ("cbl/vdl", 0.95),
        ("cement channelling", 1.0), ("channelling behind", 1.0),
        ("annular gas migration", 1.0), ("gas migration through", 1.0),
        ("sustained casing pressure", 0.95),
        ("remedial squeeze", 1.0), ("cement squeeze", 1.0),
        ("failed acceptance", 0.9), ("poor bonding", 0.95),
        ("poor to fair bond", 0.95), ("bond index", 0.85),
    ],
    "EQUIPMENT_FAILURE": [
        ("pump failure", 1.0), ("motor failed", 0.95), ("top drive failure", 1.0),
        ("mwd failure", 0.95), ("twist off", 0.95), ("washed out string", 0.9),
    ],
}

# Phrases that mean "this did NOT happen" or "this is a recommendation for the
# future", which would otherwise fire a trigger.
NEGATION_CUES = (
    "no significant drilling problems",
    "without non-productive time",
    "no remedial action",
    "should plan for",
    "future wells",
    "no specific precautions",
    "recommendations for future",
    "no losses were",
    "not encountered",
)

# Phrases that veto one specific event type on a line.  Drilling reports are
# full of terms that collide with hazard vocabulary - "Kick Off Point" is a
# directional-drilling milestone, not a well-control event.
BLOCKERS: dict[str, tuple[str, ...]] = {
    # A bond log that came back good or fair is a record, not a problem.
    "CEMENTING_ISSUE": ("indicated good bond", "indicated fair bond",
                        "no cased-hole cement jobs"),
    "KICK": ("kick off point", "kick-off point", "kick off depth", "kop ", "kicked off at"),
    "MUD_LOSS": ("loss zone was not", "cement volume calculation"),
    "WASHOUT": ("washout tool", "for cement volume"),
}

# A remedial action describes what the crew did about an event that was
# already reported on a nearby line.  Treating these as fresh events would
# double-count the trouble; attaching them instead turns "what happened" into
# "what happened and what fixed it", which is what a driller actually wants.
REMEDIAL_CUES = (
    "lcm pill", "pumped", "spotted", "cement plug placed", "drilled out after",
    "worked and jarred", "jarring continued", "back-off performed",
    "fishing assembly", "fish recovered", "killed by drillers method",
    "circulated out the influx", "weighted up to", "circulated bottoms up",
    "mud weight raised", "raised mud weight", "increased kcl",
    "circulated hi-vis sweeps", "reamed and circulated", "back-reamed the interval",
    "worked the string", "added lubricant", "circulated sweeps",
    "pulled off bottom", "jetted the bit clean", "raised flow rate",
    "added detergent", "reduced flow rate", "noted for cement volume",
    "bit pulled and replaced", "changed wob", "cut mud weight",
    "continued drilling blind", "well shut in and killed", "inhibition increased",
    "no remedial action",
    # cementing remedials
    "cut slurry density", "losses were cured", "plug bumped",
    "top-up job", "temperature survey", "additional centralisers",
    "squeeze carried out", "squeeze performed", "annulus bled down",
    "re-run", "accepted after",
)

# How far below an event a remedial line can sit and still belong to it.
REMEDIAL_WINDOW_LINES = 4

# --------------------------------------------------------------------------
# Numeric patterns
# --------------------------------------------------------------------------

_NUM = r"(\d{1,3}(?:,\d{3})*(?:\.\d+)?|\d+(?:\.\d+)?)"

RE_MD = re.compile(rf"{_NUM}\s*m\s*MD", re.I)
RE_TVD = re.compile(rf"{_NUM}\s*m\s*TVD", re.I)
RE_RANGE = re.compile(rf"(?:from|between)\s+{_NUM}\s*m(?:\s*MD)?\s+(?:to|and)\s+{_NUM}\s*m", re.I)
RE_BARE_DEPTH = re.compile(rf"\bat\s+{_NUM}\s*m\b", re.I)

RE_LOSS_RATE = re.compile(rf"{_NUM}\s*m3\s*/\s*hr", re.I)
RE_PIT_GAIN = re.compile(rf"pit\s+gain\s+{_NUM}\s*m3", re.I)
RE_SIDPP = re.compile(rf"SIDPP\s+{_NUM}\s*ksc", re.I)
RE_GAS_UNITS = re.compile(rf"gas\s+units\s+rose\s+to\s+{_NUM}", re.I)
RE_OVERPULL = re.compile(rf"overpull\s+(?:of\s+)?{_NUM}\s*t\b", re.I)
RE_FREE_POINT = re.compile(rf"free\s+point\s+(?:established\s+at\s+)?{_NUM}\s*m", re.I)
RE_CAVING_VOL = re.compile(rf"\(?{_NUM}\s*m3\)?\s*(?:recovered|over the shakers)", re.I)
RE_REAMING = re.compile(rf"{_NUM}\s*(?:reaming passes|times)", re.I)
RE_TORQUE = re.compile(rf"(?:peak(?:ing at)?\s+torque\s+|peaking at\s+){_NUM}\s*kNm", re.I)
RE_SPP_RISE = re.compile(rf"(?:rose by|increase of)\s+{_NUM}\s*ksc", re.I)
RE_CALIPER = re.compile(rf"caliper\s+{_NUM}\s*percent", re.I)
RE_ROP_VALUE = re.compile(rf"{_NUM}\s*m\s*/\s*hr", re.I)
RE_NPT = re.compile(rf"NPT\s+{_NUM}\s*hrs?|non-productive time[^.]*?{_NUM}\s*hrs?", re.I)
RE_BOND_INDEX = re.compile(rf"bond\s+index\s+{_NUM}", re.I)
RE_PLANNED_TOC = re.compile(rf"planned\s+top\s+of\s+{_NUM}\s*m", re.I)
RE_ACTUAL_TOC = re.compile(rf"top\s+of\s+cement\s+(?:logged|at)[^.]*?{_NUM}\s*m", re.I)
RE_TOC_SHORTFALL = re.compile(rf"{_NUM}\s*m\s+below\s+the\s+planned\s+top", re.I)
RE_RETURNS = re.compile(r"returns\s+(full|partial|none)", re.I)
RE_CASING_SIZE = re.compile(r"(\d{1,2}(?:-\d/\d)?in(?:\s+\w+)?)\s+(?:casing|surface|intermediate|liner|conductor)", re.I)

RE_DDR_DATE = re.compile(r"DATE\s+(\d{2})-(\d{2})-(\d{4})", re.I)
RE_SECTION_DATE = re.compile(r"-\s*(\d{2})-(\d{2})-(\d{4})\s*$")

# Which cement-job failure a sentence is describing. One job can fail in more
# than one way at the same shoe depth - a bad bond AND a squeeze AND a short
# cement top - and they call for different actions, so they must not collapse
# into a single "cementing issue".
CEMENT_MODE_CUES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("LOSSES_DURING_CEMENTING", ("while cementing", "slurry lost", "returns lost",
                                 "partial returns lost")),
    ("REMEDIAL_SQUEEZE", ("remedial squeeze", "cement squeeze", "failed acceptance")),
    ("GAS_MIGRATION", ("gas migration", "sustained casing pressure")),
    ("CHANNELLING", ("channelling",)),
    ("TOC_BELOW_PLAN", ("top of cement", "cement column fell short",
                        "below the planned top")),
    ("POOR_BOND", ("poor bonding", "poor to fair bond", "bond index", "bond log")),
)


def cement_mode(text: str) -> str | None:
    low = text.lower()
    for mode, cues in CEMENT_MODE_CUES:
        if any(c in low for c in cues):
            return mode
    return None


# Depth mentions that are not the event depth.
DEPTH_DECOYS = ("free point", "toc", "cement top", "top of cement", "shoe",
                "total depth", "kick off", "planned top", "cement at")


def _to_float(raw: str) -> float:
    return float(raw.replace(",", ""))


# --------------------------------------------------------------------------
# Output type
# --------------------------------------------------------------------------


@dataclass
class Citation:
    """Where an extracted fact came from, precisely enough to open the page."""

    document_id: str
    doc_type: str
    page: int
    line_no: int
    snippet: str


@dataclass
class ExtractedLesson:
    """
    A lesson recorded in a report for the benefit of the next well.

    These are the most valuable sentences in a completion report and the
    easiest to lose: they are written in the future tense, so every
    negation rule built for "did this happen?" throws them away. They are
    what "institutional memory" actually means - a previous crew telling
    the next one what to expect and where.
    """

    well_id: str
    text: str
    formation: str | None
    md_m: float | None
    hazard_type: str | None
    confidence: float = 0.0
    citations: list[Citation] = field(default_factory=list)


@dataclass
class ExtractedEvent:
    well_id: str
    event_type: str
    severity: int
    md_m: float | None
    tvd_m: float | None
    md_end_m: float | None
    formation: str | None
    event_date: str | None
    npt_hours: float | None
    magnitude: dict[str, float] = field(default_factory=dict)
    confidence: float = 0.0
    citations: list[Citation] = field(default_factory=list)
    # What the crew actually did about it, lifted from the following lines.
    # This is what turns a warning into a recommendation.
    remedial_action: str | None = None
    # For cementing, which failure mode this is. Part of the identity of the
    # event, so two different failures on the same casing shoe stay separate.
    subtype: str | None = None
    # Set when the extractor is not confident enough to use the event without
    # a human looking at it.
    needs_review: bool = False

    def key(self) -> tuple:
        """Identity used for de-duplication across documents."""
        bucket = round((self.md_m or -1) / 25.0)
        return (self.well_id, self.event_type, self.subtype, bucket)


# --------------------------------------------------------------------------
# Extraction
# --------------------------------------------------------------------------


def _classify(text: str) -> list[tuple[str, float]]:
    """All event types triggered by a line, with their strongest weight."""
    low = text.lower()
    hits: dict[str, float] = {}
    for event_type, triggers in TRIGGERS.items():
        if any(block in low for block in BLOCKERS.get(event_type, ())):
            continue
        for phrase, weight in triggers:
            if phrase in low:
                hits[event_type] = max(hits.get(event_type, 0.0), weight)
                break
    return sorted(hits.items(), key=lambda kv: -kv[1])


def _is_negated(text: str) -> bool:
    low = text.lower()
    return any(cue in low for cue in NEGATION_CUES)


def _is_remedial(text: str) -> bool:
    low = text.lower()
    return any(cue in low for cue in REMEDIAL_CUES)


def _extract_depths(text: str) -> tuple[float | None, float | None, float | None]:
    """Return (md, tvd, md_end) for a line."""
    md = tvd = md_end = None

    rng = RE_RANGE.search(text)
    if rng:
        md = _to_float(rng.group(1))
        md_end = _to_float(rng.group(2))

    mds = RE_MD.findall(text)
    if mds and md is None:
        md = _to_float(mds[0])
    if len(mds) > 1 and md_end is None:
        md_end = _to_float(mds[1])

    tvds = RE_TVD.findall(text)
    if tvds:
        tvd = _to_float(tvds[0])

    if md is None:
        # Fall back to "at N m", but only when the sentence is not talking
        # about a free point, a casing shoe or a cement top.
        for m in RE_BARE_DEPTH.finditer(text):
            prefix = text[max(0, m.start() - 40):m.start()].lower()
            if any(d in prefix for d in DEPTH_DECOYS):
                continue
            md = _to_float(m.group(1))
            break

    if md is not None and md_end is not None and md_end < md:
        md, md_end = md_end, md
    return md, tvd, md_end


def _extract_magnitude(event_type: str, text: str) -> dict[str, float]:
    mag: dict[str, float] = {}

    def grab(rx: re.Pattern[str], key: str, group: int = 1) -> None:
        m = rx.search(text)
        if m:
            mag[key] = _to_float(m.group(group))

    if event_type == "MUD_LOSS":
        grab(RE_LOSS_RATE, "loss_rate_m3_hr")
    elif event_type == "KICK":
        grab(RE_PIT_GAIN, "pit_gain_m3")
        grab(RE_SIDPP, "sidpp_ksc")
        grab(RE_GAS_UNITS, "gas_units")
    elif event_type == "STUCK_PIPE":
        grab(RE_OVERPULL, "overpull_t")
        grab(RE_FREE_POINT, "free_point_m")
    elif event_type == "WELLBORE_INSTABILITY":
        grab(RE_CAVING_VOL, "caving_volume_m3")
    elif event_type == "TIGHT_HOLE":
        grab(RE_REAMING, "reaming_passes")
    elif event_type == "HIGH_TORQUE_DRAG":
        grab(RE_TORQUE, "peak_torque_kNm")
    elif event_type == "BIT_BALLING":
        grab(RE_SPP_RISE, "spp_rise_ksc")
    elif event_type == "WASHOUT":
        grab(RE_CALIPER, "caliper_excess_pct")
    elif event_type == "LOW_ROP":
        grab(RE_ROP_VALUE, "rop_m_hr")
    elif event_type == "CEMENTING_ISSUE":
        grab(RE_BOND_INDEX, "bond_index")
        grab(RE_PLANNED_TOC, "planned_toc_m")
        grab(RE_ACTUAL_TOC, "actual_toc_m")
        grab(RE_TOC_SHORTFALL, "toc_shortfall_m")
        m = RE_RETURNS.search(text)
        if m:
            # Keep the returns state as a number the risk engine can compare:
            # 0 = no returns, 1 = partial, 2 = full.
            mag["returns_level"] = {"none": 0.0, "partial": 1.0, "full": 2.0}[m.group(1).lower()]
    return mag


def _infer_severity(event_type: str, text: str, mag: dict[str, float]) -> int:
    """
    Map the reported magnitude onto the 1..5 severity scale the risk engine
    works in.  Thresholds follow normal Upper Assam operating practice.
    """
    low = text.lower()

    if event_type == "MUD_LOSS":
        if "total loss" in low or "no returns" in low or "complete loss" in low:
            return 5
        rate = mag.get("loss_rate_m3_hr")
        if rate is None:
            return 3
        if rate >= 40:
            return 5
        if rate >= 15:
            return 4
        if rate >= 5:
            return 3
        return 2

    if event_type == "KICK":
        gain = mag.get("pit_gain_m3", 0.0)
        sidpp = mag.get("sidpp_ksc", 0.0)
        if gain >= 6 or sidpp >= 45:
            return 5
        if gain >= 2.5 or sidpp >= 20:
            return 4
        return 3

    if event_type == "STUCK_PIPE":
        if "fishing" in low or "back-off" in low:
            return 5
        pull = mag.get("overpull_t", 0.0)
        if pull >= 60:
            return 5
        if pull >= 30:
            return 4
        return 3

    if event_type == "WELLBORE_INSTABILITY":
        vol = mag.get("caving_volume_m3", 0.0)
        if vol >= 12:
            return 5
        if vol >= 8:
            return 4
        if vol >= 3:
            return 3
        return 2

    if event_type == "TIGHT_HOLE":
        passes = mag.get("reaming_passes", 0.0)
        return 4 if passes >= 8 else 3

    if event_type == "HIGH_TORQUE_DRAG":
        torque = mag.get("peak_torque_kNm", 0.0)
        return 4 if torque >= 30 else 3

    if event_type == "WASHOUT":
        excess = mag.get("caliper_excess_pct", 0.0)
        return 3 if excess >= 50 else 2

    if event_type == "CEMENTING_ISSUE":
        returns = mag.get("returns_level")
        if "while cementing" in low or "slurry lost" in low:
            if returns == 0.0 or "returns none" in low:
                return 5
            return 4
        if "remedial squeeze" in low or "failed acceptance" in low:
            return 4
        if "gas migration" in low or "sustained casing pressure" in low:
            return 4
        bond = mag.get("bond_index")
        if bond is not None:
            return 4 if bond < 0.30 else 3
        if "top of cement" in low or "cement column fell short" in low:
            shortfall = mag.get("toc_shortfall_m")
            planned = mag.get("planned_toc_m")
            actual = mag.get("actual_toc_m")
            if shortfall is None and planned is not None and actual is not None:
                shortfall = abs(actual - planned)
            # How bad a short cement top is depends on the length of annulus
            # it was meant to cover, not on the metres alone. ``shoe_md`` is
            # supplied by the caller from the casing table when known.
            annulus = mag.get("annulus_m")
            if annulus and annulus > 0:
                return 4 if (shortfall or 0.0) >= 0.25 * annulus else 3
            return 4 if (shortfall or 0.0) >= 250 else 3
        if "channelling" in low:
            return 3
        return 3

    if event_type == "BIT_BALLING":
        return 3 if mag.get("spp_rise_ksc", 0.0) >= 35 else 2

    if event_type == "LOW_ROP":
        rop = mag.get("rop_m_hr")
        return 3 if rop is not None and rop <= 1.2 else 2

    return 2


def _formation_in(text: str) -> str | None:
    return normalise_formation(text)


def _npt_in(text: str) -> float | None:
    m = RE_NPT.search(text)
    if not m:
        return None
    raw = m.group(1) or m.group(2)
    return _to_float(raw) if raw else None


def _score_confidence(trigger_weight: float, md: float | None, formation: str | None,
                      mag: dict, date: str | None, ocr_confidence: float) -> float:
    score = 0.35 * trigger_weight
    if md is not None:
        score += 0.28
    if formation:
        score += 0.18
    if mag:
        score += 0.12
    if date:
        score += 0.07
    # A noisy OCR pass should drag the whole extraction down.
    score *= 0.55 + 0.45 * ocr_confidence
    return round(min(score, 0.99), 3)


def extract_events(doc: DocumentText, well_id: str, doc_type: str,
                   review_threshold: float = 0.55,
                   casing: Sequence["ExtractedCasing"] = ()) -> list[ExtractedEvent]:
    """
    Pull drilling events out of one document.

    Each line is classified independently, then context that lives on
    neighbouring lines (the DDR day date, the WCR subsection heading, the
    formation named one line up) is folded in.
    """
    events: list[ExtractedEvent] = []
    current_date: str | None = None
    current_formation: str | None = None
    # Sections whose grammar is the future tense are handled by
    # extract_lessons, not here. Relying on per-line negation cues is fragile
    # because a lesson can wrap onto a second line that carries no cue at all.
    in_forward_looking_section = False

    for idx, line in enumerate(doc.lines):
        text = line.text.strip()
        if not text:
            continue

        section = _SECTION_RE.match(line.text.rstrip())
        if section:
            in_forward_looking_section = _LESSONS_SECTION in section.group(2).upper()
            continue
        if in_forward_looking_section:
            if text.startswith(("Prepared by", "Reviewed by")):
                in_forward_looking_section = False
            continue

        # Track the running context a report carries in its structure.
        dm = RE_DDR_DATE.search(text)
        if dm:
            current_date = f"{dm.group(3)}-{dm.group(2)}-{dm.group(1)}"
        sm = RE_SECTION_DATE.search(text)
        if sm:
            current_date = f"{sm.group(3)}-{sm.group(2)}-{sm.group(1)}"
        if text.startswith("  Present Formation") or text.lower().startswith("present formation"):
            current_formation = normalise_formation(text.split(":", 1)[-1])
        heading_fm = _heading_formation(text)
        if heading_fm:
            current_formation = heading_fm

        if _is_negated(text):
            continue

        hits = _classify(text)
        own_md, own_tvd, own_end = _extract_depths(text)

        # A remedial line belongs to the event just above it, not to a new one
        # - unless it also reports an event of its own, at its own depth.
        # Reports routinely state both in one sentence: "Influx taken at
        # 3,450 m MD ... killed by drillers method." Swallowing that as a
        # remedy would lose the kick entirely.
        reports_its_own_event = bool(hits) and own_md is not None
        if events and _is_remedial(text) and not reports_its_own_event:
            previous = events[-1]
            last_line = previous.citations[-1].line_no if previous.citations else -99
            if line.line_no - last_line <= REMEDIAL_WINDOW_LINES:
                if previous.remedial_action is None:
                    previous.remedial_action = text
                previous.citations.append(Citation(
                    document_id=doc.document_id,
                    doc_type=doc_type,
                    page=line.page,
                    line_no=line.line_no,
                    snippet=text[:240],
                ))
                continue

        if not hits:
            continue
        event_type, weight = hits[0]

        md, tvd, md_end = own_md, own_tvd, own_end
        if event_type == "CEMENTING_ISSUE":
            # A cement job belongs to a casing shoe, and that is the depth it
            # has to be correlated at. Any depth quoted in the sentence is
            # something else - the cement top, or the planned top - so the
            # casing table wins whenever it can answer.
            shoe = resolve_casing_depth(text, casing)
            if shoe is not None:
                md, tvd, md_end = shoe, None, None
        if md is None:
            # An event with no depth cannot be correlated, so it is only worth
            # keeping if a neighbouring line supplies one.
            md, tvd, md_end = _extract_depths(doc.window(line.line_no, 1, 1))
        if md is None:
            continue

        formation = _formation_in(text) or current_formation
        mag = _extract_magnitude(event_type, text)
        if event_type == "CEMENTING_ISSUE" and md is not None and "planned_toc_m" in mag:
            mag["annulus_m"] = max(0.0, md - mag["planned_toc_m"])
        severity = _infer_severity(event_type, text, mag)
        npt = _npt_in(text) or _npt_in(doc.window(line.line_no, 0, 2))
        confidence = _score_confidence(weight, md, formation, mag,
                                       current_date, doc.ocr_confidence)

        events.append(ExtractedEvent(
            well_id=well_id,
            event_type=event_type,
            severity=severity,
            md_m=md,
            tvd_m=tvd,
            md_end_m=md_end,
            formation=formation,
            event_date=current_date,
            npt_hours=npt,
            magnitude=mag,
            confidence=confidence,
            # A sentence that reported both the event and what was done about
            # it carries its own remedy.
            remedial_action=text if _is_remedial(text) else None,
            subtype=cement_mode(text) if event_type == "CEMENTING_ISSUE" else None,
            needs_review=confidence < review_threshold,
            citations=[Citation(
                document_id=doc.document_id,
                doc_type=doc_type,
                page=line.page,
                line_no=line.line_no,
                snippet=text[:240],
            )],
        ))

    return events


# Report section headings we need to behave differently inside.
_SECTION_RE = re.compile(r"^\s*(\d+[A-Z]?)\.\s+([A-Z][A-Z \-/&]+)\s*$")
_LESSONS_SECTION = "LESSONS LEARNT"
_LESSON_BULLET_RE = re.compile(r"^\s*-\s+(.+)$")
# "... should plan for this around 1,400 m MD."
_LESSON_DEPTH_RE = re.compile(rf"around\s+{_NUM}\s*m\s*MD", re.I)

_HEADING_RE = re.compile(r"^\s*\d+\.\d+\s+(.+?)\s+-\s+\d{2}-\d{2}-\d{4}\s*$")


def _heading_formation(text: str) -> str | None:
    """WCR problem headings look like ``5.3  Girujan Clay - 07-06-2024``."""
    m = _HEADING_RE.match(text)
    return normalise_formation(m.group(1)) if m else None


@dataclass
class ExtractedCasing:
    """One casing string as recorded in the completion report."""

    well_id: str
    sequence: int
    hole_size_in: str
    casing_size_in: str
    shoe_md_m: float
    shoe_tvd_m: float
    cement_top_m: float
    citation: Citation | None = None


_CASING_SECTION = "CASING AND HOLE PROGRAMME"
_CASING_ROW_RE = re.compile(
    rf"^\s*(\S*in)\s+(\d[\d\-/]*in\s+[a-z]+)\s+{_NUM}\s+{_NUM}\s+{_NUM}\s*$", re.I)


def extract_casing_programme(doc: DocumentText, well_id: str,
                             doc_type: str) -> list[ExtractedCasing]:
    """
    Read the casing and hole programme table out of a completion report.

    This is a named requirement in its own right - the casing programme is one
    of the things the problem statement asks to be correlated across wells -
    and it is also what lets a cementing problem be given a depth. A cement
    bond log sentence says which string it ran on, never at what depth; the
    depth lives in this table, three pages earlier.
    """
    rows: list[ExtractedCasing] = []
    in_section = False
    seq = 0

    for line in doc.lines:
        section = _SECTION_RE.match(line.text.rstrip())
        if section:
            in_section = _CASING_SECTION in section.group(2).upper()
            continue
        if not in_section:
            continue

        m = _CASING_ROW_RE.match(line.text)
        if not m:
            continue
        seq += 1
        rows.append(ExtractedCasing(
            well_id=well_id,
            sequence=seq,
            hole_size_in=m.group(1).strip(),
            casing_size_in=m.group(2).strip(),
            shoe_md_m=_to_float(m.group(3)),
            shoe_tvd_m=_to_float(m.group(4)),
            cement_top_m=_to_float(m.group(5)),
            citation=Citation(
                document_id=doc.document_id,
                doc_type=doc_type,
                page=line.page,
                line_no=line.line_no,
                snippet=line.text.strip()[:240],
            ),
        ))
    return rows


def resolve_casing_depth(text: str, casing: Sequence[ExtractedCasing]) -> float | None:
    """Which casing string a sentence is about, and therefore at what depth."""
    low = text.lower()
    best: ExtractedCasing | None = None
    for row in casing:
        name = row.casing_size_in.lower()
        size = name.split()[0]           # e.g. "13-3/8in"
        if name in low or size in low:
            # Prefer the longest match, so "9-5/8in intermediate" beats "9-5/8in".
            if best is None or len(row.casing_size_in) > len(best.casing_size_in):
                best = row
    return best.shoe_md_m if best else None


def extract_lessons(doc: DocumentText, well_id: str, doc_type: str) -> list[ExtractedLesson]:
    """
    Pull the "lessons learnt and recommendations" section out of a report.

    This runs as a separate pass rather than inside ``extract_events`` because
    it needs the opposite rules. An event extractor asks "did this happen, and
    where?" and must reject the future tense. A lesson is *written* in the
    future tense - "future wells on this structure should plan for this around
    1,400 m MD" - so the section has to be recognised first and the negation
    rules suspended inside it.

    A lesson can run across more than one physical line, so bullets are
    accumulated until the next bullet or the end of the section.
    """
    lessons: list[ExtractedLesson] = []
    in_section = False
    buffer: list[str] = []
    buffer_line = 0
    buffer_page = 1

    def flush() -> None:
        nonlocal buffer
        if not buffer:
            return
        text = " ".join(part.strip() for part in buffer if part.strip())
        buffer = []
        if len(text) < 25:
            return
        formation = normalise_formation(text)
        depth_match = _LESSON_DEPTH_RE.search(text)
        md = _to_float(depth_match.group(1)) if depth_match else None
        hits = _classify(text)
        hazard = hits[0][0] if hits else None
        confidence = round(min(0.99, 0.45 + 0.2 * bool(formation) + 0.2 * (md is not None)
                               + 0.15 * bool(hazard)) * doc.ocr_confidence, 3)
        lessons.append(ExtractedLesson(
            well_id=well_id,
            text=text,
            formation=formation,
            md_m=md,
            hazard_type=hazard,
            confidence=confidence,
            citations=[Citation(
                document_id=doc.document_id,
                doc_type=doc_type,
                page=buffer_page,
                line_no=buffer_line,
                snippet=text[:240],
            )],
        ))

    for line in doc.lines:
        text = line.text.rstrip()
        heading = _SECTION_RE.match(text)
        if heading:
            flush()
            in_section = _LESSONS_SECTION in heading.group(2).upper()
            continue
        if not in_section:
            continue
        # The signature block ends the section.
        if text.strip().startswith(("Prepared by", "Reviewed by")):
            flush()
            in_section = False
            continue
        if set(text.strip()) <= {"-", "="} and text.strip():
            continue

        bullet = _LESSON_BULLET_RE.match(text)
        if bullet:
            flush()
            buffer = [bullet.group(1)]
            buffer_line = line.line_no
            buffer_page = line.page
        elif buffer and text.strip():
            buffer.append(text)

    flush()
    return lessons


def merge_events(events: Iterable[ExtractedEvent]) -> list[ExtractedEvent]:
    """
    Collapse the same physical event reported in several documents into one
    record carrying all of its citations.

    This is the "evidence aggregation" step: a mud loss that appears in the
    WCR problem list, the DDR for that day and the mud log becomes a single
    event a driller can trust, backed by three independent references.
    """
    merged: dict[tuple, ExtractedEvent] = {}
    for ev in events:
        key = ev.key()
        existing = merged.get(key)
        if existing is None:
            merged[key] = ev
            continue

        # Keep the richest version, then pool the evidence.
        if ev.confidence > existing.confidence:
            ev.citations = existing.citations + ev.citations
            # Do not lose fields the weaker extraction happened to have.
            ev.formation = ev.formation or existing.formation
            ev.tvd_m = ev.tvd_m or existing.tvd_m
            ev.md_end_m = ev.md_end_m or existing.md_end_m
            ev.npt_hours = ev.npt_hours or existing.npt_hours
            ev.event_date = ev.event_date or existing.event_date
            ev.magnitude = {**existing.magnitude, **ev.magnitude}
            ev.severity = max(ev.severity, existing.severity)
            ev.remedial_action = ev.remedial_action or existing.remedial_action
            ev.subtype = ev.subtype or existing.subtype
            merged[key] = ev
        else:
            existing.citations.extend(ev.citations)
            existing.remedial_action = existing.remedial_action or ev.remedial_action
            existing.subtype = existing.subtype or ev.subtype
            existing.formation = existing.formation or ev.formation
            existing.tvd_m = existing.tvd_m or ev.tvd_m
            existing.md_end_m = existing.md_end_m or ev.md_end_m
            existing.npt_hours = existing.npt_hours or ev.npt_hours
            existing.event_date = existing.event_date or ev.event_date
            existing.magnitude = {**ev.magnitude, **existing.magnitude}
            existing.severity = max(existing.severity, ev.severity)

    out = list(merged.values())
    # Corroboration across independent documents raises confidence.
    for ev in out:
        distinct_docs = {c.document_id for c in ev.citations}
        if len(distinct_docs) > 1:
            ev.confidence = round(min(0.99, ev.confidence + 0.06 * (len(distinct_docs) - 1)), 3)
            ev.needs_review = False
    out.sort(key=lambda e: (e.well_id, e.md_m or 0.0))
    return out


__all__ = [
    "Citation",
    "ExtractedEvent",
    "ExtractedCasing",
    "ExtractedLesson",
    "extract_casing_programme",
    "extract_events",
    "extract_lessons",
    "resolve_casing_depth",
    "merge_events",
    "EVENT_TYPES",
]
