"""
Simulated eRTMAC real-time connector.

OIL's live drilling data comes from eRTMAC, which the prototype has no access
to.  Rather than pretend otherwise, NWIS defines the shape of the live feed
and replays recorded data through it: the bit advances, parameter frames
arrive, and the look-ahead engine re-runs as the well deepens.

Everything downstream of ``RealtimeFrame`` is the production path.  Swapping
this replay for a WITSML / eRTMAC client changes this module and nothing else.
"""

from __future__ import annotations

import asyncio
import json
from typing import AsyncIterator

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse

from ..config import settings
from ..deps import get_store, require_well
from ..engine.risk import look_ahead

router = APIRouter(prefix="/api/realtime", tags=["realtime"])

# Alerts are re-computed every N parameter frames rather than every frame:
# the interval ahead barely changes over 10 m, and the analysis is the
# expensive part.
ALERT_EVERY_FRAMES = 5


@router.get("/{well_id}/frames", summary="Replayable parameter frames")
def frames(
    well_id: str,
    md_from: float | None = Query(None, ge=0),
    md_to: float | None = Query(None, ge=0),
) -> dict:
    """The frames the stream will emit, for clients that would rather poll."""
    well = require_well(well_id)
    store = get_store()
    bit = float(well.get("current_bit_md_m") or well.get("td_md_m") or 0.0)
    lo = md_from if md_from is not None else max(0.0, bit * 0.55)
    hi = md_to if md_to is not None else bit
    rows = store.drilling_log(well_id, md_from=lo, md_to=hi)
    return {"well_id": well_id, "count": len(rows), "frames": rows}


@router.get("/{well_id}/stream", summary="Server-sent event stream of the drilling feed")
async def stream(
    well_id: str,
    md_from: float | None = Query(None, ge=0, description="Where to start the replay"),
    md_to: float | None = Query(None, ge=0),
    interval_ms: int = Query(700, ge=50, le=10000, description="Wall-clock gap between frames"),
    lookahead_m: float = Query(settings.default_lookahead_m, gt=0, le=2000),
    radius_km: float = Query(settings.default_radius_km, gt=0, le=100),
) -> StreamingResponse:
    """
    Replay a well's drilling feed as Server-Sent Events.

    Emits three event kinds:

        frame   one parameter sample, as the bit advances
        alerts  a refreshed look-ahead analysis for the new bit depth
        end     the replay has reached the current bit depth
    """
    well = require_well(well_id)
    store = get_store()

    bit = float(well.get("current_bit_md_m") or well.get("td_md_m") or 0.0)
    lo = md_from if md_from is not None else max(0.0, bit * 0.55)
    hi = md_to if md_to is not None else bit
    if hi <= lo:
        raise HTTPException(400, "md_to must be greater than md_from")

    rows = store.drilling_log(well_id, md_from=lo, md_to=hi)
    if not rows:
        raise HTTPException(404, f"No drilling log available for {well_id}")

    async def generate() -> AsyncIterator[bytes]:
        def sse(event: str, payload: dict) -> bytes:
            return f"event: {event}\ndata: {json.dumps(payload)}\n\n".encode("utf-8")

        yield sse("start", {
            "well_id": well_id,
            "well_name": well["well_name"],
            "md_from": lo,
            "md_to": hi,
            "frame_count": len(rows),
            "interval_ms": interval_ms,
        })

        try:
            for index, row in enumerate(rows):
                yield sse("frame", {"index": index, "total": len(rows), **row})

                if index % ALERT_EVERY_FRAMES == 0 or index == len(rows) - 1:
                    result = await asyncio.to_thread(
                        look_ahead, store, well_id,
                        bit_md_m=row["md_m"],
                        lookahead_m=lookahead_m,
                        radius_km=radius_km,
                    )
                    if result is not None:
                        yield sse("alerts", result.as_dict())

                await asyncio.sleep(interval_ms / 1000.0)

            yield sse("end", {"well_id": well_id, "final_md_m": rows[-1]["md_m"]})
        except asyncio.CancelledError:  # client navigated away
            raise

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
