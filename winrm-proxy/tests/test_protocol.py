"""Protocol-shape enforcement: JSON only, {host, check_name} only, and
check_name must be whitelisted -- these are the hard boundary against
ever running caller-supplied free-form script.
"""

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.winrm_executor_stub import WinrmExecutorStub

TOKEN = "a" * 32


def _client() -> TestClient:
    settings = Settings(tls_cert="c", tls_key="k", auth_mode="token", token=TOKEN)
    return TestClient(create_app(settings, WinrmExecutorStub()))


def _post(client, body):
    return client.post("/check", json=body, headers={"Authorization": f"Bearer {TOKEN}"})


def test_unknown_check_name_rejected():
    resp = _post(_client(), {"host": "example", "check_name": "does_not_exist"})
    assert resp.status_code == 400


def test_extra_field_rejected():
    # Most important protocol test: this is what stops the proxy from
    # ever accepting a free-form script field alongside a valid check_name.
    resp = _post(
        _client(),
        {"host": "example", "check_name": "reboot-pending", "script": "rm -rf /"},
    )
    assert resp.status_code == 422


def test_missing_host_rejected():
    resp = _post(_client(), {"check_name": "reboot-pending"})
    assert resp.status_code == 422


def test_missing_check_name_rejected():
    resp = _post(_client(), {"host": "example"})
    assert resp.status_code == 422


def test_non_json_body_rejected():
    client = _client()
    resp = client.post(
        "/check",
        content=b"not json",
        headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"},
    )
    assert resp.status_code == 422


def test_valid_request_reaches_check():
    resp = _post(_client(), {"host": "example", "check_name": "reboot-pending"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["value"] == {"reboot_pending": False}
