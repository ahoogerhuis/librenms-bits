"""Proxy daemon configuration.

Loaded from a YAML file (readable/commentable by a human editing it on
winrm-proxy), not JSON. All validation happens at startup and
fails loud (raises ConfigError) rather than silently falling back to an
insecure default -- a misconfigured proxy should refuse to start, not
serve requests in a weaker mode than intended.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from pydantic import BaseModel, Field, model_validator


class ConfigError(Exception):
    """Raised for any startup configuration problem. Fatal by design."""


class Settings(BaseModel):
    listen_addr: str = "0.0.0.0"
    listen_port: int = 8443

    # Proxy's own server certificate, presented to pollers.
    tls_cert: str
    tls_key: str

    auth_mode: Literal["token", "mtls"]

    # token mode
    token: str | None = None

    # mtls mode -- CA that signs poller client certs. Pinned by SHA-256
    # fingerprint of the CA file's actual DER content, not trusted purely
    # by file path: a swapped file at the same path must fail, not
    # silently be trusted.
    allowed_client_ca: str | None = None
    allowed_client_ca_fingerprint: str | None = None

    # Explicit opt-in required to run mtls mode without a pinned CA
    # (encrypted-but-unauthenticated -- equivalent risk to skip-verify).
    # Never set true outside of local dev.
    insecure_dev_mode: bool = False

    # Concurrency limiting + explicit timeouts (2026-08-10, from a
    # cascading-failure review): the /check endpoint is a sync FastAPI
    # handler, so each in-flight WinRM call ties up a worker thread for
    # its full duration. With no bound on how many can be outstanding at
    # once, many hosts going dark simultaneously (a firewall change, a
    # DC outage) can exhaust every worker thread, making the proxy
    # unresponsive even to perfectly healthy hosts. max_concurrent_connections
    # bounds that directly, independent of solving session reuse/pooling
    # (a separate, deliberately deferred follow-up -- see
    # docs/WINRM_PROXY_CONCURRENCY.md, librenms-fork, for why).
    max_concurrent_connections: int = Field(default=10, gt=0)
    # How long a request waits for a free connection slot before giving
    # up with a clear "proxy at capacity" error, rather than queuing
    # indefinitely -- a semaphore alone still lets threads pile up
    # waiting for a slot forever under sustained overload; this bounds
    # that too.
    connection_queue_timeout_seconds: int = Field(default=30, gt=0)

    # Explicit, not left as pypsrp's unstated implicit defaults (which
    # this project only discovered by reading pypsrp's actual
    # WSMan.__init__ signature, not documentation -- operation_timeout=20,
    # connection_timeout=30, read_timeout=30). Defaults here match
    # pypsrp's own so this change doesn't silently alter behavior, only
    # makes the values visible/tunable in our own config.
    winrm_operation_timeout_seconds: int = Field(default=20, gt=0)
    winrm_connection_timeout_seconds: int = Field(default=30, gt=0)
    winrm_read_timeout_seconds: int = Field(default=30, gt=0)

    # Session pooling (2026-08-10) -- deferred at first alongside the
    # concurrency-limiting change above because it carries real
    # correctness risk (staleness, thread-safety, sizing/eviction; see
    # docs/WINRM_PROXY_CONCURRENCY.md, librenms-fork). Opt-in: default
    # False preserves the original "fresh session per call" behavior
    # exactly. When enabled, reuses a live RunspacePool per host across
    # calls instead of a full Kerberos handshake + PSRP negotiation
    # every time.
    session_pooling_enabled: bool = False
    max_pooled_sessions: int = Field(default=50, gt=0)
    pooled_session_idle_timeout_seconds: int = Field(default=600, gt=0)

    @model_validator(mode="after")
    def _validate_auth_mode(self) -> "Settings":
        if self.auth_mode == "token":
            if not self.token:
                raise ConfigError("auth_mode=token requires 'token' to be set")
            if len(self.token.encode()) < 32:
                raise ConfigError("token must be at least 32 bytes of opaque random data")

        elif self.auth_mode == "mtls":
            has_ca = bool(self.allowed_client_ca)
            if not has_ca:
                if not self.insecure_dev_mode:
                    raise ConfigError(
                        "auth_mode=mtls with no allowed_client_ca is "
                        "encrypted-but-unauthenticated -- set "
                        "insecure_dev_mode: true to run this way "
                        "deliberately (never in production)"
                    )
            else:
                if not self.allowed_client_ca_fingerprint:
                    raise ConfigError(
                        "allowed_client_ca is set but "
                        "allowed_client_ca_fingerprint is not -- the CA "
                        "must be pinned by fingerprint, not trusted by "
                        "file path alone"
                    )

        return self


def _sha256_fingerprint(path: Path) -> str:
    """Fingerprint of the DER-encoded certificate -- matches the value
    produced by `openssl x509 -in <path> -noout -fingerprint -sha256`
    (the standard/expected generation method, documented in
    config.example.yml). Deliberately NOT a hash of the raw PEM file
    bytes: PEM is base64-armored text wrapping the DER structure, so
    hashing the file directly produces a different, non-standard value
    that would never match what an operator generates with openssl.
    """
    try:
        cert = x509.load_pem_x509_certificate(path.read_bytes())
    except ValueError as e:
        raise ConfigError(f"allowed_client_ca ({path}) is not a valid PEM certificate: {e}")

    return cert.fingerprint(hashes.SHA256()).hex()


def verify_ca_fingerprint(settings: Settings) -> None:
    """Verify the CA file on disk still matches its pinned fingerprint.

    Called explicitly at startup (not inside the pydantic validator,
    since this does file I/O and we want a clear, separate failure mode
    for "config is internally inconsistent" vs "the pinned file has
    changed/is missing/doesn't match").
    """
    if settings.auth_mode != "mtls" or not settings.allowed_client_ca:
        return

    ca_path = Path(settings.allowed_client_ca)
    if not ca_path.is_file():
        raise ConfigError(f"allowed_client_ca file not found: {ca_path}")

    actual = _sha256_fingerprint(ca_path)
    expected = settings.allowed_client_ca_fingerprint.lower().replace(":", "")
    if actual != expected:
        raise ConfigError(
            f"allowed_client_ca ({ca_path}) fingerprint mismatch: "
            f"expected {expected}, got {actual} -- refusing to start. "
            "If this CA rotation is intentional, update "
            "allowed_client_ca_fingerprint to match."
        )


def load_settings(path: str | Path) -> Settings:
    path = Path(path)
    if not path.is_file():
        raise ConfigError(f"config file not found: {path}")

    with path.open() as f:
        raw = yaml.safe_load(f) or {}

    settings = Settings(**raw)
    verify_ca_fingerprint(settings)
    return settings
