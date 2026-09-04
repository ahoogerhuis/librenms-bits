import datetime

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ed25519
from cryptography.x509.oid import NameOID

from app.config import ConfigError, Settings, load_settings, verify_ca_fingerprint

VALID_TOKEN = "x" * 32


def _make_test_cert_pem() -> tuple[bytes, str]:
    """Build a minimal self-signed cert and return (pem_bytes, sha256_hex).

    Real PEM/DER content, not a placeholder string -- config.py's
    fingerprint check parses this as an actual X.509 certificate (it
    must, to match `openssl x509 -fingerprint`'s DER-based behaviour,
    see the bug this caught: fingerprinting raw PEM file bytes instead
    of the DER cert content never matches what openssl produces).
    """
    key = ed25519.Ed25519PrivateKey.generate()
    subject = issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "test-ca")])
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.datetime.now(datetime.timezone.utc))
        .not_valid_after(
            datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=1)
        )
        .sign(key, algorithm=None)
    )
    pem = cert.public_bytes(serialization.Encoding.PEM)
    fingerprint = cert.fingerprint(hashes.SHA256()).hex()
    return pem, fingerprint


def test_token_mode_requires_token():
    with pytest.raises(ConfigError, match="requires 'token'"):
        Settings(tls_cert="c", tls_key="k", auth_mode="token")


def test_token_mode_rejects_short_token():
    with pytest.raises(ConfigError, match="at least 32 bytes"):
        Settings(tls_cert="c", tls_key="k", auth_mode="token", token="short")


def test_token_mode_accepts_valid_config():
    s = Settings(tls_cert="c", tls_key="k", auth_mode="token", token=VALID_TOKEN)
    assert s.token == VALID_TOKEN


def test_mtls_without_ca_requires_insecure_dev_mode():
    with pytest.raises(ConfigError, match="insecure_dev_mode"):
        Settings(tls_cert="c", tls_key="k", auth_mode="mtls")


def test_mtls_without_ca_allowed_with_insecure_dev_mode():
    s = Settings(tls_cert="c", tls_key="k", auth_mode="mtls", insecure_dev_mode=True)
    assert s.allowed_client_ca is None


def test_mtls_with_ca_requires_fingerprint():
    with pytest.raises(ConfigError, match="must be pinned by fingerprint"):
        Settings(tls_cert="c", tls_key="k", auth_mode="mtls", allowed_client_ca="/some/ca.pem")


def test_ca_fingerprint_mismatch_rejected(tmp_path):
    pem, _real_fingerprint = _make_test_cert_pem()
    ca_file = tmp_path / "ca.pem"
    ca_file.write_bytes(pem)

    settings = Settings(
        tls_cert="c",
        tls_key="k",
        auth_mode="mtls",
        allowed_client_ca=str(ca_file),
        allowed_client_ca_fingerprint="0" * 64,
    )
    with pytest.raises(ConfigError, match="fingerprint mismatch"):
        verify_ca_fingerprint(settings)


def test_ca_fingerprint_match_accepted(tmp_path):
    pem, real_fingerprint = _make_test_cert_pem()
    ca_file = tmp_path / "ca.pem"
    ca_file.write_bytes(pem)

    settings = Settings(
        tls_cert="c",
        tls_key="k",
        auth_mode="mtls",
        allowed_client_ca=str(ca_file),
        allowed_client_ca_fingerprint=real_fingerprint,
    )
    verify_ca_fingerprint(settings)  # should not raise


def test_ca_fingerprint_accepts_colon_separated_form(tmp_path):
    pem, hex_fp = _make_test_cert_pem()
    ca_file = tmp_path / "ca.pem"
    ca_file.write_bytes(pem)
    colon_fp = ":".join(hex_fp[i : i + 2] for i in range(0, len(hex_fp), 2))

    settings = Settings(
        tls_cert="c",
        tls_key="k",
        auth_mode="mtls",
        allowed_client_ca=str(ca_file),
        allowed_client_ca_fingerprint=colon_fp,
    )
    verify_ca_fingerprint(settings)  # should not raise


def test_ca_file_not_valid_pem_rejected(tmp_path):
    ca_file = tmp_path / "ca.pem"
    ca_file.write_bytes(b"not a certificate at all")

    settings = Settings(
        tls_cert="c",
        tls_key="k",
        auth_mode="mtls",
        allowed_client_ca=str(ca_file),
        allowed_client_ca_fingerprint="0" * 64,
    )
    with pytest.raises(ConfigError, match="not a valid PEM certificate"):
        verify_ca_fingerprint(settings)


def test_load_settings_missing_file(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        load_settings(tmp_path / "does-not-exist.yml")


def test_load_settings_from_yaml(tmp_path):
    config_file = tmp_path / "config.yml"
    config_file.write_text(
        f"""
        listen_port: 8443
        tls_cert: /tmp/cert.pem
        tls_key: /tmp/key.pem
        auth_mode: token
        token: "{VALID_TOKEN}"
        """
    )
    settings = load_settings(config_file)
    assert settings.auth_mode == "token"
    assert settings.listen_port == 8443
