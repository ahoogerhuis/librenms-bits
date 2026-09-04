"""LibreNMS WinRM proxy daemon.

POST /check {host, check_name} -> CheckResult. Authenticated relay
only -- see app/auth.py and app/winrm_executor.py module docstrings
for where the real security boundaries actually live.
"""

from __future__ import annotations

import argparse
import ssl
import sys

import uvicorn
from fastapi import Depends, FastAPI, HTTPException

from app.auth import require_auth
from app.checks.registry import get_check
from app.config import ConfigError, Settings, load_settings
from app.models import CheckRequest, CheckResult
from app.winrm_executor import WinrmExecutor
from app.winrm_executor_pypsrp import WinrmExecutorPypsrp
from app.winrm_executor_stub import WinrmExecutorStub


def create_app(settings: Settings, executor: WinrmExecutor) -> FastAPI:
    app = FastAPI(title="librenms-winrm-proxy")
    auth_dependency = require_auth(settings)

    @app.post("/check", response_model=CheckResult, dependencies=[Depends(auth_dependency)])
    def check(req: CheckRequest) -> CheckResult:
        check_fn = get_check(req.check_name)
        if check_fn is None:
            raise HTTPException(status_code=400, detail=f"unknown check_name: {req.check_name!r}")

        return check_fn(executor, req.host)

    return app


def _build_ssl_kwargs(settings: Settings) -> dict:
    kwargs = {
        "ssl_certfile": settings.tls_cert,
        "ssl_keyfile": settings.tls_key,
    }

    if settings.auth_mode == "mtls" and settings.allowed_client_ca:
        # The real client-auth boundary: TLS itself refuses any
        # handshake that doesn't present a cert signed by this pinned
        # CA. Requests that reach app code have already passed this.
        kwargs["ssl_ca_certs"] = settings.allowed_client_ca
        kwargs["ssl_cert_reqs"] = ssl.CERT_REQUIRED
    else:
        kwargs["ssl_cert_reqs"] = ssl.CERT_NONE

    return kwargs


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, help="path to config.yml")
    parser.add_argument(
        "--executor",
        required=True,
        choices=["stub", "pypsrp"],
        help="stub: canned responses, no keytab/JEA target needed. "
        "pypsrp: real Kerberos/JEA calls via the pypsrp library -- "
        "requires a Kerberos keytab and a target host with a "
        "registered JEA endpoint (see winrm_executor_pypsrp.py).",
    )
    parser.add_argument(
        "--jea-configuration-name",
        default="WinrmProbe",
        help="Name of the registered JEA PSSessionConfiguration to connect "
        "to on target hosts (--executor pypsrp only). Must match the "
        "-Name used with Register-PSSessionConfiguration on each target.",
    )
    args = parser.parse_args()

    try:
        settings = load_settings(args.config)
    except ConfigError as e:
        print(f"config error: {e}", file=sys.stderr)
        raise SystemExit(1)

    executor: WinrmExecutor
    if args.executor == "stub":
        executor = WinrmExecutorStub()
    else:
        executor = WinrmExecutorPypsrp(
            jea_configuration_name=args.jea_configuration_name,
            max_concurrent_connections=settings.max_concurrent_connections,
            connection_queue_timeout_seconds=settings.connection_queue_timeout_seconds,
            operation_timeout_seconds=settings.winrm_operation_timeout_seconds,
            connection_timeout_seconds=settings.winrm_connection_timeout_seconds,
            read_timeout_seconds=settings.winrm_read_timeout_seconds,
            session_pooling_enabled=settings.session_pooling_enabled,
            max_pooled_sessions=settings.max_pooled_sessions,
            pooled_session_idle_timeout_seconds=settings.pooled_session_idle_timeout_seconds,
        )

    app = create_app(settings, executor)

    uvicorn.run(
        app,
        host=settings.listen_addr,
        port=settings.listen_port,
        **_build_ssl_kwargs(settings),
    )


if __name__ == "__main__":
    main()
