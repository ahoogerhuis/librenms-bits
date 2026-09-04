# WinRM Proxy Daemon — Containerization

**Contents**
- [Scope](#scope)
- [What changed and why](#what-changed-and-why)
- [Provisioning, revised](#provisioning-revised)
- [Restart / identity behavior](#restart-identity-behavior)
- [Multi-domain (v2)](#multi-domain-v2)
- [Registry](#registry)
- [Deviations from the first sketch of this doc, made during implementation](#deviations-from-the-first-sketch-of-this-doc-made-during-implementation)
- [Known gotcha: Compose secrets and file permissions](#known-gotcha-compose-secrets-and-file-permissions)

<a id="scope"></a>

## Scope
This addendum covers **only the Python proxy daemon** (built on `pypsrp`, not `pywinrm` -- see `app/winrm_executor_pypsrp.py`'s docstring for why). It does not change:
- **JEA endpoints** — stay native PowerShell, registered per-host on each monitored Windows server. Not containerized, not touched by this doc.
- **`WinrmPoller.php`** — runs inside the existing LibreNMS poller process, not a separate service.

See `docs/WINRM_DESIGN.md` (on `feature/winrm-poller-claude`, `alexh/librenms-fork`) for the full architecture. This doc revises the proxy's deployment model only.

<a id="what-changed-and-why"></a>

## What changed and why

The original design assumed the proxy needed a **full AD domain-join** (`realmd`, `sssd-ad`, `adcli`, computer-account trust relationship) so it could act as a Kerberos client. That's more than it actually needs.

**The proxy only needs a Kerberos ticket** to authenticate outbound WinRM calls. That requires:
- `krb5.conf` pointing at the realm/KDC
- A **keytab for a plain service account** (not a computer account — no machine trust relationship)
- `kinit -kt` at container startup (and before ticket expiry)

`sssd`/`realmd` exist for NSS/PAM — resolving domain users for interactive login, `sudo`, etc. The proxy never does interactive login, so that layer isn't needed. This simplification stands regardless of whether the proxy runs in a container or on a VM — but it's what makes containerizing clean, since there's no persistent machine identity to preserve across container recreation.

<a id="provisioning-revised"></a>

## Provisioning, revised

**Playbook 1 (AD-join) shrinks to a one-time AD-side action**, not a target-host run:
- Create the service account in AD
- Generate a keytab for it (`ktpass` on a DC, or `net ads keytab` from a domain-joined Linux box)
- No `realm join`, no `sssd`, no computer account

**Playbook 2 becomes "build image, deploy via `docker-compose`"**, not "install a WinRM stack on a domain-joined box."

<a id="restart-identity-behavior"></a>

## Restart / identity behavior

No persistent machine identity is tied to the container (no computer account, unlike a real domain-join). This means:
- The container can be recreated freely — rebuilt, restarted, moved to a different host — without any AD-side cleanup or re-join step
- Only the keytab and certs need to persist (via the secrets/volume mounts), not the container itself

<a id="multi-domain-v2"></a>

## Multi-domain (v2)

The earlier v2 design was "one proxy VM per domain." With containerization:

- **One compose service per domain**, each with its own `krb5.conf` (or realm-specific `KRB5_CONFIG` override) and its own keytab
- All services can run on a **single small Docker host** — e.g. `winrm-proxy` — rather than provisioning a new VM per domain
- Config on the LibreNMS side is unchanged: `$config['winrm']['proxies']` already supports a keyed map with device-group assignment; just point each domain's device groups at its own compose service's URL/port

<a id="registry"></a>

## Registry

Container images are built and pushed to `git.vpp.local`'s Forgejo container registry, under the `vpp` org namespace: `git.vpp.local/vpp/librenms-winrm-proxy:<tag>`. Confirmed working 2026-08-08 (login, push, pull, list, delete all verified via a throwaway test image before building the real one).

<a id="deviations-from-the-first-sketch-of-this-doc-made-during-implementation"></a>

## Deviations from the first sketch of this doc, made during implementation

- The proxy already reads its full configuration (auth mode, token, TLS paths, `insecure_dev_mode`, etc.) from a single mounted `config.yml` (see `proxy/app/config.py`, built and tested before this containerization work) rather than individual `AUTH_MODE`/`PROXY_TOKEN_FILE`-style environment variables. The Dockerfile/compose in this directory mount that same `config.yml` (and the TLS cert/key, and eventually the keytab) as files rather than reconstructing config from env vars -- reuses the already-verified config-loading and validation code as-is instead of adding a second, parallel env-var-based config path.
- Kerberos re-`kinit` strategy: implemented as a background loop in `entrypoint.sh` (re-`kinit`s periodically against the ticket lifetime) rather than app-level scheduling inside the Python process, since it doesn't need to be Python-aware and keeps the retry/loop logic outside the ASGI app's request-handling code entirely. Only actually runs when a keytab is configured; the image runs fine without one (stub-executor / pre-Kerberos development mode), matching how the daemon is used today.

<a id="known-gotcha-compose-secrets-and-file-permissions"></a>

## Known gotcha: Compose secrets and file permissions

Hit this for real, not a hypothetical: this host's `docker-compose` (2.26.1-4, Debian-packaged) silently ignores the `mode`/`uid`/`gid` options on service-level secret references — warns `secrets \`uid\`, \`gid\` and \`mode\` are not supported, they will be ignored` and does nothing. A secret's permissions *inside* the container end up being whatever the **source file's permissions are on the host**, not anything settable in `docker-compose.yml`.

Concretely: copying an existing `600` TLS private key into `secrets/tls/server.key` reproduced `PermissionError: [Errno 13] Permission denied` at container startup (loading the TLS cert chain), because the daemon runs as a non-root user (`winrm-proxy`, uid 999) and couldn't read a root-owned `600` file mounted read-only at `/run/secrets/proxy_tls_key`.

**Original fix here was wrong, corrected 2026-08-08.** This section originally said `chmod 644` everything under `secrets/`. That "worked" (container started) but made the TLS private key and the bearer-token-bearing `config.yml` **world-readable to every local user on the Docker host**, not just the container's own process — caught during a security self-review, not initially. `chmod`-ing to world-readable is not something Compose forces on you; it was this doc's own bad advice for working around the secret-mode gap.

**Correct fix:** the Dockerfile now pins the daemon's UID explicitly (999, `winrm-proxy` — previously left to `useradd`'s automatic allocation, which isn't guaranteed stable across rebuilds either). `chown` the host-side secret files to that same numeric UID and restrict them to owner-only, rather than opening them to everyone:
```bash
chown 999:999 secrets/config.yml secrets/tls/server.key secrets/tls/server.crt
chmod 600 secrets/config.yml secrets/tls/server.key
chmod 644 secrets/tls/server.crt   # public cert, fine to be world-readable
```
Documented directly in `docker-compose.yml`'s secrets comment and the proxy `README.md`'s deployment steps.
