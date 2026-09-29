"""
The well knowledge base.

One searchable store holding everything NWIS knows about every well: headers,
trajectory design, picked formation tops, the depth-indexed drilling log, the
events extracted from the reports, and the citation trail behind each event.

SQLite is used so the prototype runs with no database server.  The schema is
deliberately written to be portable: every table maps one-to-one onto the
PostgreSQL + PostGIS deployment described in docs/architecture.md, where
``wells`` gains ``geometry(Point, 4326)`` columns and the distance queries
move from Python into ``ST_DWithin``.
"""

from __future__ import annotations

import csv
import json
import sqlite3
import threading
from pathlib import Path
from typing import Any, Iterable, Sequence

SCHEMA = """
PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS wells (
    well_id                  TEXT PRIMARY KEY,
    well_name                TEXT NOT NULL,
    field_name               TEXT NOT NULL,
    field_code               TEXT NOT NULL,
    purpose                  TEXT,
    status                   TEXT,
    surface_lat              REAL NOT NULL,
    surface_lon              REAL NOT NULL,
    bottom_lat               REAL NOT NULL,
    bottom_lon               REAL NOT NULL,
    ground_level_m           REAL,
    spud_date                TEXT,
    completion_date          TEXT,
    rig                      TEXT,
    mud_system               TEXT,
    well_type                TEXT,
    kop_md_m                 REAL,
    build_rate_deg_30m       REAL,
    max_inclination_deg      REAL,
    azimuth_deg              REAL,
    td_md_m                  REAL,
    td_tvd_m                 REAL,
    horizontal_displacement_m REAL,
    target_formation         TEXT,
    days_drilled             INTEGER,
    total_npt_hours          REAL,
    current_bit_md_m         REAL,
    planned_td_md_m          REAL
);

CREATE TABLE IF NOT EXISTS formation_tops (
    well_id     TEXT NOT NULL REFERENCES wells(well_id),
    formation   TEXT NOT NULL,
    top_md_m    REAL NOT NULL,
    top_tvd_m   REAL NOT NULL,
    lithology   TEXT,
    PRIMARY KEY (well_id, formation)
);

CREATE TABLE IF NOT EXISTS events (
    event_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    well_id         TEXT NOT NULL REFERENCES wells(well_id),
    event_type      TEXT NOT NULL,
    severity        INTEGER NOT NULL,
    md_m            REAL,
    tvd_m           REAL,
    md_end_m        REAL,
    formation       TEXT,
    event_date      TEXT,
    npt_hours       REAL,
    magnitude_json  TEXT,
    confidence      REAL,
    subtype         TEXT,
    remedial_action TEXT,
    needs_review    INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS citations (
    citation_id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id    INTEGER NOT NULL REFERENCES events(event_id),
    document_id TEXT NOT NULL,
    doc_type    TEXT,
    page        INTEGER,
    line_no     INTEGER,
    snippet     TEXT
);

CREATE TABLE IF NOT EXISTS documents (
    document_id TEXT PRIMARY KEY,
    well_id     TEXT NOT NULL REFERENCES wells(well_id),
    doc_type    TEXT NOT NULL,
    title       TEXT,
    filename    TEXT,
    pages       INTEGER,
    characters  INTEGER
);

CREATE TABLE IF NOT EXISTS drilling_log (
    well_id          TEXT NOT NULL REFERENCES wells(well_id),
    md_m             REAL NOT NULL,
    tvd_m            REAL NOT NULL,
    formation        TEXT,
    inclination_deg  REAL,
    rop_m_hr         REAL,
    wob_t            REAL,
    rpm              REAL,
    torque_knm       REAL,
    flow_lpm         REAL,
    spp_ksc          REAL,
    mud_weight_sg    REAL,
    ecd_sg           REAL,
    gas_units        REAL,
    PRIMARY KEY (well_id, md_m)
);

CREATE TABLE IF NOT EXISTS casing_strings (
    well_id        TEXT NOT NULL REFERENCES wells(well_id),
    sequence       INTEGER NOT NULL,
    hole_size_in   TEXT,
    casing_size_in TEXT,
    shoe_md_m      REAL,
    shoe_tvd_m     REAL,
    cement_top_m   REAL,
    source         TEXT,        -- extracted | database
    document_id    TEXT,
    page           INTEGER,
    line_no        INTEGER,
    PRIMARY KEY (well_id, sequence)
);

CREATE TABLE IF NOT EXISTS cement_jobs (
    well_id          TEXT NOT NULL REFERENCES wells(well_id),
    casing_size_in   TEXT NOT NULL,
    hole_size_in     TEXT,
    shoe_md_m        REAL,
    shoe_tvd_m       REAL,
    planned_toc_m    REAL,
    actual_toc_m     REAL,
    slurry_volume_m3 REAL,
    excess_pct       REAL,
    returns          TEXT,
    bond_index       REAL,
    bond_quality     TEXT,
    job_date         TEXT,
    issues_json      TEXT,
    PRIMARY KEY (well_id, casing_size_in)
);

CREATE TABLE IF NOT EXISTS reservoir_intervals (
    well_id              TEXT NOT NULL REFERENCES wells(well_id),
    formation            TEXT NOT NULL,
    fluid                TEXT,
    top_md_m             REAL,
    base_md_m            REAL,
    top_tvd_m            REAL,
    base_tvd_m           REAL,
    gross_thickness_m    REAL,
    net_pay_m            REAL,
    net_to_gross         REAL,
    porosity_pct         REAL,
    permeability_md      REAL,
    water_saturation_pct REAL,
    virgin_pressure_sg   REAL,
    current_pressure_sg  REAL,
    depletion_sg         REAL,
    temperature_c        REAL,
    PRIMARY KEY (well_id, formation)
);

CREATE TABLE IF NOT EXISTS lessons (
    lesson_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    well_id     TEXT NOT NULL REFERENCES wells(well_id),
    text        TEXT NOT NULL,
    formation   TEXT,
    md_m        REAL,
    hazard_type TEXT,
    confidence  REAL,
    document_id TEXT,
    doc_type    TEXT,
    page        INTEGER,
    line_no     INTEGER
);

CREATE TABLE IF NOT EXISTS ingest_runs (
    run_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT,
    stats_json TEXT
);

CREATE INDEX IF NOT EXISTS idx_events_well     ON events(well_id);
CREATE INDEX IF NOT EXISTS idx_events_type     ON events(event_type);
CREATE INDEX IF NOT EXISTS idx_events_fm       ON events(formation);
CREATE INDEX IF NOT EXISTS idx_events_tvd      ON events(tvd_m);
CREATE INDEX IF NOT EXISTS idx_tops_well       ON formation_tops(well_id);
CREATE INDEX IF NOT EXISTS idx_log_well        ON drilling_log(well_id, md_m);
CREATE INDEX IF NOT EXISTS idx_citations_event ON citations(event_id);
CREATE INDEX IF NOT EXISTS idx_wells_field     ON wells(field_code);
CREATE INDEX IF NOT EXISTS idx_lessons_well    ON lessons(well_id);
CREATE INDEX IF NOT EXISTS idx_lessons_fm      ON lessons(formation);
CREATE INDEX IF NOT EXISTS idx_res_well        ON reservoir_intervals(well_id);
CREATE INDEX IF NOT EXISTS idx_cement_well     ON cement_jobs(well_id);

-- Full-text index across everything a drilling engineer might search for:
-- the prose of every report line, every extracted event, and every lesson.
-- ``kind`` and ``ref_id`` say what a hit is and how to open it.
CREATE VIRTUAL TABLE IF NOT EXISTS search_index USING fts5(
    kind UNINDEXED,
    ref_id UNINDEXED,
    well_id,
    title,
    body,
    tokenize = 'porter unicode61'
);
"""

WELL_COLUMNS = (
    "well_id", "well_name", "field_name", "field_code", "purpose", "status",
    "surface_lat", "surface_lon", "bottom_lat", "bottom_lon", "ground_level_m",
    "spud_date", "completion_date", "rig", "mud_system", "well_type",
    "kop_md_m", "build_rate_deg_30m", "max_inclination_deg", "azimuth_deg",
    "td_md_m", "td_tvd_m", "horizontal_displacement_m", "target_formation",
    "days_drilled", "total_npt_hours", "current_bit_md_m", "planned_td_md_m",
)


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {k: row[k] for k in row.keys()}


class Store:
    """
    Read/write access to the well knowledge base.

    Connections are thread-local.  FastAPI runs synchronous endpoints in a
    worker threadpool, and a single ``sqlite3.Connection`` shared across those
    threads fails under concurrent requests, so each thread gets its own
    connection to the same file.  WAL mode lets those connections read while
    another writes.
    """

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._local = threading.local()
        self._all_connections: list[sqlite3.Connection] = []
        self._lock = threading.Lock()

    @property
    def conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(str(self.db_path), timeout=15.0)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            self._local.conn = conn
            with self._lock:
                self._all_connections.append(conn)
        return conn

    # -- lifecycle ---------------------------------------------------------

    @classmethod
    def open(cls, db_path: Path) -> "Store":
        store = cls(Path(db_path))
        store.conn.executescript(SCHEMA)
        store.conn.commit()
        return store

    def close(self) -> None:
        with self._lock:
            for conn in self._all_connections:
                try:
                    conn.close()
                except sqlite3.Error:
                    pass
            self._all_connections.clear()
        self._local = threading.local()

    def is_empty(self) -> bool:
        return self.conn.execute("SELECT COUNT(*) FROM wells").fetchone()[0] == 0

    # -- ingestion ---------------------------------------------------------

    def load_structured_sources(self, data_dir: Path) -> None:
        """
        Load the sources that already arrive structured: the drilling
        database (well headers), the geological database (formation tops) and
        the eRTMAC parameter logs.
        """
        wells = json.loads((data_dir / "wells.json").read_text(encoding="utf-8"))
        tops = json.loads((data_dir / "formation_tops.json").read_text(encoding="utf-8"))
        docs = json.loads((data_dir / "documents_index.json").read_text(encoding="utf-8"))

        cur = self.conn.cursor()
        cur.executemany(
            f"INSERT OR REPLACE INTO wells ({','.join(WELL_COLUMNS)}) "
            f"VALUES ({','.join('?' * len(WELL_COLUMNS))})",
            [tuple(w.get(c) for c in WELL_COLUMNS) for w in wells],
        )
        cur.executemany(
            "INSERT OR REPLACE INTO formation_tops "
            "(well_id, formation, top_md_m, top_tvd_m, lithology) VALUES (?,?,?,?,?)",
            [(wid, t["formation"], t["top_md_m"], t["top_tvd_m"], t["lithology"])
             for wid, rows in tops.items() for t in rows],
        )
        cur.executemany(
            "INSERT OR REPLACE INTO documents "
            "(document_id, well_id, doc_type, title, filename, pages, characters) "
            "VALUES (?,?,?,?,?,?,?)",
            [(d["document_id"], d["well_id"], d["doc_type"], d["title"],
              d["filename"], d["pages"], d["characters"]) for d in docs],
        )

        log_dir = data_dir / "logs"
        for well in wells:
            path = log_dir / f"{well['well_id']}_drilling.csv"
            if not path.exists():
                continue
            with path.open(newline="", encoding="utf-8") as fh:
                rows = [
                    (well["well_id"], float(r["md_m"]), float(r["tvd_m"]), r["formation"],
                     float(r["inclination_deg"]), float(r["rop_m_hr"]), float(r["wob_t"]),
                     float(r["rpm"]), float(r["torque_kNm"]), float(r["flow_lpm"]),
                     float(r["spp_ksc"]), float(r["mud_weight_sg"]), float(r["ecd_sg"]),
                     float(r["gas_units"]))
                    for r in csv.DictReader(fh)
                ]
            cur.executemany(
                "INSERT OR REPLACE INTO drilling_log (well_id, md_m, tvd_m, formation, "
                "inclination_deg, rop_m_hr, wob_t, rpm, torque_knm, flow_lpm, spp_ksc, "
                "mud_weight_sg, ecd_sg, gas_units) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                rows,
            )
        self.conn.commit()

    def load_reservoir_and_cementing(self, data_dir: Path) -> None:
        """
        Load the reservoir and cementing records.

        These are the "reservoir characteristics" and "cementing practices"
        the problem statement asks to be correlated across wells. They arrive
        structured, the way a petrophysics and a cementing database would hand
        them over; the *narrative* of what went wrong still comes out of the
        reports through the NLP pipeline.
        """
        cur = self.conn.cursor()

        res_path = data_dir / "reservoir_intervals.json"
        if res_path.exists():
            rows = json.loads(res_path.read_text(encoding="utf-8"))
            cols = ("well_id", "formation", "fluid", "top_md_m", "base_md_m",
                    "top_tvd_m", "base_tvd_m", "gross_thickness_m", "net_pay_m",
                    "net_to_gross", "porosity_pct", "permeability_md",
                    "water_saturation_pct", "virgin_pressure_sg",
                    "current_pressure_sg", "depletion_sg", "temperature_c")
            cur.executemany(
                f"INSERT OR REPLACE INTO reservoir_intervals ({','.join(cols)}) "
                f"VALUES ({','.join('?' * len(cols))})",
                [tuple(r.get(c) for c in cols) for r in rows])

        cem_path = data_dir / "cement_jobs.json"
        if cem_path.exists():
            rows = json.loads(cem_path.read_text(encoding="utf-8"))
            cur.executemany(
                "INSERT OR REPLACE INTO cement_jobs (well_id, casing_size_in, hole_size_in, "
                "shoe_md_m, shoe_tvd_m, planned_toc_m, actual_toc_m, slurry_volume_m3, "
                "excess_pct, returns, bond_index, bond_quality, job_date, issues_json) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                [(r["well_id"], r["casing_size_in"], r["hole_size_in"], r["shoe_md_m"],
                  r["shoe_tvd_m"], r["planned_toc_m"], r["actual_toc_m"],
                  r["slurry_volume_m3"], r["excess_pct"], r["returns"], r["bond_index"],
                  r["bond_quality"], r["job_date"], json.dumps(r["issues"]))
                 for r in rows])

        self.conn.commit()

    def load_casing(self, extracted: Sequence[Any], data_dir: Path | None = None) -> int:
        """
        Store the casing programme.

        Preference is given to strings read out of the completion reports,
        because that proves the document pipeline handles tabular data as well
        as prose. Wells with no completion report yet - the ones still
        drilling - fall back to the drilling database.
        """
        cur = self.conn.cursor()
        cur.execute("DELETE FROM casing_strings")
        seen: set[str] = set()
        for c in extracted:
            cite = c.citation
            cur.execute(
                "INSERT OR REPLACE INTO casing_strings (well_id, sequence, hole_size_in, "
                "casing_size_in, shoe_md_m, shoe_tvd_m, cement_top_m, source, "
                "document_id, page, line_no) VALUES (?,?,?,?,?,?,?,'extracted',?,?,?)",
                (c.well_id, c.sequence, c.hole_size_in, c.casing_size_in, c.shoe_md_m,
                 c.shoe_tvd_m, c.cement_top_m,
                 cite.document_id if cite else None,
                 cite.page if cite else None,
                 cite.line_no if cite else None))
            seen.add(c.well_id)

        if data_dir is not None:
            wells = json.loads((data_dir / "wells.json").read_text(encoding="utf-8"))
            for w in wells:
                if w["well_id"] in seen:
                    continue
                for i, cp in enumerate(w.get("casing_scheme") or [], start=1):
                    cur.execute(
                        "INSERT OR REPLACE INTO casing_strings (well_id, sequence, "
                        "hole_size_in, casing_size_in, shoe_md_m, shoe_tvd_m, "
                        "cement_top_m, source) VALUES (?,?,?,?,?,?,?,'database')",
                        (w["well_id"], i, cp["hole_size_in"], cp["casing_size_in"],
                         cp["shoe_md_m"], cp["shoe_tvd_m"], cp["cement_top_m"]))
        self.conn.commit()
        return cur.execute("SELECT COUNT(*) FROM casing_strings").fetchone()[0]

    def load_lessons(self, lessons: Sequence[Any]) -> None:
        """Persist the lessons-learnt records with their citations."""
        cur = self.conn.cursor()
        cur.execute("DELETE FROM lessons")
        for lesson in lessons:
            cite = lesson.citations[0] if lesson.citations else None
            cur.execute(
                "INSERT INTO lessons (well_id, text, formation, md_m, hazard_type, "
                "confidence, document_id, doc_type, page, line_no) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (lesson.well_id, lesson.text, lesson.formation, lesson.md_m,
                 lesson.hazard_type, lesson.confidence,
                 cite.document_id if cite else None, cite.doc_type if cite else None,
                 cite.page if cite else None, cite.line_no if cite else None))
        self.conn.commit()

    def build_search_index(self, data_dir: Path) -> int:
        """
        Build the full-text index over the whole knowledge base.

        Three kinds of thing are indexed, because an engineer searching for
        "differential sticking in the Tipam" should find the structured event,
        the lesson another crew wrote about it, and the raw report line both
        came from.
        """
        from .ingest.ocr import read_document

        cur = self.conn.cursor()
        cur.execute("DELETE FROM search_index")
        rows: list[tuple] = []

        for e in self.events():
            title = f"{e['event_type'].replace('_', ' ').title()} - {e.get('formation') or ''}"
            parts = [
                e["event_type"].replace("_", " "),
                e.get("formation") or "",
                f"{e.get('md_m') or 0:.0f} m MD",
                e.get("remedial_action") or "",
            ]
            parts.extend(c["snippet"] for c in self.citations(e["event_id"]))
            rows.append(("event", str(e["event_id"]), e["well_id"], title,
                         " ".join(x for x in parts if x)))

        for lesson in self.conn.execute("SELECT * FROM lessons"):
            rows.append(("lesson", str(lesson["lesson_id"]), lesson["well_id"],
                         f"Lesson - {lesson['formation'] or ''}", lesson["text"]))

        for doc in self.documents():
            path = data_dir / "documents" / doc["filename"]
            if not path.exists():
                continue
            text = read_document(path, doc["document_id"])
            for line in text.lines:
                body = line.text.strip()
                if len(body) < 12:
                    continue
                rows.append((
                    "document", f"{doc['document_id']}#{line.page}#{line.line_no}",
                    doc["well_id"], doc["title"], body,
                ))

        cur.executemany(
            "INSERT INTO search_index (kind, ref_id, well_id, title, body) "
            "VALUES (?,?,?,?,?)", rows)
        self.conn.commit()
        return len(rows)

    def search(self, query: str, kind: str | None = None, well_id: str | None = None,
               limit: int = 40) -> list[dict]:
        """
        Full-text search across events, lessons and report text.

        Returns snippets with the matched terms marked, ranked by BM25 and
        weighted so that a structured event or a recorded lesson outranks the
        raw line it was read from.
        """
        if not query.strip():
            return []
        sql = [
            "SELECT kind, ref_id, well_id, title,",
            "       snippet(search_index, 4, '<<', '>>', ' ... ', 18) AS excerpt,",
            "       bm25(search_index, 0.0, 0.0, 1.0, 4.0, 8.0) AS score",
            "FROM search_index WHERE search_index MATCH ?",
        ]
        args: list[Any] = [query]
        if kind:
            sql.append("AND kind = ?")
            args.append(kind)
        if well_id:
            sql.append("AND well_id = ?")
            args.append(well_id)
        sql.append("ORDER BY CASE kind WHEN 'event' THEN 0 WHEN 'lesson' THEN 1 "
                   "ELSE 2 END, score LIMIT ?")
        args.append(limit)
        try:
            return [_row_to_dict(r) for r in self.conn.execute(" ".join(sql), args)]
        except sqlite3.OperationalError as exc:
            # A malformed FTS query (stray quote, bare operator) is the
            # caller's mistake, not a server fault.
            raise ValueError(f"Invalid search query: {exc}") from exc

    # -- casing, cementing, reservoir, lessons -----------------------------

    def casing(self, well_id: str) -> list[dict]:
        return [_row_to_dict(r) for r in self.conn.execute(
            "SELECT * FROM casing_strings WHERE well_id = ? ORDER BY sequence", (well_id,))]

    def casing_bulk(self, well_ids: Iterable[str]) -> dict[str, list[dict]]:
        ids = list(well_ids)
        if not ids:
            return {}
        ph = ",".join("?" * len(ids))
        out: dict[str, list[dict]] = {i: [] for i in ids}
        for r in self.conn.execute(
            f"SELECT * FROM casing_strings WHERE well_id IN ({ph}) ORDER BY sequence", ids
        ):
            out[r["well_id"]].append(_row_to_dict(r))
        return out

    def cement_jobs(self, well_id: str) -> list[dict]:
        rows = [_row_to_dict(r) for r in self.conn.execute(
            "SELECT * FROM cement_jobs WHERE well_id = ? ORDER BY shoe_md_m", (well_id,))]
        for r in rows:
            r["issues"] = json.loads(r.pop("issues_json") or "[]")
        return rows

    def reservoir(self, well_id: str) -> list[dict]:
        return [_row_to_dict(r) for r in self.conn.execute(
            "SELECT * FROM reservoir_intervals WHERE well_id = ? ORDER BY top_tvd_m",
            (well_id,))]

    def reservoir_bulk(self, well_ids: Iterable[str]) -> dict[str, list[dict]]:
        ids = list(well_ids)
        if not ids:
            return {}
        ph = ",".join("?" * len(ids))
        out: dict[str, list[dict]] = {i: [] for i in ids}
        for r in self.conn.execute(
            f"SELECT * FROM reservoir_intervals WHERE well_id IN ({ph}) "
            f"ORDER BY top_tvd_m", ids
        ):
            out[r["well_id"]].append(_row_to_dict(r))
        return out

    def lessons(self, well_id: str | None = None, formation: str | None = None,
                hazard_type: str | None = None, limit: int = 200) -> list[dict]:
        sql = "SELECT * FROM lessons WHERE 1=1"
        args: list[Any] = []
        for col, val in (("well_id", well_id), ("formation", formation),
                         ("hazard_type", hazard_type)):
            if val:
                sql += f" AND {col} = ?"
                args.append(val)
        sql += " ORDER BY confidence DESC, md_m LIMIT ?"
        args.append(limit)
        return [_row_to_dict(r) for r in self.conn.execute(sql, args)]

    def lessons_bulk(self, well_ids: Iterable[str]) -> dict[str, list[dict]]:
        ids = list(well_ids)
        if not ids:
            return {}
        ph = ",".join("?" * len(ids))
        out: dict[str, list[dict]] = {i: [] for i in ids}
        for r in self.conn.execute(
            f"SELECT * FROM lessons WHERE well_id IN ({ph}) ORDER BY md_m", ids
        ):
            out[r["well_id"]].append(_row_to_dict(r))
        return out

    def load_extracted_events(self, events: Sequence[Any], stats: dict | None = None) -> None:
        """Persist the output of the NLP pipeline, citations and all."""
        cur = self.conn.cursor()
        cur.execute("DELETE FROM citations")
        cur.execute("DELETE FROM events")
        for ev in events:
            cur.execute(
                "INSERT INTO events (well_id, event_type, severity, md_m, tvd_m, md_end_m, "
                "formation, event_date, npt_hours, magnitude_json, confidence, subtype, "
                "remedial_action, needs_review) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (ev.well_id, ev.event_type, ev.severity, ev.md_m, ev.tvd_m, ev.md_end_m,
                 ev.formation, ev.event_date, ev.npt_hours, json.dumps(ev.magnitude),
                 ev.confidence, getattr(ev, "subtype", None),
                 ev.remedial_action, int(ev.needs_review)),
            )
            event_id = cur.lastrowid
            cur.executemany(
                "INSERT INTO citations (event_id, document_id, doc_type, page, line_no, snippet) "
                "VALUES (?,?,?,?,?,?)",
                [(event_id, c.document_id, c.doc_type, c.page, c.line_no, c.snippet)
                 for c in ev.citations],
            )
        if stats is not None:
            cur.execute(
                "INSERT INTO ingest_runs (started_at, stats_json) VALUES (datetime('now'), ?)",
                (json.dumps(stats),),
            )
        self.conn.commit()

    def backfill_event_tvd(self) -> int:
        """
        Reports quote measured depth; correlation works in true vertical
        depth.  Where the text gave no TVD, interpolate it from the well's own
        drilling log, which carries both.
        """
        cur = self.conn.cursor()
        rows = cur.execute(
            "SELECT event_id, well_id, md_m FROM events WHERE tvd_m IS NULL AND md_m IS NOT NULL"
        ).fetchall()
        fixed = 0
        for row in rows:
            tvd = self._tvd_from_log(row["well_id"], row["md_m"])
            if tvd is not None:
                cur.execute("UPDATE events SET tvd_m = ? WHERE event_id = ?", (tvd, row["event_id"]))
                fixed += 1
        self.conn.commit()
        return fixed

    def _tvd_from_log(self, well_id: str, md: float) -> float | None:
        row = self.conn.execute(
            "SELECT md_m, tvd_m FROM drilling_log WHERE well_id = ? "
            "ORDER BY ABS(md_m - ?) LIMIT 1", (well_id, md)
        ).fetchone()
        if row is None:
            return None
        # The log is sampled every 10 m; close enough to take directly, but
        # scale for the residual so a deviated well stays honest.
        ratio = row["tvd_m"] / row["md_m"] if row["md_m"] else 1.0
        return round(md * ratio, 1)

    # -- queries -----------------------------------------------------------

    def wells(self, status: str | None = None) -> list[dict]:
        sql = "SELECT * FROM wells"
        args: tuple = ()
        if status:
            sql += " WHERE status = ?"
            args = (status,)
        sql += " ORDER BY field_code, well_id"
        return [_row_to_dict(r) for r in self.conn.execute(sql, args)]

    def well(self, well_id: str) -> dict | None:
        row = self.conn.execute("SELECT * FROM wells WHERE well_id = ?", (well_id,)).fetchone()
        return _row_to_dict(row) if row else None

    def tops(self, well_id: str) -> list[dict]:
        return [_row_to_dict(r) for r in self.conn.execute(
            "SELECT * FROM formation_tops WHERE well_id = ? ORDER BY top_tvd_m", (well_id,))]

    def tops_bulk(self, well_ids: Iterable[str]) -> dict[str, list[dict]]:
        ids = list(well_ids)
        if not ids:
            return {}
        placeholders = ",".join("?" * len(ids))
        out: dict[str, list[dict]] = {i: [] for i in ids}
        for r in self.conn.execute(
            f"SELECT * FROM formation_tops WHERE well_id IN ({placeholders}) ORDER BY top_tvd_m",
            ids,
        ):
            out[r["well_id"]].append(_row_to_dict(r))
        return out

    def events(self, well_id: str | None = None, event_type: str | None = None,
               min_severity: int = 0) -> list[dict]:
        sql = "SELECT * FROM events WHERE severity >= ?"
        args: list[Any] = [min_severity]
        if well_id:
            sql += " AND well_id = ?"
            args.append(well_id)
        if event_type:
            sql += " AND event_type = ?"
            args.append(event_type)
        sql += " ORDER BY well_id, md_m"
        rows = [_row_to_dict(r) for r in self.conn.execute(sql, args)]
        for r in rows:
            r["magnitude"] = json.loads(r.pop("magnitude_json") or "{}")
            r["needs_review"] = bool(r["needs_review"])
        return rows

    def events_bulk(self, well_ids: Iterable[str]) -> dict[str, list[dict]]:
        ids = list(well_ids)
        if not ids:
            return {}
        placeholders = ",".join("?" * len(ids))
        out: dict[str, list[dict]] = {i: [] for i in ids}
        for r in self.conn.execute(
            f"SELECT * FROM events WHERE well_id IN ({placeholders}) ORDER BY md_m", ids
        ):
            d = _row_to_dict(r)
            d["magnitude"] = json.loads(d.pop("magnitude_json") or "{}")
            d["needs_review"] = bool(d["needs_review"])
            out[d["well_id"]].append(d)
        return out

    def citations(self, event_id: int) -> list[dict]:
        return [_row_to_dict(r) for r in self.conn.execute(
            "SELECT * FROM citations WHERE event_id = ? ORDER BY doc_type, page, line_no",
            (event_id,))]

    def citations_bulk(self, event_ids: Sequence[int]) -> dict[int, list[dict]]:
        if not event_ids:
            return {}
        placeholders = ",".join("?" * len(event_ids))
        out: dict[int, list[dict]] = {i: [] for i in event_ids}
        for r in self.conn.execute(
            f"SELECT * FROM citations WHERE event_id IN ({placeholders}) "
            f"ORDER BY doc_type, page, line_no", list(event_ids)
        ):
            out[r["event_id"]].append(_row_to_dict(r))
        return out

    def documents(self, well_id: str | None = None) -> list[dict]:
        sql = "SELECT * FROM documents"
        args: tuple = ()
        if well_id:
            sql += " WHERE well_id = ?"
            args = (well_id,)
        sql += " ORDER BY well_id, doc_type"
        return [_row_to_dict(r) for r in self.conn.execute(sql, args)]

    def document(self, document_id: str) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM documents WHERE document_id = ?", (document_id,)).fetchone()
        return _row_to_dict(row) if row else None

    def drilling_log(self, well_id: str, md_from: float = 0.0,
                     md_to: float = 1e9, every: int = 1) -> list[dict]:
        rows = [_row_to_dict(r) for r in self.conn.execute(
            "SELECT * FROM drilling_log WHERE well_id = ? AND md_m BETWEEN ? AND ? "
            "ORDER BY md_m", (well_id, md_from, md_to))]
        return rows[::every] if every > 1 else rows

    def section_averages(self, well_id: str) -> dict[str, dict[str, float]]:
        """Mean drilling parameters per formation - the comparison fingerprint."""
        out: dict[str, dict[str, float]] = {}
        for r in self.conn.execute(
            "SELECT formation, AVG(rop_m_hr) rop, AVG(wob_t) wob, AVG(rpm) rpm, "
            "AVG(torque_knm) torque, AVG(mud_weight_sg) mw, AVG(ecd_sg) ecd, "
            "AVG(gas_units) gas, COUNT(*) n "
            "FROM drilling_log WHERE well_id = ? GROUP BY formation", (well_id,)
        ):
            out[r["formation"]] = {
                "rop_m_hr": round(r["rop"], 2), "wob_t": round(r["wob"], 2),
                "rpm": round(r["rpm"], 1), "torque_kNm": round(r["torque"], 2),
                "mud_weight_sg": round(r["mw"], 3), "ecd_sg": round(r["ecd"], 3),
                "gas_units": round(r["gas"], 1), "samples": r["n"],
            }
        return out

    def stats(self) -> dict:
        c = self.conn.execute
        latest = c("SELECT stats_json FROM ingest_runs ORDER BY run_id DESC LIMIT 1").fetchone()
        return {
            "wells": c("SELECT COUNT(*) FROM wells").fetchone()[0],
            "wells_drilling": c("SELECT COUNT(*) FROM wells WHERE status='Drilling'").fetchone()[0],
            "fields": c("SELECT COUNT(DISTINCT field_code) FROM wells").fetchone()[0],
            "documents": c("SELECT COUNT(*) FROM documents").fetchone()[0],
            "document_pages": c("SELECT COALESCE(SUM(pages),0) FROM documents").fetchone()[0],
            "events": c("SELECT COUNT(*) FROM events").fetchone()[0],
            "lessons": c("SELECT COUNT(*) FROM lessons").fetchone()[0],
            "casing_strings": c("SELECT COUNT(*) FROM casing_strings").fetchone()[0],
            "cement_jobs": c("SELECT COUNT(*) FROM cement_jobs").fetchone()[0],
            "reservoir_intervals": c("SELECT COUNT(*) FROM reservoir_intervals").fetchone()[0],
            "search_rows": c("SELECT COUNT(*) FROM search_index").fetchone()[0],
            "citations": c("SELECT COUNT(*) FROM citations").fetchone()[0],
            "formation_tops": c("SELECT COUNT(*) FROM formation_tops").fetchone()[0],
            "log_samples": c("SELECT COUNT(*) FROM drilling_log").fetchone()[0],
            "total_npt_hours": round(
                c("SELECT COALESCE(SUM(npt_hours),0) FROM events").fetchone()[0], 1),
            "events_needing_review": c(
                "SELECT COUNT(*) FROM events WHERE needs_review = 1").fetchone()[0],
            "last_ingest": json.loads(latest[0]) if latest else None,
        }

    def event_type_counts(self) -> list[dict]:
        return [{"event_type": r[0], "count": r[1], "npt_hours": round(r[2] or 0.0, 1)}
                for r in self.conn.execute(
                    "SELECT event_type, COUNT(*), SUM(npt_hours) FROM events "
                    "GROUP BY event_type ORDER BY COUNT(*) DESC")]

    def formation_event_counts(self) -> list[dict]:
        return [{"formation": r[0], "count": r[1], "npt_hours": round(r[2] or 0.0, 1)}
                for r in self.conn.execute(
                    "SELECT formation, COUNT(*), SUM(npt_hours) FROM events "
                    "WHERE formation IS NOT NULL GROUP BY formation ORDER BY COUNT(*) DESC")]
