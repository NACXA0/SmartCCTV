"""REST API。"""

from __future__ import annotations

import asyncio
import json
import time

import cv2
import numpy as np
from fastapi import APIRouter, File, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select

from server.bus import BusMessage
from server.cameras import mjpeg_generator
from server.config import public_config
from server.db import DoorLog, EventRow, FaceEmbedding, Person
from server.health import collect_health
from server.stats import peak_summary, query_stats


class LoginBody(BaseModel):
    code: str


class PersonBody(BaseModel):
    name: str
    note: str = ""


def build_api(get_runtime) -> APIRouter:
    api = APIRouter(prefix="/api")

    def rt():
        runtime = get_runtime()
        if runtime is None:
            raise HTTPException(503, "服务未就绪")
        return runtime

    def require_user(request: Request):
        runtime = rt()
        token = request.cookies.get("session", "")
        session = runtime.auth.read_token(token)
        if session is None:
            raise HTTPException(401, "未登录")
        return session

    @api.post("/auth/login")
    async def login(body: LoginBody, request: Request, response: Response):
        runtime = rt()
        ip = request.client.host if request.client else "unknown"
        result = runtime.auth.login(body.code, ip)
        if not result.ok:
            raise HTTPException(401, result.error)
        response.set_cookie(
            "session",
            result.token,
            httponly=True,
            samesite="lax",
            max_age=runtime.config.auth.session_days * 86400,
        )
        return {"ok": True}

    @api.post("/auth/logout")
    async def logout(request: Request, response: Response):
        runtime = rt()
        session = runtime.auth.read_token(request.cookies.get("session", ""))
        if session:
            runtime.auth.logout(session.sid)
        response.delete_cookie("session")
        return {"ok": True}

    @api.get("/auth/me")
    async def me(request: Request):
        session = require_user(request)
        return {"ok": True, "ip": session.ip, "sid": session.sid}

    @api.get("/system/health")
    async def health(request: Request):
        require_user(request)
        return await collect_health(rt())

    @api.get("/system/config")
    async def get_config(request: Request):
        require_user(request)
        return public_config(rt().config)

    @api.get("/system/load")
    async def get_load(request: Request):
        require_user(request)
        runtime = rt()
        return {"cpu": runtime.load.cpu, "mem": runtime.load.mem, "peaks": runtime.load.peaks()}

    @api.post("/system/notify/test")
    async def notify_test(request: Request):
        require_user(request)
        ok = await rt().notify.test()
        return {"ok": ok}

    @api.get("/cameras")
    async def cameras(request: Request):
        require_user(request)
        return rt().hub.status()

    @api.get("/cameras/{cam_id}/mjpeg")
    async def mjpeg(cam_id: str, request: Request):
        require_user(request)
        runtime = rt()
        if cam_id not in runtime.hub.slots:
            raise HTTPException(404, "摄像头不存在")
        gen = mjpeg_generator(
            runtime.hub,
            cam_id,
            runtime.config.watch.jpeg_quality,
            runtime.config.watch.display_width,
        )
        return StreamingResponse(gen, media_type="multipart/x-mixed-replace; boundary=frame")

    @api.get("/events")
    async def list_events(request: Request, before: float | None = None, limit: int = 50):
        require_user(request)
        runtime = rt()
        before = before or time.time()
        limit = min(200, max(1, limit))

        async def read(session):
            result = await session.execute(
                select(EventRow).where(EventRow.ts < before).order_by(EventRow.ts.desc()).limit(limit)
            )
            return list(result.scalars())

        rows = await runtime.db.read(read)
        return [
            {
                "id": row.id,
                "cam_id": row.cam_id,
                "type": row.type,
                "level": row.level,
                "payload": json.loads(row.payload or "{}"),
                "snapshot_path": row.snapshot_path,
                "mode": row.mode,
                "ts": row.ts,
            }
            for row in rows
        ]

    @api.get("/events/stream")
    async def events_stream(request: Request):
        require_user(request)
        runtime = rt()
        queue = runtime.bus.subscribe()

        async def gen():
            try:
                yield f"data: {json.dumps({'type': 'hello', 'ts': time.time()})}\n\n"
                while True:
                    if await request.is_disconnected():
                        break
                    try:
                        msg = await asyncio.wait_for(queue.get(), timeout=15)
                        yield f"data: {json.dumps(msg.to_dict(), ensure_ascii=False)}\n\n"
                    except TimeoutError:
                        yield f": ping {time.time()}\n\n"
            finally:
                runtime.bus.unsubscribe(queue)

        return StreamingResponse(gen(), media_type="text/event-stream")

    @api.get("/faces/persons")
    async def persons(request: Request):
        require_user(request)

        async def read(session):
            result = await session.execute(select(Person).where(Person.deleted_at.is_(None)).order_by(Person.id))
            return list(result.scalars())

        rows = await rt().db.read(read)
        return [{"id": r.id, "name": r.name, "note": r.note, "enabled": bool(r.enabled), "created_at": r.created_at} for r in rows]

    @api.post("/faces/reload")
    async def reload_face_index(request: Request):
        """手动重建内存人脸索引（从 DB 重载 embedding）。"""
        require_user(request)
        await rt().face.reload_gallery()
        count = len(rt().face.facedb.ids)
        return {"ok": True, "persons_in_index": count}

    @api.post("/faces/persons")
    async def create_person(body: PersonBody, request: Request):
        require_user(request)

        async def write(session):
            person = Person(name=body.name.strip(), note=body.note, enabled=1, created_at=time.time())
            session.add(person)
            await session.flush()
            return person.id

        pid = await rt().db.write(write)
        return {"id": pid}

    @api.post("/faces/persons/{person_id}/photos")
    async def add_photo(person_id: int, request: Request, file: UploadFile = File(...)):
        require_user(request)
        runtime = rt()
        raw = await file.read()
        arr = np.frombuffer(raw, dtype=np.uint8)
        image = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if image is None:
            raise HTTPException(400, "无法解码图片")
        emb = runtime.face.embed_image(image)
        if emb is None:
            raise HTTPException(400, "未检测到合格人脸")
        async def read_person(session):
            return await session.get(Person, person_id)

        person = await runtime.db.read(read_person)
        if person is None or person.deleted_at is not None:
            raise HTTPException(404, "人员不存在")
        blob = runtime.secrets.encrypt(emb.astype(np.float32).tobytes())
        snap = runtime.storage.save_jpeg("faces", raw)

        async def write(session):
            session.add(
                FaceEmbedding(
                    person_id=person_id,
                    embedding=blob,
                    source_snapshot_path=snap,
                    created_at=time.time(),
                )
            )

        await runtime.db.write(write)
        await runtime.face.reload_gallery()
        runtime.bus.publish(BusMessage(type="face_enroll", level="info", payload={"person_id": person_id, "snapshot_path": snap}))
        return {"ok": True, "snapshot_path": snap}

    @api.post("/internal/persons/enroll")
    async def internal_enroll(request: Request):
        """总控 HMAC 入库：note=云端 user_id。监控不执行开门。"""
        import hashlib
        import hmac as hm

        runtime = rt()
        secret = runtime.config.center.enroll_hmac_secret
        if not secret:
            raise HTTPException(503, "未配置 center.enroll_hmac_secret")
        form = await request.form()
        body_meta = form.get("meta_sha_body") or "{}"
        if hasattr(body_meta, "read"):
            body_meta = "{}"
        meta = str(body_meta).encode()
        ts = request.headers.get("X-Lobn-Timestamp", "")
        nonce = request.headers.get("X-Lobn-Nonce", "")
        sig = request.headers.get("X-Lobn-Signature", "")
        path = "/api/internal/persons/enroll"
        digest = hashlib.sha256(meta).hexdigest()
        payload = f"{ts}\n{nonce}\nPOST\n{path}\n{digest}"
        expected = hm.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
        try:
            if abs(time.time() - float(ts)) > 60:
                raise HTTPException(401, "时间窗超时")
        except ValueError as exc:
            raise HTTPException(401, "时间戳无效") from exc
        if not hm.compare_digest(expected, sig or ""):
            raise HTTPException(401, "签名错误")

        user_id = str(form.get("user_id") or "")
        name = str(form.get("name") or user_id or "学员")
        if not user_id:
            raise HTTPException(400, "缺少 user_id")
        upload = form.get("file")
        if upload is None or not hasattr(upload, "read"):
            raise HTTPException(400, "缺少图片文件")
        raw = await upload.read()
        if len(raw) > 2 * 1024 * 1024:
            raise HTTPException(400, "图片过大")
        image = cv2.imdecode(np.frombuffer(raw, dtype=np.uint8), cv2.IMREAD_COLOR)
        if image is None:
            raise HTTPException(400, "无法解码图片")
        emb = runtime.face.embed_image(image)
        if emb is None:
            raise HTTPException(400, "未检测到合格人脸")

        async def find_by_note(session):
            result = await session.execute(
                select(Person).where(Person.note == user_id, Person.deleted_at.is_(None))
            )
            return result.scalar_one_or_none()

        existing = await runtime.db.read(find_by_note)
        blob = runtime.secrets.encrypt(emb.astype(np.float32).tobytes())
        snap = runtime.storage.save_jpeg("faces", raw)
        existing_id = existing.id if existing is not None else None

        async def write(session):
            if existing_id is None:
                person = Person(name=name, note=user_id, enabled=1, created_at=time.time())
                session.add(person)
                await session.flush()
                pid = person.id
            else:
                person = await session.get(Person, existing_id)
                pid = existing_id
                if person is not None:
                    person.name = name
                    person.enabled = 1
            session.add(
                FaceEmbedding(
                    person_id=pid,
                    embedding=blob,
                    source_snapshot_path=snap,
                    created_at=time.time(),
                )
            )
            return pid

        pid = await runtime.db.write(write)
        await runtime.face.reload_gallery()
        return {"ok": True, "person_id": pid, "snapshot_path": snap}

    @api.post("/search/person/{person_id}")
    async def search_person(person_id: int, request: Request, hours: int = 168):
        require_user(request)
        start = time.time() - hours * 3600
        return await rt().search.by_person(person_id, start_ts=start)

    @api.post("/search/image")
    async def search_image(request: Request, file: UploadFile = File(...), threshold: float = 0.4):
        require_user(request)
        runtime = rt()
        raw = await file.read()
        image = cv2.imdecode(np.frombuffer(raw, dtype=np.uint8), cv2.IMREAD_COLOR)
        if image is None:
            raise HTTPException(400, "无法解码图片")
        emb = runtime.face.embed_image(image)
        if emb is None:
            raise HTTPException(400, "未检测到人脸")
        return await runtime.search.by_embedding(emb, threshold=threshold)

    @api.get("/stats")
    async def stats(request: Request, cam_id: str | None = None, hours: int = 24):
        require_user(request)
        return {"series": await query_stats(rt().db, cam_id, hours), "peaks": await peak_summary(rt().db, hours)}

    @api.get("/door/logs")
    async def door_logs(request: Request, limit: int = 50):
        require_user(request)

        async def read(session):
            result = await session.execute(select(DoorLog).order_by(DoorLog.ts.desc()).limit(min(200, limit)))
            return list(result.scalars())

        rows = await rt().db.read(read)
        return [
            {
                "id": r.id,
                "ts": r.ts,
                "person_id": r.person_id,
                "person_name": r.person_name,
                "similarity": r.similarity,
                "action": r.action,
                "reason": r.reason,
                "snapshot_path": r.snapshot_path,
            }
            for r in rows
        ]

    @api.post("/door/open")
    async def door_open(request: Request):
        require_user(request)
        return await rt().door.manual_open()

    @api.get("/door/status")
    async def door_status(request: Request):
        require_user(request)
        return rt().door.status()

    @api.get("/media/{path:path}")
    async def media(path: str, request: Request):
        require_user(request)
        file_path = rt().storage.open_local(path)
        if not file_path.exists():
            raise HTTPException(404)
        return FileResponse(file_path)

    @api.get("/recordings")
    async def recordings(request: Request, cam_id: str | None = None, start: float | None = None, end: float | None = None):
        require_user(request)
        from server.db import Recording

        start = start or (time.time() - 86400)
        end = end or time.time()

        async def read(session):
            stmt = select(Recording).where(Recording.end_ts >= start, Recording.start_ts <= end)
            if cam_id:
                stmt = stmt.where(Recording.cam_id == cam_id)
            stmt = stmt.order_by(Recording.start_ts.asc())
            result = await session.execute(stmt)
            return list(result.scalars())

        rows = await rt().db.read(read)
        return [
            {
                "id": row.id,
                "cam_id": row.cam_id,
                "path": row.path,
                "start_ts": row.start_ts,
                "end_ts": row.end_ts,
                "kind": row.kind,
                "storage": row.storage,
            }
            for row in rows
        ]

    @api.get("/recordings/{rec_id}/stream")
    async def recording_stream(rec_id: int, request: Request):
        require_user(request)
        from server.db import Recording

        async def read(session):
            return await session.get(Recording, rec_id)

        row = await rt().db.read(read)
        if row is None:
            raise HTTPException(404, "录像不存在")
        path = rt().storage.open_local(row.path)
        if not path.exists():
            raise HTTPException(404, "文件不在热层")
        return FileResponse(path, media_type="video/mp4", filename=path.name)

    return api
