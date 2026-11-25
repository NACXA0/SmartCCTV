from pathlib import Path

import pyotp

from server.auth import AuthService
from server.config import AuthConfig
from server.secrets import SecretStore


def test_totp_login_and_replay(tmp_path: Path):
    secrets = SecretStore(tmp_path / "secrets")
    auth = AuthService(secrets, AuthConfig(max_sessions=5, max_sessions_per_ip=3))
    code = pyotp.TOTP(secrets.totp_secret).now()
    ok = auth.login(code, "127.0.0.1")
    assert ok.ok
    again = auth.login(code, "127.0.0.1")
    assert not again.ok
    session = auth.read_token(ok.token)
    assert session is not None
