"""人数统计报表查询。"""

from __future__ import annotations

import time


async def query_stats(db, cam_id: str | None, hours: int = 24) -> list[dict]:
    from sqlalchemy import select

    from server.db import DetectionStat

    start = time.time() - hours * 3600

    async def read(session):
        stmt = select(DetectionStat).where(DetectionStat.bucket_start >= start)
        if cam_id:
            stmt = stmt.where(DetectionStat.cam_id == cam_id)
        stmt = stmt.order_by(DetectionStat.bucket_start.asc())
        result = await session.execute(stmt)
        return list(result.scalars())

    rows = await db.read(read)
    return [
        {
            "bucket_start": row.bucket_start,
            "cam_id": row.cam_id,
            "person_max": row.person_max,
            "person_avg": round(row.person_sum / max(1, row.samples), 2),
            "samples": row.samples,
            "events": row.events,
            "cpu_avg": round(row.cpu_avg, 1),
            "mem_avg": round(row.mem_avg, 1),
        }
        for row in rows
    ]


async def peak_summary(db, hours: int = 24) -> dict:
    rows = await query_stats(db, None, hours)
    if not rows:
        return {"max_persons": 0, "peak_bucket": None, "events": 0}
    peak = max(rows, key=lambda r: r["person_max"])
    return {
        "max_persons": peak["person_max"],
        "peak_bucket": peak["bucket_start"],
        "peak_cam": peak["cam_id"],
        "events": sum(r["events"] for r in rows),
    }
