"""进程内运行时装配。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from server.auth import AuthService, RateLimiter
from server.bus import EventBus
from server.cameras import CameraHub
from server.config import AppConfig
from server.db import Database
from server.detect import DetectorService
from server.diskmon import DiskMon
from server.door import DoorService
from server.face.pipeline import FaceService
from server.load import LoadGauge
from server.notify import Notifier
from server.record import RecordService
from server.scheduler import Scheduler
from server.search import FaceSearch
from server.secrets import SecretStore
from server.storage import Storage


@dataclass
class Runtime:
    root: Path
    config: AppConfig
    config_path: Path
    db: Database
    secrets: SecretStore
    auth: AuthService
    limiter: RateLimiter
    bus: EventBus
    hub: CameraHub
    storage: Storage
    diskmon: DiskMon
    load: LoadGauge
    detector: DetectorService
    face: FaceService
    door: DoorService
    record: RecordService
    notify: Notifier
    scheduler: Scheduler
    search: FaceSearch
    tasks_ok: bool = True


def build_runtime(root: Path, config_path: Path, config: AppConfig) -> Runtime:
    data = config.data_path(root)
    db = Database(data / "db" / "smartcctv.sqlite3")
    secrets = SecretStore(data / "secrets")
    auth = AuthService(secrets, config.auth)
    bus = EventBus()
    hub = CameraHub(config)
    storage = Storage(data, config.storage)
    diskmon = DiskMon(data, config.disk)
    load = LoadGauge()
    detector = DetectorService(config, hub, bus, db, load, root)
    face = FaceService(config, hub, bus, db, secrets, storage, root)
    door = DoorService(config.door, bus, db)
    record = RecordService(config, db, storage, diskmon, bus, root)
    notify = Notifier(config.notify, db)
    bus.add_handler(notify.submit)
    scheduler = Scheduler(config, db, load, record, storage)
    search = FaceSearch(face)
    return Runtime(
        root=root,
        config=config,
        config_path=config_path,
        db=db,
        secrets=secrets,
        auth=auth,
        limiter=RateLimiter(),
        bus=bus,
        hub=hub,
        storage=storage,
        diskmon=diskmon,
        load=load,
        detector=detector,
        face=face,
        door=door,
        record=record,
        notify=notify,
        scheduler=scheduler,
        search=search,
    )


async def start_runtime(rt: Runtime) -> None:
    await rt.db.init()
    if rt.secrets.totp_created:
        print("\n=== SmartCCTV 首次启动：请用 Authenticator 扫描/录入 ===")
        print(rt.secrets.totp_uri())
        try:
            import qrcode

            qr = qrcode.QRCode(border=1)
            qr.add_data(rt.secrets.totp_uri())
            qr.make(fit=True)
            qr.print_ascii(invert=True)
        except Exception:
            pass
        print("====================================================\n")
    await rt.door.startup_close()
    rt.hub.start()
    await rt.record.start()
    await rt.detector.start()
    await rt.face.start()
    await rt.notify.start()
    await rt.scheduler.start()


async def stop_runtime(rt: Runtime) -> None:
    await rt.scheduler.stop()
    await rt.notify.stop()
    await rt.face.stop()
    await rt.detector.stop()
    await rt.record.stop()
    rt.hub.stop()
    await rt.db.close()
