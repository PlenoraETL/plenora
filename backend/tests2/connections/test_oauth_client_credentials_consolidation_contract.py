"""
Contract test: consolidazione OAuth2 client_credentials.

Bug originale (plenora-bug-connections P3, code-duplication):
  `core/features/arcgis_auth.py::_fetch_oauth2_token_record` aveva una
  copia parallela della logica di `_fetch_oauth2_client_credentials_token`
  in `shared/connections/oauth.py`. Le due implementazioni rischiavano
  di divergere su retry, log redaction, error mapping.

Post-fix: ArcGIS delega alla funzione shared passando
`extra_form_data={"f": "json"}` per preservare il quirk del proprio
endpoint legacy.
"""

from __future__ import annotations

import inspect

from tests2.shared._ast_identifiers import all_identifiers


def test_shared_oauth_helper_accepts_extra_form_data():
    """La funzione shared accetta `extra_form_data` per quirk provider-specific."""
    from app.shared.connections import oauth

    sig = inspect.signature(oauth._fetch_oauth2_client_credentials_token)
    assert "extra_form_data" in sig.parameters, (
        "_fetch_oauth2_client_credentials_token deve esporre `extra_form_data` "
        "per permettere ai caller (es. ArcGIS) di estendere il body POST "
        "senza forkare la funzione."
    )


def test_arcgis_delegates_to_shared_oauth_helper():
    """`_fetch_oauth2_token_record` ArcGIS non duplica piu' il fetch."""
    from app.core.features import arcgis_auth

    # `_fetch_oauth2_token_record` e' un method di una classe;
    # cerchiamo nel source completo del file
    source = inspect.getsource(arcgis_auth)

    # AST-bound: la delega allo helper shared e' un identificatore.
    assert "_fetch_oauth2_client_credentials_token" in all_identifiers(source), (
        "arcgis_auth deve delegare a _fetch_oauth2_client_credentials_token "
        "invece di mantenere una copia parallela del grant flow."
    )
    # Sanity: il quirk ArcGIS (`f=json`) deve essere ancora preservato.
    # RAW-source: "f"/"json" sono VALORI string-literal (extra_form_data),
    # non identificatori -> non catturabili da all_identifiers.
    assert '"f"' in source and '"json"' in source, (
        "Il delegate deve passare `extra_form_data={'f': 'json'}` per "
        "preservare il comportamento dell'endpoint legacy ArcGIS."
    )
