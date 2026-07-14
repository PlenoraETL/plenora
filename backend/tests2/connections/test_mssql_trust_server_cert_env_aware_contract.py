"""
Contract test: TrustServerCertificate ODBC SQL Server per-connessione.

Bug originale (plenora-bug-connections P3):
  `connections/service.py:_test_mssql` hardcoded `TrustServerCertificate=yes`
  in tutte le connection string ODBC, accettando self-signed/MITM
  arbitrari anche in produzione. Equivalente a `verify=False` su HTTP.

Post-fix: default ``verify`` in ogni ambiente; l'eccezione esplicita
``trust_server_certificate`` mantiene comunque la cifratura obbligatoria.
"""

from __future__ import annotations

import inspect
import textwrap

from tests2.shared._ast_identifiers import all_identifiers


def test_mssql_test_connection_uses_per_connection_gate():
    """`_test_mssql` deve consultare la policy della singola connessione."""
    from app.core.connections import service

    source = textwrap.dedent(inspect.getsource(service.ConnectionService._test_mssql))

    # RAW: stringa literal con caratteri non-identificatore (="yes" con virgolette)
    assert "TrustServerCertificate=yes\"" not in source, (
        "TrustServerCertificate=yes hardcoded e' un security regression: "
        "accetta MITM. Usa il gate per-connessione."
    )
    assert "trusts_server_certificate" in all_identifiers(source), (
        "Il fix MSSQL TrustServerCertificate deve delegare a "
        "trusts_server_certificate() per gating per-connessione."
    )


def test_tls_policy_blocks_bypass_in_prod():
    """Sanity sul gate stesso: prod-like env nega il bypass."""
    import os
    from app.shared.security import tls_policy

    saved_env = os.environ.get("ENVIRONMENT")
    saved_allow = os.environ.get("PLENORA_ALLOW_TLS_BYPASS")
    try:
        os.environ["ENVIRONMENT"] = "production"
        os.environ["PLENORA_ALLOW_TLS_BYPASS"] = "1"  # opt-in ignorato in prod
        assert tls_policy.tls_bypass_allowed() is False, (
            "In production tls_bypass_allowed deve essere False anche con "
            "PLENORA_ALLOW_TLS_BYPASS=1 (prod-like fail-closed)."
        )
    finally:
        if saved_env is None:
            os.environ.pop("ENVIRONMENT", None)
        else:
            os.environ["ENVIRONMENT"] = saved_env
        if saved_allow is None:
            os.environ.pop("PLENORA_ALLOW_TLS_BYPASS", None)
        else:
            os.environ["PLENORA_ALLOW_TLS_BYPASS"] = saved_allow


def test_mssql_all_paths_require_encryption_and_disable_legacy_fallbacks():
    from app.core.connections import service
    from app.core.estrai import database_inspector

    test_source = textwrap.dedent(
        inspect.getsource(service.ConnectionService._test_mssql)
    )
    inspector_source = textwrap.dedent(
        inspect.getsource(database_inspector.DefaultConnectionFactory._connetti_mssql)
    )

    for source in (test_source, inspector_source):
        assert "Encrypt=Mandatory" in source
        assert "Encrypt=Yes" in source
        assert "trusts_server_certificate" in all_identifiers(source)
        assert "Encrypt=Optional" not in source
        assert "Encrypt=No" not in source
    assert "pymssql" not in all_identifiers(test_source)


def test_real_query_paths_never_disable_mssql_encryption():
    from app.core.carica import executor, router
    from app.core.query_execution import query_engines

    sources = (
        textwrap.dedent(inspect.getsource(query_engines.DefaultEngineFactory._costruisci_connection_string)),
        textwrap.dedent(inspect.getsource(executor.CaricaExecutor._build_engine_manual)),
        textwrap.dedent(inspect.getsource(router._create_engine)),
    )
    for source in sources:
        assert "sqlalchemy_tls_query" in all_identifiers(source)
        assert "Encrypt=Optional" not in source
        assert "Encrypt=No" not in source
