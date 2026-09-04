# syntax=docker/dockerfile:1

# Build stage: compile the kerberos C extension (pykerberos, via
# pypsrp[kerberos]) against libkrb5-dev, then discard the build
# toolchain -- the runtime image only needs the compiled result plus
# krb5's runtime libs and kinit/klist, not gcc/headers.
FROM python:3.13-slim AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc \
        libkrb5-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY pyproject.toml ./
COPY app ./app

RUN python -m venv /venv
ENV PATH="/venv/bin:$PATH"
RUN pip install --no-cache-dir .

# Runtime stage
FROM python:3.13-slim

# UID/GID pinned explicitly (999), not left to useradd's automatic
# system-ID allocation. Host-side secret files are chowned to this
# same numeric UID (see docker-compose.yml/CONTAINERIZATION.md) so
# only this container's process can read them -- Docker doesn't
# isolate UIDs from the host by default (no user-namespace remap
# configured here), so an unpinned UID could silently shift on a
# future rebuild and break that permission scheme without any error.
RUN apt-get update && apt-get install -y --no-install-recommends \
        krb5-user \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid 999 winrm-proxy \
    && useradd --system --uid 999 --gid winrm-proxy --no-create-home --shell /usr/sbin/nologin winrm-proxy

COPY --from=builder /venv /venv
ENV PATH="/venv/bin:$PATH"

WORKDIR /app
COPY app ./app
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Config/certs/keytab are mounted at runtime, not baked into the image
# -- see docker-compose.yml. Default KRB5_CONFIG points at a mount
# point; entrypoint.sh only touches Kerberos at all if a keytab is
# actually mounted, so the image runs fine with none of this present
# (stub-executor / pre-Kerberos development mode).
ENV KRB5_CONFIG=/etc/krb5.conf \
    KRB5CCNAME=/tmp/krb5cc_winrm-proxy \
    WINRM_PROXY_CONFIG=/etc/librenms-winrm-proxy/config.yml \
    WINRM_PROXY_EXECUTOR=stub \
    WINRM_PROXY_KEYTAB= \
    WINRM_PROXY_PRINCIPAL= \
    WINRM_PROXY_JEA_CONFIG_NAME=WinrmProbe

USER winrm-proxy

EXPOSE 8443

ENTRYPOINT ["/entrypoint.sh"]
