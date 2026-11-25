"""SQLite 模型。元数据在库里，大文件在磁盘或 S3。"""

from __future__ import annotations

import asyncio
from pathlib import Path

from sqlalchemy import Float, ForeignKey, Index, Integer, LargeBinary, String, Text, event, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

BUS_SCHEMA = "smartcctv.event.v1"
DOOR_SCHEMA = "door.command.v1"


class Base(DeclarativeBase):
    pass


class Person(Base):
    __tablename__ = "persons"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    note: Mapped[str] = mapped_column(Text, default="")
    enabled: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[float] = mapped_column(Float, index=True)
    deleted_at: Mapped[float | None] = mapped_column(Float, nullable=True)


class FaceEmbedding(Base):
    __tablename__ = "face_embeddings"
    id: Mapped[int] = mapped_column(primary_key=True)
    person_id: Mapped[int] = mapped_column(ForeignKey("persons.id"))
    embedding: Mapped[bytes] = mapped_column(LargeBinary)
    source_snapshot_path: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[float] = mapped_column(Float)


class FaceSighting(Base):
    __tablename__ = "face_sightings"
    id: Mapped[int] = mapped_column(primary_key=True)
    cam_id: Mapped[str] = mapped_column(String(64), index=True)
    person_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    embedding: Mapped[bytes] = mapped_column(LargeBinary)
    snapshot_path: Mapped[str] = mapped_column(Text, default="")
    similarity: Mapped[float] = mapped_column(Float, default=0)
    det_score: Mapped[float] = mapped_column(Float, default=0)
    ts: Mapped[float] = mapped_column(Float, index=True)


class DoorPassage(Base):
    """一次开门窗口内的通行摘要；云端只收人数/陌生人计数，不收向量与抓拍。"""

    __tablename__ = "door_passages"
    id: Mapped[int] = mapped_column(primary_key=True)
    command_id: Mapped[str] = mapped_column(String(64), index=True)
    people_count: Mapped[int] = mapped_column(Integer, default=0)
    stranger_count: Mapped[int] = mapped_column(Integer, default=0)
    first_snapshot_path: Mapped[str] = mapped_column(Text, default="")
    started_at: Mapped[float] = mapped_column(Float, index=True)
    ended_at: Mapped[float] = mapped_column(Float, default=0)
    truncated: Mapped[int] = mapped_column(Integer, default=0)


class EventRow(Base):
    __tablename__ = "events"
    __table_args__ = (Index("ix_events_cam_ts", "cam_id", "ts"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    cam_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    type: Mapped[str] = mapped_column(String(64), index=True)
    level: Mapped[str] = mapped_column(String(16), default="info")
    payload: Mapped[str] = mapped_column(Text, default="{}")
    snapshot_path: Mapped[str] = mapped_column(Text, default="")
    clip_path: Mapped[str] = mapped_column(Text, default="")
    mode: Mapped[str] = mapped_column(String(16), default="day")
    ts: Mapped[float] = mapped_column(Float, index=True)


class Recording(Base):
    __tablename__ = "recordings"
    id: Mapped[int] = mapped_column(primary_key=True)
    cam_id: Mapped[str] = mapped_column(String(64), index=True)
    path: Mapped[str] = mapped_column(Text)
    start_ts: Mapped[float] = mapped_column(Float, index=True)
    end_ts: Mapped[float] = mapped_column(Float, index=True)
    kind: Mapped[str] = mapped_column(String(16))
    storage: Mapped[str] = mapped_column(String(16), default="local")
    s3_key: Mapped[str] = mapped_column(Text, default="")


class DoorLog(Base):
    __tablename__ = "door_logs"
    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[float] = mapped_column(Float, index=True)
    person_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    person_name: Mapped[str] = mapped_column(String(120), default="")
    similarity: Mapped[float | None] = mapped_column(Float, nullable=True)
    action: Mapped[str] = mapped_column(String(32))
    reason: Mapped[str] = mapped_column(String(32), default="")
    command_id: Mapped[str] = mapped_column(String(64), default="")
    snapshot_path: Mapped[str] = mapped_column(Text, default="")
    detail: Mapped[str] = mapped_column(Text, default="")


class DetectionStat(Base):
    __tablename__ = "detection_stats"
    __table_args__ = (Index("ix_stats_bucket_cam", "bucket_start", "cam_id", unique=True),)
    id: Mapped[int] = mapped_column(primary_key=True)
    bucket_start: Mapped[float] = mapped_column(Float)
    cam_id: Mapped[str] = mapped_column(String(64))
    person_max: Mapped[int] = mapped_column(Integer, default=0)
    person_sum: Mapped[int] = mapped_column(Integer, default=0)
    samples: Mapped[int] = mapped_column(Integer, default=0)
    events: Mapped[int] = mapped_column(Integer, default=0)
    cpu_avg: Mapped[float] = mapped_column(Float, default=0)
    mem_avg: Mapped[float] = mapped_column(Float, default=0)
    viewers: Mapped[int] = mapped_column(Integer, default=0)


class JobRow(Base):
    __tablename__ = "jobs"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    progress: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[float] = mapped_column(Float, default=0)


class NotifySpill(Base):
    __tablename__ = "notify_spill"
    id: Mapped[int] = mapped_column(primary_key=True)
    level: Mapped[str] = mapped_column(String(16))
    body: Mapped[str] = mapped_column(Text)
    created_at: Mapped[float] = mapped_column(Float, index=True)
    sent_at: Mapped[float | None] = mapped_column(Float, nullable=True)


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[float] = mapped_column(Float, index=True)
    action: Mapped[str] = mapped_column(String(64))
    detail: Mapped[str] = mapped_column(Text, default="")


class Database:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.engine = create_async_engine(f"sqlite+aiosqlite:///{path}", connect_args={"timeout": 30})
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False, class_=AsyncSession)
        self._write_lock = asyncio.Lock()
        self._install_pragmas()

    def _install_pragmas(self) -> None:
        @event.listens_for(self.engine.sync_engine, "connect")
        def _pragmas(dbapi_connection, _record) -> None:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.close()

    async def init(self) -> None:
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def checkpoint(self) -> None:
        async with self.engine.begin() as conn:
            await conn.execute(text("PRAGMA wal_checkpoint(TRUNCATE)"))

    async def integrity(self) -> str:
        async with self.engine.connect() as conn:
            result = await conn.execute(text("PRAGMA integrity_check"))
            row = result.first()
            return str(row[0]) if row else "unknown"

    async def write(self, fn):
        async with self._write_lock:
            async with self.sessions() as session:
                result = await fn(session)
                await session.commit()
                return result

    async def read(self, fn):
        async with self.sessions() as session:
            return await fn(session)

    async def close(self) -> None:
        await self.engine.dispose()
