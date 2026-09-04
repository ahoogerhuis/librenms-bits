"""Request-level auth tests, via a real TestClient/ASGI transport.

Note: TestClient talks to the app over ASGI directly, not real
sockets/TLS -- so mtls's actual enforcement (uvicorn's
ssl_cert_reqs=CERT_REQUIRED against the pinned CA, see main.py) is NOT
exercised here. That's a real TLS-layer behaviour that can only be
verified with a real running instance (e.g. `openssl s_client`/`curl
--cert` against winrm-proxy) -- see the plan's A4 testing
notes. What IS tested here is everything at the app layer: token mode
end to end, and that mtls mode's request handler doesn't add its own
(redundant, and easy to get subtly wrong) app-layer cert check on top
of what TLS already guarantees.
"""

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.winrm_executor_stub import WinrmExecutorStub

TOKEN = "a" * 32


def _client(settings: Settings) -> TestClient:
    return TestClient(create_app(settings, WinrmExecutorStub()))


def test_token_mode_accepts_correct_token():
    settings = Settings(tls_cert="c", tls_key="k", auth_mode="token", token=TOKEN)
    client = _client(settings)
    resp = client.post(
        "/check",
        json={"host": "example", "check_name": "reboot-pending"},
        headers={"Authorization": f"Bearer {TOKEN}"},
    )
    assert resp.status_code == 200


def test_token_mode_rejects_wrong_token():
    settings = Settings(tls_cert="c", tls_key="k", auth_mode="token", token=TOKEN)
    client = _client(settings)
    resp = client.post(
        "/check",
        json={"host": "example", "check_name": "reboot-pending"},
        headers={"Authorization": "Bearer wrong-token-wrong-token-wrong"},
    )
    assert resp.status_code == 401


def test_token_mode_rejects_missing_header():
    settings = Settings(tls_cert="c", tls_key="k", auth_mode="token", token=TOKEN)
    client = _client(settings)
    resp = client.post("/check", json={"host": "example", "check_name": "reboot-pending"})
    assert resp.status_code == 401


def test_token_mode_rejects_non_bearer_scheme():
    settings = Settings(tls_cert="c", tls_key="k", auth_mode="token", token=TOKEN)
    client = _client(settings)
    resp = client.post(
        "/check",
        json={"host": "example", "check_name": "reboot-pending"},
        headers={"Authorization": f"Token {TOKEN}"},
    )
    assert resp.status_code == 401


def test_mtls_mode_has_no_redundant_app_layer_check():
    # In mtls mode with insecure_dev_mode (no CA), the app layer should
    # impose no auth requirement of its own -- the request should reach
    # the check handler unconditionally, since TLS-layer enforcement is
    # the real (and, in this insecure_dev_mode case, deliberately absent)
    # boundary.
    settings = Settings(tls_cert="c", tls_key="k", auth_mode="mtls", insecure_dev_mode=True)
    client = _client(settings)
    resp = client.post("/check", json={"host": "example", "check_name": "reboot-pending"})
    assert resp.status_code == 200
