"""Shared MSSQL TLS policy helpers.

The per-connection exception never disables encryption: it only skips server
certificate-chain validation for legacy/private SQL Server installations.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal


MssqlTlsMode = Literal["verify", "trust_server_certificate"]
MSSQL_TLS_VERIFY: MssqlTlsMode = "verify"
MSSQL_TLS_TRUST_SERVER_CERTIFICATE: MssqlTlsMode = "trust_server_certificate"


def normalize_mssql_tls_mode(value: object) -> MssqlTlsMode:
    """Return the stored mode, falling back safely for legacy rows."""
    if value == MSSQL_TLS_TRUST_SERVER_CERTIFICATE:
        return MSSQL_TLS_TRUST_SERVER_CERTIFICATE
    return MSSQL_TLS_VERIFY


def get_mssql_tls_mode(connection: Any) -> MssqlTlsMode:
    """Read the TLS mode from either a mapping or an attribute object."""
    if isinstance(connection, Mapping):
        value = connection.get("mssql_tls_mode")
    else:
        value = getattr(connection, "mssql_tls_mode", None)
    return normalize_mssql_tls_mode(value)


def trusts_server_certificate(connection: Any) -> bool:
    return get_mssql_tls_mode(connection) == MSSQL_TLS_TRUST_SERVER_CERTIFICATE


def sqlalchemy_tls_query(connection: Any) -> str:
    """SQLAlchemy URL query fragment with encryption always mandatory."""
    trust = "yes" if trusts_server_certificate(connection) else "no"
    return f"Encrypt=Mandatory&TrustServerCertificate={trust}"
