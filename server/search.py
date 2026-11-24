"""以图搜人：embedding 暴力余弦 + 时间范围过滤。"""

from __future__ import annotations

import time

import numpy as np

from server.face.match import cosine, l2_normalize


class FaceSearch:
    def __init__(self, face_service) -> None:
        self.face = face_service

    async def by_person(self, person_id: int, start_ts: float | None = None, end_ts: float | None = None, limit: int = 50):
        from sqlalchemy import select

        from server.db import FaceSighting

        start_ts = start_ts or 0
        end_ts = end_ts or time.time()

        async def read(session):
            result = await session.execute(
                select(FaceSighting)
                .where(
                    FaceSighting.person_id == person_id,
                    FaceSighting.ts >= start_ts,
                    FaceSighting.ts <= end_ts,
                )
                .order_by(FaceSighting.ts.desc())
                .limit(limit)
            )
            return list(result.scalars())

        rows = await self.face.db.read(read)
        return [
            {
                "id": row.id,
                "cam_id": row.cam_id,
                "person_id": row.person_id,
                "similarity": row.similarity,
                "snapshot_path": row.snapshot_path,
                "ts": row.ts,
            }
            for row in rows
        ]

    async def by_embedding(
        self,
        embedding: np.ndarray,
        threshold: float = 0.4,
        start_ts: float | None = None,
        end_ts: float | None = None,
        limit: int = 50,
    ):
        from sqlalchemy import select

        from server.db import FaceSighting

        start_ts = start_ts or (time.time() - 7 * 86400)
        end_ts = end_ts or time.time()
        query = l2_normalize(embedding)

        async def read(session):
            result = await session.execute(
                select(FaceSighting)
                .where(FaceSighting.ts >= start_ts, FaceSighting.ts <= end_ts)
                .order_by(FaceSighting.ts.desc())
                .limit(2000)
            )
            return list(result.scalars())

        rows = await self.face.db.read(read)
        scored = []
        for row in rows:
            try:
                vec = np.frombuffer(self.face.secrets.decrypt(row.embedding), dtype=np.float32)
            except Exception:
                continue
            score = cosine(query, vec)
            if score >= threshold:
                scored.append((score, row))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [
            {
                "id": row.id,
                "cam_id": row.cam_id,
                "person_id": row.person_id,
                "similarity": round(score, 4),
                "snapshot_path": row.snapshot_path,
                "ts": row.ts,
            }
            for score, row in scored[:limit]
        ]
