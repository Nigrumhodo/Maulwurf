"""Riva endpoint format and TLS trust fail closed outside local."""

from __future__ import annotations

from pathlib import Path

import pytest

from maulwurf_ingest.asr.riva_client import (
    GrpcRivaTransport,
    resolve_riva_tls,
    riva_transport_from_env,
    validate_riva_server,
)
from maulwurf_ingest.config import settings


def test_server_must_be_host_and_port() -> None:
    assert validate_riva_server("grpc.nvcf.nvidia.com:443") == "grpc.nvcf.nvidia.com:443"
    assert validate_riva_server(" 127.0.0.1:50051") == "127.0.0.1:50051"
    assert validate_riva_server("[::1]:443") == "[::1]:443"
    for bad in (
        "https://grpc.nvcf.nvidia.com:443",
        "grpc.nvcf.nvidia.com",
        "host:0",
        "host:65536",
        "a b:443",
        "",
        "tcp://127.0.0.1:9",
    ):
        with pytest.raises(RuntimeError, match="riva_server_invalid"):
            validate_riva_server(bad)


def test_plaintext_is_refused_outside_local(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("INGEST_ENV", raising=False)
    monkeypatch.setattr(settings, "environment", "production")
    with pytest.raises(RuntimeError, match="riva_tls_required"):
        GrpcRivaTransport(
            api_key="not-a-real-key", server="127.0.0.1:50051", function_id="fn", use_ssl=False,
        )


def test_plaintext_is_allowed_only_for_local(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INGEST_ENV", "local")
    transport = GrpcRivaTransport(
        api_key="not-a-real-key", server="127.0.0.1:50051", function_id="fn", use_ssl=False,
    )
    assert transport._use_ssl is False  # noqa: SLF001 — the flag is the contract under test


def test_an_unconfigured_roots_override_is_refused(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setattr(settings, "riva_ca_file", None)
    monkeypatch.delenv("INGEST_RIVA_CA_FILE", raising=False)
    monkeypatch.setenv("GRPC_DEFAULT_SSL_ROOTS_FILE_PATH", str(tmp_path / "attacker.pem"))
    with pytest.raises(RuntimeError, match="riva_roots_overridden"):
        resolve_riva_tls(use_ssl=True, ca_file=None)


def test_explicit_ca_must_exist_and_match_any_override(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    ca = tmp_path / "ca.pem"
    other = tmp_path / "other.pem"
    ca.write_bytes(b"dummy")
    other.write_bytes(b"other")
    monkeypatch.delenv("GRPC_DEFAULT_SSL_ROOTS_FILE_PATH", raising=False)
    with pytest.raises(RuntimeError, match="riva_ca_unreadable"):
        resolve_riva_tls(use_ssl=True, ca_file=str(tmp_path / "missing.pem"))
    assert resolve_riva_tls(use_ssl=True, ca_file=str(ca)) == str(ca)
    monkeypatch.setenv("GRPC_DEFAULT_SSL_ROOTS_FILE_PATH", str(other))
    with pytest.raises(RuntimeError, match="riva_roots_overridden"):
        resolve_riva_tls(use_ssl=True, ca_file=str(ca))
    monkeypatch.setenv("GRPC_DEFAULT_SSL_ROOTS_FILE_PATH", str(ca))
    assert resolve_riva_tls(use_ssl=True, ca_file=str(ca)) == str(ca)


def test_system_store_is_used_when_nothing_overrides_it(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GRPC_DEFAULT_SSL_ROOTS_FILE_PATH", raising=False)
    monkeypatch.delenv("INGEST_RIVA_CA_FILE", raising=False)
    monkeypatch.setattr(settings, "riva_ca_file", None)
    assert resolve_riva_tls(use_ssl=True, ca_file=None) is None


def test_factory_rejects_a_url_and_plaintext(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NVIDIA_API_KEY", "not-a-real-key")
    monkeypatch.delenv("INGEST_ENV", raising=False)
    monkeypatch.setattr(settings, "environment", "production")
    monkeypatch.setenv("RIVA_SERVER", "https://collector.invalid/asr")
    monkeypatch.delenv("RIVA_USE_SSL", raising=False)
    with pytest.raises(RuntimeError, match="riva_server_invalid"):
        riva_transport_from_env()
    monkeypatch.setenv("RIVA_SERVER", "grpc.nvcf.nvidia.com:443")
    monkeypatch.setenv("RIVA_USE_SSL", "false")
    with pytest.raises(RuntimeError, match="riva_tls_required"):
        riva_transport_from_env()
    monkeypatch.setenv("RIVA_USE_SSL", "maybe")
    with pytest.raises(RuntimeError, match="riva_tls_required"):
        riva_transport_from_env()
