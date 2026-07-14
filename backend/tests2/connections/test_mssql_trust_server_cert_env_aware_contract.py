"""
Contract test: TrustServerCertificate ODBC SQL Server gated da tls_policy.

Bug originale (plenora-bug-connections P3):
  `connections/service.py:_test_mssql` hardcoded `TrustServerCertificate=yes`
  in tutte le connection string ODBC, accettando self-signed/MITM
  arbitrari anche in produzione. Equivalente a `verify=False` su HTTP.

Post-fix: gating env-aware via `tls_policy.tls_bypass_allowed()`.
  - dev/CI o `PLENORA_ALLOW_TLS_BYPASS=1` → "yes" (back-compat dev)
  - production/staging → "no" (forza chain validation)
"""

from __future__ import annotations

import inspect
import textwrap

from tests2.shared._ast_identifiers import all_identifiers


def test_mssql_test_connection_uses_tls_policy_gate():
    """`_test_mssql` deve consultare `tls_bypass_allowed` per Trust* flag."""
    from app.core.connections import service

    source = textwrap.dedent(inspect.getsource(service.ConnectionService._test_mssql))

    # RAW: stringa literal con caratteri non-identificatore (="yes" con virgolette)
    assert "TrustServerCertificate=yes\"" not in source, (
        "TrustServerCertificate=yes hardcoded e' un security regression: "
        "in prod accetta MITM. Usa il gate env-aware tls_bypass_allowed()."
    )
    assert "tls_bypass_allowed" in all_identifiers(source), (
        "Il fix MSSQL TrustServerCertificate deve delegare a "
        "tls_policy.tls_bypass_allowed() per gating env-aware."
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


def test_mssql_prod_paths_require_encryption_and_disable_legacy_fallbacks():
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
        assert "tls_bypass_allowed" in all_identifiers(source)

    # Il fallback pymssql/FreeTDS non ha una verifica certificato equivalente
    # in questo boundary: in prod deve essere raggiungibile solo dopo il gate.
    gate_pos = test_source.index("if not tls_policy_module.tls_bypass_allowed()")
    pymssql_pos = test_source.index("import pymssql")
    assert gate_pos < pymssql_pos
