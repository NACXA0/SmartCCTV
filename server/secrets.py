"""0600 密钥文件：TOTP、会话签名、人脸向量 AES 密钥。"""

from __future__ import annotations

import os
from pathlib import Path

import pyotp
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from server.paths import chmod_private, ensure_dir

TOTP_NAME = "totp.secret"
COOKIE_NAME = "cookie.secret"
FACE_NAME = "face.key"
VERSION_NAME = "session.version"


class SecretStore:
    def __init__(self, directory: Path) -> None:
        self.directory = ensure_dir(directory)
        self.totp_created = False
        self.totp_secret = self._load_or_create_totp()
        self.cookie_secret = self._load_or_create(COOKIE_NAME, os.urandom(32).hex())
        self.face_key = self._load_or_create_bytes(FACE_NAME, AESGCM.generate_key(bit_length=256))
        self.session_version = self._load_version()

    def _path(self, name: str) -> Path:
        return self.directory / name

    def _load_or_create(self, name: str, generated: str) -> str:
        path = self._path(name)
        if path.exists():
            chmod_private(path)
            return path.read_text(encoding="utf-8").strip()
        path.write_text(generated + "\n", encoding="utf-8")
        chmod_private(path)
        return generated

    def _load_or_create_bytes(self, name: str, generated: bytes) -> bytes:
        path = self._path(name)
        if path.exists():
            chmod_private(path)
            return path.read_bytes()
        path.write_bytes(generated)
        chmod_private(path)
        return generated

    def _load_or_create_totp(self) -> str:
        path = self._path(TOTP_NAME)
        if path.exists():
            chmod_private(path)
            return path.read_text(encoding="utf-8").strip()
        secret = pyotp.random_base32()
        path.write_text(secret + "\n", encoding="utf-8")
        chmod_private(path)
        self.totp_created = True
        return secret

    def _load_version(self) -> int:
        path = self._path(VERSION_NAME)
        if path.exists():
            try:
                return int(path.read_text(encoding="utf-8").strip() or "1")
            except ValueError:
                return 1
        path.write_text("1\n", encoding="utf-8")
        chmod_private(path)
        return 1

    def totp_uri(self, issuer: str = "SmartCCTV") -> str:
        return pyotp.TOTP(self.totp_secret).provisioning_uri(name="cctv.lobn", issuer_name=issuer)

    def rotate_totp(self) -> str:
        secret = pyotp.random_base32()
        path = self._path(TOTP_NAME)
        path.write_text(secret + "\n", encoding="utf-8")
        chmod_private(path)
        self.totp_secret = secret
        self.bump_session_version()
        self.cookie_secret = os.urandom(32).hex()
        cookie = self._path(COOKIE_NAME)
        cookie.write_text(self.cookie_secret + "\n", encoding="utf-8")
        chmod_private(cookie)
        return secret

    def bump_session_version(self) -> int:
        self.session_version += 1
        path = self._path(VERSION_NAME)
        path.write_text(f"{self.session_version}\n", encoding="utf-8")
        chmod_private(path)
        return self.session_version

    def encrypt(self, payload: bytes) -> bytes:
        nonce = os.urandom(12)
        return nonce + AESGCM(self.face_key).encrypt(nonce, payload, None)

    def decrypt(self, blob: bytes) -> bytes:
        if len(blob) < 13:
            raise ValueError("密文过短")
        nonce, body = blob[:12], blob[12:]
        return AESGCM(self.face_key).decrypt(nonce, body, None)
