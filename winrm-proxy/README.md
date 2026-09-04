# librenms-winrm-proxy

**Contents**
- [Status](#status)
- [Dev setup](#dev-setup)
- [Run tests](#run-tests)
- [Run the daemon locally (stub executor, no domain-join needed)](#run-the-daemon-locally-stub-executor-no-domain-join-needed)
- [Container deployment](#container-deployment)
- [Adding a new check](#adding-a-new-check)

Authenticated relay: LibreNMS poller --HTTPS(token/mTLS)--> this daemon --Kerberos/WinRM--> target's own JEA endpoint. Never holds broad execution rights itself -- the real security boundary is the per-host JEA endpoint. See `docs/WINRM_DESIGN.md` on `feature/winrm-poller-claude` (`alexh/librenms-fork`) for the full architecture.

<a id="status"></a>

## Status

Auth, protocol, whitelist, and the real Kerberos/JEA executor (`app/winrm_executor_pypsrp.py`, built on `pypsrp` -- not `pywinrm`, which turned out not to support connecting to a named JEA `PSSessionConfiguration` at all, see the module's own docstring) are all complete and validated end-to-end against a real Windows target, not just tested against the stub. See `CONTAINERIZATION.md` for the keytab-only (no domain-join) proxy-side plan, and `WINRM_JEA_WINDOWS.md` for the Windows-side JEA module/GPO build.

Checks implemented: `reboot-pending`, `service-status-wuauserv`, `service-status-w32time`.

<a id="dev-setup"></a>

## Dev setup

```bash
python3 -m venv venv
source venv/bin/activate
pip install -e '.[dev]'
```

<a id="run-tests"></a>

## Run tests

```bash
pytest
```

<a id="run-the-daemon-locally-stub-executor-no-domain-join-needed"></a>

## Run the daemon locally (stub executor, no domain-join needed)

```bash
cp config.example.yml /tmp/config.yml
# edit /tmp/config.yml -- at minimum set tls_cert/tls_key to a self-signed
# test cert pair, and a real 32+ byte token
python -m app.main --config /tmp/config.yml --executor stub
```

Then from another shell:

```bash
curl -k https://localhost:8443/check \
    -H "Authorization: Bearer <token from config.yml>" \
    -H "Content-Type: application/json" \
    -d '{"host": "test-host", "check_name": "reboot-pending"}'
```

<a id="container-deployment"></a>

## Container deployment

Images are pushed to Forgejo's container registry at `git.vpp.local/vpp/librenms-winrm-proxy`.

```bash
mkdir -p secrets/tls
cp config.example.yml secrets/config.yml
# edit secrets/config.yml -- set tls_cert/tls_key to /run/secrets/proxy_tls_cert
# and /run/secrets/proxy_tls_key (the container-deployment paths, see the
# note at the top of config.example.yml), and a real 32+ byte token
openssl req -x509 -newkey rsa:2048 -keyout secrets/tls/server.key \
    -out secrets/tls/server.crt -days 365 -nodes -subj "/CN=<proxy-hostname>"

# Required: restrict these to the container's user (uid 999, pinned in
# the Dockerfile), not world-readable. This compose version ignores
# secret mode/uid/gid options -- see docker-compose.yml's comment.
chown 999:999 secrets/config.yml secrets/tls/server.key secrets/tls/server.crt
chmod 600 secrets/config.yml secrets/tls/server.key
chmod 644 secrets/tls/server.crt

docker compose build
docker compose up -d
docker compose logs -f
```

`secrets/` is gitignored -- never commit real config/certs/keytabs there.

To push a new build to the registry:

```bash
docker login git.vpp.local   # Forgejo personal access token, write:package + read:package scope
docker compose build
docker compose push
```

<a id="adding-a-new-check"></a>

## Adding a new check

1. Add `app/checks/<name>.py` with a `run(executor: WinrmExecutor, host: str) -> CheckResult` function. Only use `Get-ItemProperty`-class read-only cmdlets that the target's JEA role capability actually whitelists -- this proxy has no way to enforce that from its own side, the JEA endpoint is what enforces it.
2. Register it in `app/checks/registry.py`'s `CHECKS` dict.
3. Add tests mirroring `tests/test_checks_reboot_pending.py`.

Never add a check that accepts caller-supplied script/command content -- `check_name` must always map to a fixed, reviewed PowerShell command.
