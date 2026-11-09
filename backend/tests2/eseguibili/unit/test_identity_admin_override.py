"""«Utente che esegue»: valore scelto dagli amministratori (09/11/2026).

Regola (``app/shared/execution/runtime_identity``, sezione VALORE SCELTO
DAGLI AMMINISTRATORI): con ``admin_override`` sull'input, chi esegue con
ruolo ``admin`` o ``superadmin`` (letto SOLO dal server) puo' inviare il
valore: non vuoto -> usato com'e' (nessuna trasformazione), come testo; vuoto
-> regola normale. Ogni altro ruolo, o input senza l'opzione -> 400
``runtime_identity_client_value``. Il valore scelto passa dalla via del valore
del server (``value_literal``: un solo parametro SQL) e il riepilogo della
run registra ``overridden_by``. Ogni test qui fallisce senza la funzione:

1. risoluzione: chi puo' scegliere, valore usato com'e', vuoto, testo;
2. sicurezza agli endpoint ``/runs`` e ``/preflight``: esecutore, user,
   viewer, admin senza opzione -> 400; admin con opzione -> valore e audit;
3. Estrai: valori pericolosi dell'amministratore -> bind;
4. manifest: opzione ed etichetta nel manifest, valore di prova mai;
5. editor: valore di prova scelto (anteprima, prova);
6. ripubblicazione (V1): mai ``kind_changed``, togliere l'opzione e'
   un'informazione che non blocca.
"""

from __future__ import annotations

import copy
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.core.eseguibili import db as eseguibili_db
from app.core.eseguibili import preflight as eseguibili_preflight
from app.core.eseguibili import runtime as executable_runtime
from app.core.eseguibili import service as eseguibili_service
from app.core.eseguibili.identity_transform import identity_values
from app.core.orchestrator.execution_contract import PipelineExecutionContract
from app.core.orchestrator.runtime_inputs import apply_identity_inputs, apply_test_run_inputs
from app.core.projects.canvas_models import CanvasState
from app.shared import error_codes
from app.shared.execution.runtime_identity import (
    ADMIN_OVERRIDE_MAX_LENGTH,
    RuntimeIdentity,
    RuntimeIdentityError,
)
from tests2.eseguibili.unit.conftest import build_client
from tests2.eseguibili.unit.test_runtime_identity_inputs import (
    _bound_value,
    _code,
    _estrai_canvas,
    _manifest,
    _manipola_canvas,
    _Patched,
    _project_app,
)
from tests2.helpers.builders import build_current_user

pytestmark = [pytest.mark.unit, pytest.mark.eseguibili, pytest.mark.contract]

RULE = "utente.str.slice(1, 4) + '_IS'"
CLIENT = error_codes.RUNTIME_IDENTITY_CLIENT_VALUE
INVALID = error_codes.RUNTIME_IDENTITY_OVERRIDE_INVALID
LABEL = "Istituto (solo amministratori)"
ON = {"identity_transform": RULE, "admin_override": True, "admin_override_label": LABEL}


def _defs(admin_override: bool = True):
    return [SimpleNamespace(key="utente", label="Utente", kind="current_user", transform=RULE,
                            admin_override=admin_override)]


def _as(role: str, username: str = "E0041"):
    return build_current_user(username=username, role=role)


def _identity(role: str, username: str = "E0041") -> RuntimeIdentity:
    return RuntimeIdentity.authenticated(_as(role, username))


# ---------------------------------------------------------------- 1. risoluzione


@pytest.mark.parametrize("role", ["admin", "superadmin"])
def test_an_admin_value_is_used_as_is_without_the_transform(role):
    values = identity_values(_defs(), identity=_identity(role), client_values={"utente": "004_IS"})
    assert values == {"utente": "004_IS"}
    assert values.overridden == {"utente"}


@pytest.mark.parametrize("empty", ["", "   ", None, "\t\n"])
def test_an_empty_admin_value_applies_the_normal_rule(empty):
    values = identity_values(_defs(), identity=_identity("admin", "E0071"), client_values={"utente": empty})
    assert values == {"utente": "007_IS"}
    assert values.overridden == frozenset()


def test_without_a_value_the_admin_gets_the_normal_rule():
    assert identity_values(_defs(), identity=_identity("admin"), client_values={}) == {"utente": "004_IS"}


@pytest.mark.parametrize("role", ["esecutore", "user", "viewer"])
def test_other_roles_cannot_send_the_value_even_with_the_option(role):
    for value in ("004_IS", ""):
        with pytest.raises(RuntimeIdentityError) as err:
            identity_values(_defs(), identity=_identity(role), client_values={"utente": value})
        assert err.value.code == CLIENT


def test_an_admin_cannot_send_the_value_without_the_option():
    with pytest.raises(RuntimeIdentityError) as err:
        identity_values(_defs(admin_override=False), identity=_identity("admin"), client_values={"utente": "004_IS"})
    assert err.value.code == CLIENT


@pytest.mark.parametrize("identity", [
    RuntimeIdentity.preview_of(_estrai_canvas(preview_username="E0041", **ON)),
    RuntimeIdentity.test_run(_estrai_canvas(preview_username="E0041", **ON)),
    RuntimeIdentity.project_run(),
    RuntimeIdentity.unattended(),
])
def test_only_an_authenticated_admin_may_choose(identity):
    """Anteprima, prova e percorsi senza utente non hanno un ruolo: il
    valore del client si rifiuta sempre (il ruolo non arriva mai dal client)."""
    with pytest.raises(RuntimeIdentityError) as err:
        identity_values(_defs(), identity=identity, client_values={"utente": "004_IS"})
    assert err.value.code == CLIENT


def test_control_characters_and_outer_spaces_are_removed():
    values = identity_values(_defs(), identity=_identity("admin"), client_values={"utente": " 004\x00_IS\x1b\n"})
    assert values == {"utente": "004_IS"}


@pytest.mark.parametrize("value,words", [
    ("x" * (ADMIN_OVERRIDE_MAX_LENGTH + 1), f"supera {ADMIN_OVERRIDE_MAX_LENGTH} caratteri"),
    (4, "deve essere un testo"),
    (["004_IS"], "deve essere un testo"),
    ({"value": "004_IS"}, "deve essere un testo"),
])
def test_an_invalid_admin_value_is_refused(value, words):
    with pytest.raises(RuntimeIdentityError) as err:
        identity_values(_defs(), identity=_identity("admin"), client_values={"utente": value})
    assert err.value.code == INVALID and words in err.value.message


def test_the_role_comes_only_from_the_authenticated_user():
    identity = _identity("esecutore")
    assert identity.role == "esecutore" and not identity.may_choose(_defs()[0])
    assert _identity("superadmin").may_choose(_defs()[0])


# ---------------------------------------------------------------- 2. endpoint /runs e /preflight


def _rows(canvas: dict):
    manifest = _manifest("estrai", canvas).model_dump()
    version = eseguibili_db.EseguibileVersionRow(
        id="version-1", eseguibile_id="exe-1", version_number=1, schema_version=2, project_type="estrai",
        source_project_id="p", source_hash="h", canvas=canvas, manifest=manifest, status="published",
        created_by="u-1", created_at="2026-11-09T00:00:00+00:00")
    executable = eseguibili_db.EseguibileRow(
        id="exe-1", name="Immobili", description="", canvas=canvas, expected_columns=[],
        default_output_format="csv", group_id="grp-1", created_by="u-1",
        created_at="2026-11-09T00:00:00+00:00", updated_at="2026-11-09T00:00:00+00:00",
        project_type="estrai", active_version_id="version-1", manifest=manifest)
    return executable, version


def _patches(canvas: dict):
    executable, version = _rows(canvas)
    return [
        patch("app.core.eseguibili.router.eseguibili_db.get_eseguibile", return_value=executable),
        patch("app.core.eseguibili.router._check_visible", return_value=None),
        patch("app.core.eseguibili.router.eseguibili_db.get_eseguibile_version", return_value=version),
        patch("app.core.eseguibili.service.eseguibili_db.get_eseguibile_version", return_value=version),
        patch("app.core.eseguibili.service.eseguibili_db.get_active_eseguibile_version", return_value=version),
        patch("app.core.eseguibili.router._save_task_owner"),
        patch("app.core.eseguibili.router._task_service.reserve_task_id", return_value="task-1"),
        patch("app.core.eseguibili.service.eseguibili_db.bind_eseguibile_run_task"),
    ]


def _run(user, inputs: dict, canvas: dict | None = None):
    """POST /runs: (status, codice) oppure (200, valore nel filtro, riepilogo)."""
    canvas = canvas or _estrai_canvas(**ON)
    task = SimpleNamespace(task_id="task-1")
    with _Patched(_patches(canvas)), \
         patch("app.core.eseguibili.service.eseguibili_db.create_eseguibile_run", return_value="run-1") as created, \
         patch("app.core.orchestrator.task_service.task_service.create_pipeline_task", return_value=task) as submit:
        response = build_client(user).post("/api/eseguibili/exe-1/runs", json={"inputs": inputs})
    if response.status_code != 200:
        return response.status_code, _code(response)
    canvas_sent = submit.call_args.kwargs["execution_contract"]["canvas"]
    return 200, _bound_value(canvas_sent, "estrai"), created.call_args.kwargs["input_summary"]["utente"]


def _preflight(user, inputs: dict):
    with _Patched(_patches(_estrai_canvas(**ON))):
        response = build_client(user).post("/api/eseguibili/exe-1/preflight", json={"inputs": inputs})
    if response.status_code != 200:
        return response.status_code, _code(response)
    return 200, response.json()["preparation_fingerprint"]


@pytest.mark.parametrize("role", ["esecutore", "user", "viewer"])
def test_run_refuses_the_value_from_other_roles_with_the_option_on(role):
    assert _run(_as(role), {"utente": "999_IS"}) == (400, CLIENT)


@pytest.mark.parametrize("role", ["esecutore", "user", "viewer"])
def test_preflight_refuses_the_value_from_other_roles(role):
    assert _preflight(_as(role), {"utente": "999_IS"}) == (400, CLIENT)


def test_run_refuses_the_admin_value_without_the_option():
    canvas = _estrai_canvas(identity_transform=RULE)
    assert _run(_as("admin"), {"utente": "004_IS"}, canvas) == (400, CLIENT)


def test_the_client_cannot_declare_its_role():
    with _Patched(_patches(_estrai_canvas(**ON))):
        response = build_client(_as("esecutore")).post(
            "/api/eseguibili/exe-1/runs", json={"inputs": {"utente": "999_IS"}, "role": "admin"})
    assert response.status_code == 422


@pytest.mark.parametrize("role", ["admin", "superadmin"])
def test_run_uses_the_admin_value_and_records_who_chose_it(role):
    status, value, summary = _run(_as(role, "capo"), {"utente": "004_IS"})
    assert (status, value) == (200, "004_IS")
    assert summary == {"kind": "current_user", "provided": True, "username": "capo",
                       "value": "004_IS", "overridden_by": "capo"}


def test_run_with_an_empty_admin_value_applies_the_normal_rule():
    status, value, summary = _run(_as("admin", "E0071"), {"utente": ""})
    assert (status, value) == (200, "007_IS")
    assert "overridden_by" not in summary and summary["value"] == "007_IS"


def test_run_refuses_an_admin_value_too_long():
    assert _run(_as("admin"), {"utente": "x" * 500}) == (400, INVALID)


def test_preflight_fingerprint_follows_the_admin_value():
    admin = _as("admin", "E0041")
    status, chosen = _preflight(admin, {"utente": "007_IS"})
    _, normal = _preflight(admin, {})
    assert status == 200 and chosen != normal
    executable, _ = _rows(_estrai_canvas(**ON))
    with _Patched(_patches(_estrai_canvas(**ON))):
        # Stessa impronta all'avvio (stessi input, stessa identita').
        assert chosen == eseguibili_preflight.fingerprint_for(
            eseguibile=executable, raw_inputs={"utente": "007_IS"},
            identity=RuntimeIdentity.authenticated(admin), version_id="version-1")


# ---------------------------------------------------------------- 3. Estrai: bind


def _estrai_sql(values: dict, identity: RuntimeIdentity, operator: str = "=") -> dict:
    from app.core.estrai import query_plan
    from app.core.estrai.query_generator import QueryGenerator

    canvas = _estrai_canvas(**ON)
    canvas["nodes"][0]["filters"][0]["operator"] = operator
    resolved, _, summary = executable_runtime.resolve_runtime_inputs(
        eseguibile_id="exe-1", canvas=canvas, manifest=_manifest("estrai", canvas), values=values,
        group_id="grp-1", identity=identity)
    state = CanvasState(**resolved)
    info = query_plan.sql_query(QueryGenerator(), state.nodes, state.edges,
                                query_plan.estrai_result_node(state.nodes),
                                {"db_type": "postgres", "db_schema": "public"})
    info["summary"] = summary
    return info


@pytest.mark.parametrize("value,operator,clause", [
    ("004_IS", "=", "= :p_0"),
    ("x' OR '1'='1", "=", "= :p_0"),
    ("004_IS'; DROP TABLE pratiche; --", "=", "= :p_0"),
    ("NOW()", "=", "= :p_0"),
    ("GETDATE()", "=", "= :p_0"),
    ("004_IS,007_IS", "IN", "IN (:p_0)"),
])
def test_a_dangerous_admin_value_is_bound_as_one_parameter(value, operator, clause):
    info = _estrai_sql({"utente": value}, _identity("admin"), operator)
    sql = info["sql"]
    assert f"owner {clause}" in sql, sql
    assert list(info["params"].values()) == [value]
    assert value not in sql
    assert info["summary"]["utente"]["overridden_by"] == "E0041"


@pytest.mark.parametrize("operator", ["LIKE", "!=", "NOT IN", "BETWEEN"])
def test_the_admin_value_keeps_the_operator_limits(operator):
    with pytest.raises(ValueError, match="si confronta solo con «uguale» o «è uno di»"):
        _estrai_sql({"utente": "004_IS"}, _identity("admin"), operator)


# ---------------------------------------------------------------- 4. manifest


def test_the_manifest_carries_the_option_and_never_the_test_value():
    canvas = _estrai_canvas(preview_username="E0041", preview_admin_value="SEGRETO_9", **ON)
    definition = _manifest("estrai", canvas).inputs[0]
    assert definition.admin_override is True and definition.admin_override_label == LABEL
    assert "SEGRETO_9" not in str(_manifest("estrai", canvas).model_dump())


@pytest.mark.parametrize("raw", [None, False, "true", 1, "yes"])
def test_only_true_turns_the_option_on(raw):
    canvas = _estrai_canvas(admin_override=raw, admin_override_label=LABEL)
    definition = _manifest("estrai", canvas).inputs[0]
    assert definition.admin_override is False and definition.admin_override_label is None


def test_the_option_on_a_client_input_is_ignored():
    canvas = _estrai_canvas(**ON)
    canvas["nodes"][-1]["data"]["input_kind"] = "text"
    definition = _manifest("estrai", canvas).inputs[0]
    assert definition.admin_override is False and definition.admin_override_label is None


# ---------------------------------------------------------------- 5. editor


def _contract(canvas: dict) -> PipelineExecutionContract:
    return PipelineExecutionContract(project_id="p", project_type="estrai", group_id="grp-1",
                                     canvas=copy.deepcopy(canvas), runtime_context={"default_connection_id": "conn"})


def test_test_run_uses_the_saved_test_value():
    canvas = _estrai_canvas(preview_username="E0041", preview_admin_value=" 009_IS ", **ON)
    resolved = apply_test_run_inputs(_contract(canvas), {})
    assert _bound_value(resolved.canvas, "estrai") == "009_IS"
    summary = resolved.runtime_context["runtime_input_summary"]["utente"]
    assert summary["test_value"] is True and summary["value"] == "009_IS"
    assert "overridden_by" not in summary


def test_test_run_ignores_the_test_value_without_the_option():
    canvas = _estrai_canvas(preview_username="E0041", preview_admin_value="009_IS", identity_transform=RULE)
    assert _bound_value(apply_test_run_inputs(_contract(canvas), {}).canvas, "estrai") == "004_IS"


def test_test_run_with_an_empty_test_value_applies_the_normal_rule():
    canvas = _estrai_canvas(preview_username="E0041", preview_admin_value="  ", **ON)
    assert _bound_value(apply_test_run_inputs(_contract(canvas), {}).canvas, "estrai") == "004_IS"


def test_preview_uses_the_test_value(monkeypatch):
    canvas = _manipola_canvas(preview_username="E0041", preview_admin_value="009_IS", **ON)
    client, submitted = _project_app("manipola", canvas, monkeypatch)
    assert client.get("/api/projects/p/preview").status_code == 200
    assert _bound_value(submitted.call_args.kwargs["execution_contract"]["canvas"], "manipola") == "009_IS"


def test_preview_identity_inputs_use_the_test_value():
    canvas = _estrai_canvas(preview_username="E0041", preview_admin_value="009_IS", **ON)
    contract = apply_identity_inputs(_contract(canvas), RuntimeIdentity.preview_of(canvas))
    assert _bound_value(contract.canvas, "estrai") == "009_IS"


def test_a_test_value_still_needs_the_test_username():
    canvas = _estrai_canvas(preview_admin_value="009_IS", **ON)
    with pytest.raises(RuntimeIdentityError) as err:
        apply_test_run_inputs(_contract(canvas), {})
    assert err.value.code == error_codes.RUNTIME_IDENTITY_REQUIRED


# ---------------------------------------------------------------- 6. ripubblicazione (V1)


def _published(canvas: dict, *, legacy: bool = False) -> dict:
    manifest = _manifest("estrai", canvas).model_dump(mode="json")
    if legacy:  # forma salvata prima della funzione
        for item in manifest["inputs"]:
            item.pop("admin_override", None)
            item.pop("admin_override_label", None)
    return manifest


@pytest.mark.parametrize("old,new,legacy", [
    ({"identity_transform": RULE}, ON, True),     # manifest di prima -> opzione aggiunta
    ({"identity_transform": RULE}, ON, False),    # opzione aggiunta
    (ON, {**ON, "admin_override_label": "Altro"}, False),
])
def test_republish_lists_nothing_when_the_option_is_added_or_relabelled(old, new, legacy):
    new_canvas = _estrai_canvas(**new)
    manifest = _manifest("estrai", new_canvas)
    published = _published(_estrai_canvas(**old), legacy=legacy)
    assert eseguibili_service.input_changes(published, manifest.inputs, manifest.bindings, new_canvas) == []


def test_republish_lists_the_removed_option_as_information():
    new_canvas = _estrai_canvas(identity_transform=RULE)
    manifest = _manifest("estrai", new_canvas)
    rows = eseguibili_service.input_changes(_published(_estrai_canvas(**ON)), manifest.inputs,
                                            manifest.bindings, new_canvas)
    assert [(r["change"], r["blocking"]) for r in rows] == [("admin_override_removed", False)]
    assert "amministratori" in rows[0]["motivo"]


def test_republish_is_not_refused_for_the_removed_option():
    published, current = _estrai_canvas(**ON), _estrai_canvas(identity_transform=RULE)
    version = eseguibili_db.EseguibileVersionRow(
        id="v1", eseguibile_id="exe-1", version_number=1, schema_version=2, project_type="estrai",
        source_project_id="p", source_hash="h", canvas=published, manifest=_published(published),
        status="published", created_by="u-1", created_at="2026-11-09T00:00:00+00:00")
    record = eseguibili_db.EseguibileRow(
        id="exe-1", name="Immobili", description="", canvas=published, expected_columns=[],
        default_output_format="csv", group_id="grp-1", created_by="u-1",
        created_at="2026-11-09T00:00:00+00:00", updated_at="2026-11-09T00:00:00+00:00",
        source_canvas_id="p", project_type="estrai", active_version_id="v1")
    project = SimpleNamespace(project_id="p", group_id="grp-1", project_type="estrai",
                              canvas=copy.deepcopy(current), default_connection_id="conn")
    created: list[dict] = []
    with patch.object(eseguibili_db, "get_eseguibile_version", return_value=version), \
         patch.object(eseguibili_service.project_access_context, "load_project_publish_context",
                      return_value=project), \
         patch.object(eseguibili_service.connection_sets, "connection_set_options", return_value=[]), \
         patch.object(eseguibili_db, "create_eseguibile_version",
                      side_effect=lambda **kw: created.append(kw) or version), \
         patch.object(eseguibili_db, "activate_eseguibile_version", return_value=None), \
         patch.object(eseguibili_db, "get_eseguibile", return_value=record):
        eseguibili_service.publish_new_version(eseguibile=record, created_by="u-1")
    assert len(created) == 1
    assert created[0]["manifest"]["inputs"][0]["admin_override"] is False
