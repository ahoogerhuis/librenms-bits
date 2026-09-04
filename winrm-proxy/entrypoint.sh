#!/bin/sh
# entrypoint.sh
#
# Kerberos setup is entirely conditional on a keytab actually being
# mounted -- with none present (WINRM_PROXY_KEYTAB unset, or the file
# missing), this drops straight to starting the daemon. That's the
# normal case today: no AD service-account keytab exists yet, so the
# proxy runs with --executor stub. Once a keytab is provisioned, mount
# it (see docker-compose.yml) and set WINRM_PROXY_KEYTAB/
# WINRM_PROXY_PRINCIPAL/WINRM_PROXY_EXECUTOR=pypsrp -- no rebuild
# needed, this script picks it up at container start.
set -eu

if [ -n "${WINRM_PROXY_KEYTAB:-}" ] && [ -f "$WINRM_PROXY_KEYTAB" ]; then
    if [ -z "${WINRM_PROXY_PRINCIPAL:-}" ]; then
        echo "entrypoint: WINRM_PROXY_KEYTAB is set but WINRM_PROXY_PRINCIPAL is not -- refusing to start" >&2
        exit 1
    fi

    echo "entrypoint: obtaining initial Kerberos ticket for $WINRM_PROXY_PRINCIPAL"
    kinit -kt "$WINRM_PROXY_KEYTAB" "$WINRM_PROXY_PRINCIPAL"

    # Re-kinit well inside typical AD ticket lifetimes (commonly ~10h)
    # rather than relying on renewal alone. A plain background loop,
    # not systemd-timer-equivalent app-level scheduling -- doesn't need
    # to be Python/asyncio-aware, and keeps retry logic out of the
    # ASGI app's request-handling code entirely. This becomes an
    # orphaned child once the exec below replaces this shell as PID 1;
    # accepted tradeoff for a lightweight image with no init system --
    # Docker's stop timeout reaps it along with everything else.
    (
        while true; do
            sleep 1800
            echo "entrypoint: renewing Kerberos ticket for $WINRM_PROXY_PRINCIPAL"
            kinit -kt "$WINRM_PROXY_KEYTAB" "$WINRM_PROXY_PRINCIPAL" || echo "entrypoint: kinit renewal failed, will retry" >&2
        done
    ) &
else
    echo "entrypoint: no keytab configured, skipping Kerberos setup (stub-executor mode)"
fi

exec python -m app.main \
    --config "$WINRM_PROXY_CONFIG" \
    --executor "$WINRM_PROXY_EXECUTOR" \
    --jea-configuration-name "${WINRM_PROXY_JEA_CONFIG_NAME:-WinrmProbe}"
