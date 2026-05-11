"""
ArcGIS Online — real-portal smoke suite.

Esegue il flow end-to-end (createService → addToDefinition →
applyEdits → query → delete) contro un AGOL **vero** (default:
``urbyetorbit2.maps.arcgis.com``, folder ``plenora-ci``).

Opt-in: i test partono SOLO se le 4 env var sono presenti. Senza
credenziali la suite skippa pulita (no failure). Convenzione filename
``test_arcgis_*`` -> ``tests2/integration/conftest.py`` aggiunge i
marker ``requires_arcgis`` + ``requires_external_network`` +
``optional_provider`` + ``developer_nightly``.

Sicurezza / blast radius:
- I service creati hanno prefix ``plenora_ci_pytest_`` + uuid: il
  cleanup nel teardown filtra esattamente per quel prefix nella folder
  di test, mai per pattern generici.
- Cleanup e' best-effort idempotente: anche se un test crasha in mezzo
  al flow, il fixture ``managed_service_name`` elimina sempre dopo.
- I test NON toccano altri item dell'organizzazione: l'app
  client_credentials eredita i permessi del proprietario, ma il filtro
  di teardown e' per name-prefix, non per "ogni item della folder".

Credit cost AGOL: ogni FeatureService creato consuma frazioni di
credito storage AGOL fino al delete. Il flow completo ~5 service
short-lived (< 60s ciascuno).
"""

from __future__ import annotations

import os
import uuid
from typing import Iterator, Tuple

import pytest

# pandas/geopandas usati per costruire DF d'input come fa il codice reale
pd = pytest.importorskip("pandas")
gpd = pytest.importorskip("geopandas")
from shapely.geometry import Point  # noqa: E402  (after importorskip)


# Env vars consumed (set via docker-compose.override.yml, gitignored)
_ENV_PORTAL = "ARCGIS_TEST_PORTAL_URL"
_ENV_OWNER = "ARCGIS_TEST_OWNER"
_ENV_FOLDER = "ARCGIS_TEST_FOLDER"
_ENV_ENABLE = "ARCGIS_TESTS_ENABLED"  # double opt-in: anche con creds, gate esplicito

# Username + password (auth_type="token", piu' privilegi su /content/users)
_ENV_USERNAME = "ARCGIS_TEST_USERNAME"
_ENV_PASSWORD = "ARCGIS_TEST_PASSWORD"
# Client_credentials OAuth2 (auth_type="oauth2", read-only sui content endpoint)
_ENV_CLIENT_ID = "ARCGIS_TEST_CLIENT_ID"
_ENV_CLIENT_SECRET = "ARCGIS_TEST_CLIENT_SECRET"

# Prefix usato per filtrare il cleanup. NON toccare service che non lo hanno.
_SERVICE_PREFIX = "plenora_ci_pytest_"


def _resolve_env_or_skip() -> Tuple[str, str, str]:
    """Carica le env minime: portal + owner + opt-in flag. Skip pulito
    se manca anche solo una, cosi' la suite e' eseguibile su CI/dev
    senza credenziali."""
    if not os.getenv(_ENV_PORTAL):
        pytest.skip(f"{_ENV_PORTAL} non configurata")
    if not os.getenv(_ENV_OWNER):
        pytest.skip(f"{_ENV_OWNER} non configurata")
    if os.getenv(_ENV_ENABLE, "").lower() not in {"1", "true", "yes"}:
        pytest.skip(
            f"Set {_ENV_ENABLE}=1 per abilitare i test contro AGOL reale "
            "(creano service che consumano credit, anche se cleanati)."
        )
    return (
        os.environ[_ENV_PORTAL].rstrip("/"),
        os.environ[_ENV_OWNER],
        os.getenv(_ENV_FOLDER, "plenora-ci"),
    )


@pytest.fixture(scope="module")
def arcgis_conn():
    """ArcGISConnectionInfo per AGOL. Preferisce username/password (auth
    type=``token``) se entrambi presenti — perche' client_credentials su
    AGOL non ha privilegi su ``/content/users/<X>/...`` (limitazione
    Esri intenzionale, non bypassabile via ruolo).

    Se solo client_id/client_secret presenti: skippa, perche' la suite
    smoke include create_service che richiede privileges utente reali.
    """
    portal, owner, _folder = _resolve_env_or_skip()
    from app.core.features.arcgis_client import ArcGISConnectionInfo

    username = os.getenv(_ENV_USERNAME) or owner
    password = os.getenv(_ENV_PASSWORD)

    if password:
        return ArcGISConnectionInfo(
            server_url=portal,
            server_type="arcgis_online",
            auth_type="token",          # /sharing/rest/generateToken
            username=username,
            password=password,
        )

    client_id = os.getenv(_ENV_CLIENT_ID)
    client_secret = os.getenv(_ENV_CLIENT_SECRET)
    if client_id and client_secret:
        pytest.skip(
            "Solo client_credentials disponibili: AGOL non permette "
            "createService via app login. Configura "
            f"{_ENV_USERNAME}+{_ENV_PASSWORD} per la suite smoke."
        )

    pytest.skip(
        f"Credenziali utente mancanti ({_ENV_USERNAME}+{_ENV_PASSWORD}) "
        f"e nessun client_credentials fallback."
    )


@pytest.fixture(scope="module")
def arcgis_client():
    from app.core.features.arcgis_client import ArcGISClient

    return ArcGISClient()


@pytest.fixture(scope="module")
def arcgis_folder_id(arcgis_client, arcgis_conn) -> str:
    """Risolve l'id della folder di test. Se la folder non esiste e'
    accettabile fall-through a root ('') — il test crea comunque il
    service ma in root anziche' nella folder di test."""
    _portal, _owner, folder_name = _resolve_env_or_skip()
    if not folder_name:
        return ""
    try:
        folders = arcgis_client.list_folders(arcgis_conn)
    except Exception as e:
        pytest.skip(f"list_folders fallito (skip non-fatal): {e}")
    for f in folders:
        if (f.get("title") or "").lower() == folder_name.lower():
            return str(f.get("id") or "")
    # folder non trovata: il test puo' usare root, ma logghiamo
    # cosi' chi gira la suite sa di creare la folder manuale.
    print(
        f"\n[arcgis-real-smoke] folder '{folder_name}' non trovata, "
        f"uso root. Crea folder per evitare di sporcare root."
    )
    return ""


def _cleanup_prefixed_services(client, conn, service_name: str) -> None:
    """Cancella ogni portal item che matcha esattamente ``service_name``.

    Non usa wildcard / contains su altri service per limitare il blast
    radius: il name del service e' univoco per test (uuid).
    """
    try:
        items = client.list_user_items(conn)
    except Exception as exc:
        # Best-effort: non rompere il teardown se la lista fallisce.
        print(f"[arcgis-real-smoke] list_user_items teardown failed: {exc}")
        return

    targets = [
        it for it in items
        if str(it.get("title") or it.get("name") or "") == service_name
        or str(it.get("name") or "") == service_name
    ]
    if not targets:
        return

    deleted, attempted = client.delete_feature_service_items(
        conn, targets, service_name=service_name,
    )
    print(
        f"[arcgis-real-smoke] cleanup '{service_name}': "
        f"{deleted}/{attempted} items deleted"
    )


@pytest.fixture
def managed_service_name(arcgis_client, arcgis_conn) -> Iterator[str]:
    """Yield un service name univoco; in teardown elimina l'item creato.

    Pattern try/finally garantisce il delete anche su test fallito.
    Lo prefisso ``plenora_ci_pytest_`` rende facile spottare in UI
    eventuali residui da crash brutali.
    """
    name = f"{_SERVICE_PREFIX}{uuid.uuid4().hex[:12]}"
    try:
        yield name
    finally:
        _cleanup_prefixed_services(arcgis_client, arcgis_conn, name)


# Tests


def test_oauth2_client_credentials_returns_token(arcgis_client, arcgis_conn):
    """Smoke piu' basso possibile: la POST /oauth2/token risponde con
    access_token non vuoto. Tutto il resto della suite dipende da questo."""
    token = arcgis_client.authenticate(arcgis_conn)
    assert token, "authenticate() ha ritornato token vuoto"
    assert len(token) > 10, f"token sospettosamente corto: len={len(token)}"


def test_list_user_items_authorized(arcgis_client, arcgis_conn):
    """Verifica end-to-end l'accesso content/users/<owner>:
    il token deve avere visibilita' sui contenuti dell'owner.

    Nota: ``list_folders`` non e' un buon proxy perche' il client ha un
    fallback ``[(root)]`` anche su errori HTTP (per non rompere la UI):
    una 403 li dentro passerebbe il test silenziosamente. Qui usiamo
    ``list_user_items`` che invece **solleva** RuntimeError su errore
    dal portale, dandoci un segnale chiaro se le credenziali OAuth2
    non sono mappate al giusto owner / non hanno privilegi sufficienti.
    """
    try:
        items = arcgis_client.list_user_items(arcgis_conn)
    except Exception as e:
        msg = str(e)
        if "permissions" in msg.lower() or "403" in msg or "GWM_0003" in msg:
            pytest.fail(
                "OAuth2 token autenticato ma NON autorizzato per content/users/<owner>.\n"
                f"ARCGIS_TEST_OWNER='{arcgis_conn.username}' probabilmente non e' il "
                "proprietario della Registered App, oppure il suo ruolo non include "
                "'Publish hosted feature layers'. Vedi docstring suite per fix.\n"
                f"Errore originale: {msg}"
            )
        raise
    assert isinstance(items, list)


def test_create_service_then_query_then_delete(
    arcgis_client, arcgis_conn, arcgis_folder_id, managed_service_name,
):
    """Flow completo:
    1. create_service da un piccolo GeoDataFrame (3 punti, 1 layer)
    2. query_features per leggere indietro i 3 record
    3. (cleanup automatico via fixture)

    E' il test "money" della suite: stressa createService +
    addToDefinition + applyEdits (insert) + query in un solo passaggio,
    cioe' la maggior parte della superficie scrittura/lettura del client.
    """
    gdf = gpd.GeoDataFrame(
        {
            "id": [1, 2, 3],
            "label": ["Milano", "Roma", "Napoli"],
            "score": [42.5, 17.0, 99.9],
            "geometry": [
                Point(9.19, 45.46),
                Point(12.50, 41.90),
                Point(14.27, 40.85),
            ],
        },
        crs="EPSG:4326",
    )

    result = arcgis_client.create_service(
        arcgis_conn,
        service_name=managed_service_name,
        layer_name="cities",
        df=gdf,
        geometry_column="geometry",
        srid=4326,
        folder_id=arcgis_folder_id,
    )

    assert result.get("success"), f"create_service fallito: {result}"
    layer_url = result.get("layer_url") or ""
    service_url = result.get("service_url") or ""
    assert "FeatureServer" in service_url, (
        f"service_url malformato: {service_url!r}"
    )
    assert layer_url.startswith(service_url), (
        f"layer_url non coerente col service_url: layer={layer_url}, "
        f"service={service_url}"
    )

    # Lettura: query_features deve ritrovare le 3 city.
    rows, columns = arcgis_client.query_features(
        arcgis_conn,
        layer_url=layer_url,
        where="1=1",
        out_fields="*",
    )
    assert rows, f"query_features ha ritornato 0 righe (cols={columns})"
    assert len(rows) == 3, f"atteso 3 feature, ricevute {len(rows)}: {rows}"
    assert "label" in columns, f"colonna 'label' mancante: {columns}"

    # Estrai i label rispettando l'ordine columns.
    label_idx = columns.index("label")
    labels = [str(row[label_idx]) for row in rows]
    assert "Milano" in labels and "Roma" in labels and "Napoli" in labels, (
        f"labels persi nel round-trip: {labels}"
    )


def test_create_service_then_apply_edits_update_delete(
    arcgis_client, arcgis_conn, arcgis_folder_id, managed_service_name,
):
    """Verifica il path apply_edits (update + delete) post-create.

    Insert iniziale e' parte di create_service; qui esercitiamo update
    su una feature e delete su un'altra. Il client AGOL traduce internamente
    in /applyEdits con updates=[...] e deletes=[...].
    """
    gdf = gpd.GeoDataFrame(
        {
            "id": [10, 20, 30],
            "label": ["Alpha", "Beta", "Gamma"],
            "geometry": [
                Point(9.0, 45.0),
                Point(10.0, 46.0),
                Point(11.0, 47.0),
            ],
        },
        crs="EPSG:4326",
    )

    create_result = arcgis_client.create_service(
        arcgis_conn,
        service_name=managed_service_name,
        layer_name="apply_edits_test",
        df=gdf,
        geometry_column="geometry",
        srid=4326,
        folder_id=arcgis_folder_id,
    )
    assert create_result.get("success"), f"create fallito: {create_result}"
    layer_url = create_result["layer_url"]

    # Risali agli OBJECTID per poter fare update/delete mirati: AGOL li
    # assegna lato server al primo addFeatures.
    rows, columns = arcgis_client.query_features(
        arcgis_conn,
        layer_url=layer_url,
        where="1=1",
        out_fields="*",
    )
    assert len(rows) == 3, f"expected 3 rows post-create, got {len(rows)}: {rows}"
    oid_idx = columns.index("OBJECTID")
    id_idx = columns.index("id")

    oid_for_id10 = next(r[oid_idx] for r in rows if r[id_idx] == 10)
    oid_for_id30 = next(r[oid_idx] for r in rows if r[id_idx] == 30)

    # apply_edits: update id=10 label, delete id=30.
    edits_result = arcgis_client.apply_edits(
        arcgis_conn,
        layer_url=layer_url,
        adds=[],
        updates=[{
            "attributes": {"OBJECTID": oid_for_id10, "label": "AlphaUPDATED"},
            "geometry": {"x": 9.5, "y": 45.5, "spatialReference": {"wkid": 4326}},
        }],
        deletes=[oid_for_id30],
    )
    assert edits_result.get("success") or edits_result.get("updateResults"), (
        f"apply_edits non success: {edits_result!r}"
    )

    # Re-query: ora ci sono 2 feature, una con label aggiornata.
    rows_after, cols_after = arcgis_client.query_features(
        arcgis_conn,
        layer_url=layer_url,
        where="1=1",
        out_fields="*",
    )
    assert len(rows_after) == 2, (
        f"post-delete atteso 2 feature, ricevute {len(rows_after)}: {rows_after}"
    )
    label_idx = cols_after.index("label")
    labels_after = sorted(str(r[label_idx]) for r in rows_after)
    assert "AlphaUPDATED" in labels_after, (
        f"update label non propagata: {labels_after}"
    )
    assert "Gamma" not in labels_after, (
        f"feature id=30 non eliminata: {labels_after}"
    )
