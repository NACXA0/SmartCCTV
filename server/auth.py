"""TOTP 登录、防重放、会话上限。"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field

import pyotp
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from server.config import AuthConfig
from server.secrets import SecretStore


@dataclass
class Session:
    sid: str
    ip: str
    created: float
    seen: float
    version: int


@dataclass
class AuthResult:
    ok: bool
    error: str = ""
    token: str = ""
    session: Session | None = None


class AuthService:
    def __init__(self, secrets: SecretStore, config: AuthConfig, clock=time.time) -> None:
        self.secrets = secrets
        self.config = config
        self.clock = clock
        self.sessions: dict[str, Session] = {}
        self._used_steps: dict[int, float] = {}
        self._fails: dict[str, list[float]] = {}
        self._global_fails: list[float] = []

    def _serializer(self) -> URLSafeTimedSerializer:
        return URLSafeTimedSerializer(self.secrets.cookie_secret, salt="smartcctv-session")

    def _prune_fails(self, ip: str) -> None:
        window = self.config.login_lock_seconds
        now = self.clock()
        self._fails[ip] = [item for item in self._fails.get(ip, []) if now - item < window]
        self._global_fails = [item for item in self._global_fails if now - item < window]
        self._used_steps = {step: ts for step, ts in self._used_steps.items() if now - ts < 120}

    def locked(self, ip: str) -> bool:
        self._prune_fails(ip)
        limit = self.config.login_max_fails
        return len(self._fails.get(ip, [])) >= limit or len(self._global_fails) >= limit * 4

    def login(self, code: str, ip: str) -> AuthResult:
        self._prune_fails(ip)
        if self.locked(ip):
            return AuthResult(False, "登录尝试过多，请稍后再试")
        if self._ip_count(ip) >= self.config.max_sessions_per_ip:
            return AuthResult(False, "该地址的会话数已达上限")
        if len(self.sessions) >= self.config.max_sessions:
            return AuthResult(False, "会话数已达上限")
        totp = pyotp.TOTP(self.secrets.totp_secret)
        now = self.clock()
        matched_step = None
        for offset in (-1, 0, 1):
            step_time = now + offset * 30
            if totp.at(step_time) == str(code).strip():
                matched_step = int(step_time) // 30
                break
        if matched_step is None:
            self._fails.setdefault(ip, []).append(now)
            self._global_fails.append(now)
            return AuthResult(False, "验证码错误")
        if matched_step in self._used_steps:
            return AuthResult(False, "验证码已使用")
        self._used_steps[matched_step] = now
        self._fails.pop(ip, None)
        session = Session(str(uuid.uuid4()), ip, now, now, self.secrets.session_version)
        self.sessions[session.sid] = session
        token = self._serializer().dumps({"sid": session.sid, "ver": session.version, "iat": now})
        return AuthResult(True, token=token, session=session)

    def read_token(self, token: str) -> Session | None:
        if not token:
            return None
        max_age = self.config.session_days * 86400
        try:
            data = self._serializer().loads(token, max_age=max_age)
        except (BadSignature, SignatureExpired):
            return None
        if int(data.get("ver", -1)) != self.secrets.session_version:
            return None
        session = self.sessions.get(data.get("sid", ""))
        if session is None or session.version != self.secrets.session_version:
            return None
        session.seen = self.clock()
        return session

    def logout(self, sid: str) -> None:
        self.sessions.pop(sid, None)

    def drop_all(self) -> None:
        self.sessions.clear()

    def list_sessions(self) -> list[dict]:
        return [
            {"sid": item.sid, "ip": item.ip, "created": item.created, "seen": item.seen}
            for item in self.sessions.values()
        ]

    def renew_token(self, session: Session) -> str | None:
        now = self.clock()
        if now - session.created < self.config.session_days * 86400 * 0.5:
            return None
        session.created = now
        return self._serializer().dumps({"sid": session.sid, "ver": session.version, "iat": now})

    def _ip_count(self, ip: str) -> int:
        return sum(1 for item in self.sessions.values() if item.ip == ip)


@dataclass
class Bucket:
    tokens: float
    updated: float
    rate: float = 10
    burst: float = 20

    def allow(self, now: float) -> bool:
        self.tokens = min(self.burst, self.tokens + (now - self.updated) * self.rate)
        self.updated = now
        if self.tokens >= 1:
            self.tokens -= 1
            return True
        return False


@dataclass
class RateLimiter:
    buckets: dict[str, Bucket] = field(default_factory=dict)

    def allow(self, ip: str, now: float | None = None) -> bool:
        now = time.time() if now is None else now
        bucket = self.buckets.get(ip)
        if bucket is None:
            bucket = Bucket(tokens=20, updated=now)
            self.buckets[ip] = bucket
        return bucket.allow(now)
