"""
Contract test per l'hardening dell'endpoint `GET /admin/runtime-config`.

Cluster bug coperti (plenora-bug-admin-api):

  1. P1 service.py:258-269 — `_runtime_snapshot()` esponeva sempre
     `hostname`, `docker_socket_mounted`, `compose_project`,
     `container_name`. Anche con `require_superadmin`, un admin
     compromesso ne ricava la mappa di deployment interno per lateral
     movement. Post-fix: dati di topologia visibili solo in dev/test
     sotto chiave `_dev_topology`.

  2. P1 service.py:83 — `MINIO_ENDPOINT_URL` non era redatto perche'
     nessuna needle in `_SECRET_NEEDLES` matchava. Post-fix: aggiunta
     `"ENDPOINT"` alle needle.

  3. P1 router.py — mancava `@audit_action`: estrazione completa della
     configurazione (DATABASE_URL host, broker Redis, ecc.) da admin
     compromesso non lasciava traccia per forensics. Post-fix:
     `@audit_action(action="admin.runtime_config.read")`.
"""

from __future__ import annotations

import pytest

from app.core.admin_runtime_config import router as runtime_router
from app.core.admin_runtime_config import service


pytestmark = [
    pytest.mark.unit,
    pytest.mark.security,
    pytest.mark.contract,
    pytest.mark.regression,
]


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in ("ENVIRONMENT", "ENV"):
        monkeypatch.delenv(name, raising=False)


class TestSecretNeedlesIncludesEndpoint:
    """`_is_secret_key` deve oscurare le chiavi `*ENDPOINT*`.

    Pre-fix `MINIO_ENDPOINT_URL` (e qualunque altra `_ENDPOINT_`)
    finiva nella risposta in chiaro.
    """

    @pytest.mark.parametrize("key", [
        "MINIO_ENDPOINT_URL",
        "S3_ENDPOINT_URL",
        "REDIS_ENDPOINT",
        "CELERY_BROKER_ENDPOINT",
    ])
    def test_endpoint_keys_are_treated_as_secret(self, key):
        assert service._is_secret_key(key) is True, (
            f"Chiavi che terminano/contengono ENDPOINT devono essere "
            f"oscurate; {key!r} non lo era."
        )

    def test_unrelated_keys_are_not_redacted(self):
        # Sanity: il match deve essere sostantivo, non degradare a
        # "tutto secret" (verrebbe ridotto il valore informativo).
        assert service._is_secret_key("MAX_FILE_SIZE") is False
        assert service._is_secret_key("CELERY_QUEUE_DEFAULT") is False


class TestRuntimeSnapshotHidesTopologyInProd:
    """`_runtime_snapshot` deve nascondere hostname/docker socket/compose
    project/container name in prod/staging.
    """

    @pytest.mark.parametrize("prod_env", ["production", "prod", "staging", "stage"])
    def test_prod_or_staging_excludes_topology_keys(self, prod_env, monkeypatch):
        monkeypatch.setenv("ENVIRONMENT", prod_env)
        snapshot = service._runtime_snapshot()

        # Solo `note` deve essere presente. Niente sotto-chiavi di topologia.
        assert "_dev_topology" not in snapshot
        for sensitive in (
            "hostname",
            "docker_socket_mounted",
            "compose_project",
            "container_name",
        ):
            assert sensitive not in snapshot, (
                f"`{sensitive}` esposto in env={prod_env!r}: info leak "
                f"verso admin compromesso."
            )

    @pytest.mark.parametrize("dev_env", ["development", "dev", "test"])
    def test_dev_envs_expose_topology_under_dev_key(self, dev_env, monkeypatch):
        monkeypatch.setenv("ENVIRONMENT", dev_env)
        snapshot = service._runtime_snapshot()

        # In dev la topologia resta visibile sotto chiave dedicata per
        # debug locale, ma e' isolata dal payload principale.
        assert "_dev_topology" in snapshot
        topology = snapshot["_dev_topology"]
        assert "hostname" in topology
        assert "docker_socket_mounted" in topology


class TestRouterHasAuditDecorator:
    """L'endpoint `GET /admin/runtime-config` deve essere coperto da
    `@audit_action`: la lettura della configurazione da admin compromesso
    e' un evento forense rilevante.

    AST-check: verifica la presenza del decorator nel source del router
    invece di sniffare attributi runtime (functools.wraps preserva la
    funzione originale, rendendo il check sui marker fragile).
    """

    def test_get_endpoint_has_audit_action_decorator(self):
        import ast
        from pathlib import Path

        router_path = (
            Path(runtime_router.__file__).resolve()
        )
        tree = ast.parse(router_path.read_text(encoding="utf-8"))

        found = False
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if node.name != "get_runtime_config":
                continue
            for deco in node.decorator_list:
                # Cerca `audit_action(action="admin.runtime_config.read")`
                if isinstance(deco, ast.Call):
                    func = deco.func
                    name = getattr(func, "id", None) or getattr(func, "attr", None)
                    if name == "audit_action":
                        # Verifica l'azione esatta nei kwargs/args
                        for kw in deco.keywords:
                            if kw.arg == "action" and isinstance(kw.value, ast.Constant):
                                assert kw.value.value == "admin.runtime_config.read"
                                found = True
            break

        assert found, (
            "GET /admin/runtime-config deve avere "
            "@audit_action(action='admin.runtime_config.read') per "
            "lasciare audit trail su lettura config completa."
        )
