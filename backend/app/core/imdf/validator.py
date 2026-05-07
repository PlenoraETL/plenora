"""
validator.py — Pre-publish validator per bundle IMDF → Feature Service ArcGIS

Esegue un controllo read-only (senza side effect su ArcGIS) prima della
"""

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.utils.logger import get_logger
from app.shared.connections.arcgis_connection import ArcGISConnectionInfo
from app.shared.connections.arcgis_provider import ArcGISProviderProtocol

logger = get_logger(__name__)


def _count_features_where_via_post(
    client: ArcGISProviderProtocol,
    conn: ArcGISConnectionInfo,
    layer_url: str,
    where: str,
) -> int:
    """POST /query diretto con `returnCountOnly=true`. Bypassa la pipeline
    standard di `client.query_features` che fa GET (sensibile a URL length,
    content-type negotiation proxy, ecc.) e al JSON decode.

    Ritorna il count. Solleva `RuntimeError` su errore — il chiamante
    gestisce il fallback.

    BE-IMDF-002: implementazione consolidata in
    `app.core.features.arcgis_client.ArcGISClient.count_features_where`.
    Questo modulo-level symbol resta come thin wrapper per:
    1. preservare il path di monkeypatch usato dai test
       (`tests2/imdf/unit/test_validate_publish_issue_categories.py`
       patcha `validator_mod._count_features_where_via_post`);
    2. mantenere invariato il call site in `validate_publish`.
    """
    return client.count_features_where(conn, layer_url, where)


# Mapping dei tipi Esri verso una rappresentazione "coarse" per il
# confronto schema. Esri usa nomi come esriFieldTypeString/Integer/Double
# Il GeoDataFrame ha dtype numpy. Normalizziamo entrambi a un set ridotto
# BE-IMDF-003: implementazione consolidata in
# `app.core.features.arcgis_client.EsriTypeMapper.map_esri_field_to_schema_bucket`
# / `map_pandas_dtype_to_schema_bucket`. Questi due simboli modulo-level
# restano come thin wrapper per preservare il path di monkeypatch dei
# test (es. `tests2/imdf/unit/test_validate_publish_*` che possono
# patchare `validator._coarse_type_*`) e per non cambiare il call site
# di `validate_publish`
def _coarse_type_from_esri(esri_type: str) -> str:
    from app.shared.connections.esri_types import EsriTypeMapper  # ADR-0023: moved from features
    return EsriTypeMapper.map_esri_field_to_schema_bucket(esri_type)


def _coarse_type_from_dtype(dtype) -> str:
    from app.shared.connections.esri_types import EsriTypeMapper  # ADR-0023: moved from features
    return EsriTypeMapper.map_pandas_dtype_to_schema_bucket(dtype)


def validate_publish(
    bundle_task_id: str,
    connection_id: str,
    fs_name: str,
    mode: str = "replace",
    match_columns: Optional[Dict[str, str]] = None,
    folder: Optional[str] = None,
) -> Dict[str, Any]:
    """Esegue la validazione e ritorna un dict report.

    Args:
        bundle_task_id: task_id del bundle persistito in IMDF_BUNDLES_DIR
        connection_id: id connessione ArcGIS in cui pubblicheremo
        fs_name: nome Feature Service target
        mode: 'replace' | 'append' | 'upsert'
        match_columns: override per-layer dei default
        folder: folder ArcGIS scelta dall'utente. Se fornita e
                        diversa dalla folder effettiva dell'FS esistente,
                        emette un warning FOLDER_MISMATCH (informativo:
                        il publish funzionerà comunque, ma in replace
                        mode il nuovo FS verrà creato nella folder
                        richiesta, spostando di fatto il servizio).

    Returns:
        Dict con la struttura del report (vedi docstring modulo).
        Non lancia eccezioni per errori di validazione — li riporta nel dict.
    """
    from app.config import settings
    from app.core.features.arcgis_connection_adapter import resolve_arcgis_connection
    from app.core.imdf.loader import load_imdf_layers
    from app.core.imdf.arcgis_uploader import (
        DEFAULT_MATCH_COLUMNS, LAYER_ORDER, PUBLISH_MODES,
        _find_existing_service, _quote_sql_value, _chunked,
        _is_service_name_available, _list_user_folders,
    )

    effective_match = {**DEFAULT_MATCH_COLUMNS, **(match_columns or {})}

    report: Dict[str, Any] = {
        "ok": True,
        "fs_exists": False,
        "fs_url": None,
        "fs_item_id": None,
        "fs_folder": None,
        "requested_folder": folder,
        "mode": mode,
        "summary": {
            "errors": 0,
            "warnings": 0,
            "layers_count": 0,
            "total_features": 0,
        },
        "layers": [],
        "top_issues": [],
    }

    def _add_top_issue(sev: str, code: str, msg: str):
        report["top_issues"].append({"severity": sev, "code": code, "message": msg})
        if sev == "error":
            report["summary"]["errors"] += 1
            report["ok"] = False
        elif sev == "warning":
            report["summary"]["warnings"] += 1

    # ── 0. Validazioni base ────────────────────────────────────────
    if mode not in PUBLISH_MODES:
        _add_top_issue("error", "INVALID_MODE", f"Modalita' '{mode}' non supportata")
        return report

    # Validazione bundle_task_id contro path traversal. Il router chiama gia'
    # `_safe_bundle_dir`, ma difesa in profondita' nel modulo validator
    # Utility condivisa in app.shared.path_policy (D3 commit 1): regex +
    # path-traversal guard centralizzati. Il validator traduce ValueError
    # in un issue con code INVALID_BUNDLE_ID (shape report preservata
    from app.shared.path_policy import validate_imdf_task_id
    _root = settings.IMDF_BUNDLES_DIR.resolve()
    try:
        bundle_dir: Path = validate_imdf_task_id(bundle_task_id, _root)
    except ValueError:
        _add_top_issue(
            "error", "INVALID_BUNDLE_ID",
            f"bundle_task_id '{bundle_task_id}' malformato "
            "(ammessi solo alfanumerico, '-', '_')"
        )
        return report

    # ── 1. Bundle su disco ────────────────────────────────────────
    if not bundle_dir.exists() or not bundle_dir.is_dir():
        _add_top_issue(
            "error", "BUNDLE_MISSING",
            f"Bundle '{bundle_task_id}' non trovato (potrebbe essere scaduto: "
            f"TTL {settings.IMDF_BUNDLE_TTL_HOURS}h)"
        )
        return report

    # Trova il .gdb
    gdb_path = None
    for p in bundle_dir.rglob("*.gdb"):
        if p.is_dir():
            gdb_path = p
            break
    if gdb_path is None:
        _add_top_issue("error", "GDB_MISSING", f"Nessun .gdb trovato in {bundle_dir}")
        return report

    # Carica layer dal GDB
    try:
        bundle_layers = load_imdf_layers(gdb_path)
    except Exception as e:
        _add_top_issue("error", "GDB_LOAD_FAILED", f"Caricamento GDB fallito: {e}")
        return report

    layers_present = [n for n in LAYER_ORDER if n in bundle_layers and len(bundle_layers[n]) > 0]
    if not layers_present:
        _add_top_issue("error", "BUNDLE_EMPTY", "Nessun layer IMDF popolato nel bundle")
        return report

    report["summary"]["layers_count"] = len(layers_present)
    report["summary"]["total_features"] = sum(len(bundle_layers[n]) for n in layers_present)

    _resolved = resolve_arcgis_connection(connection_id)
    if _resolved is None:
        _add_top_issue("error", "CONNECTION_NOT_FOUND",
                       f"Connessione ArcGIS '{connection_id}' non trovata")
        return report
    conn, client = _resolved

    # ── 3. Cerca FS esistente globalmente ──────────────────────────
    try:
        existing_url, existing_item, existing_folder = _find_existing_service(
            client, conn, fs_name
        )
    except Exception as e:
        _add_top_issue("warning", "FS_SEARCH_FAILED",
                       f"Ricerca FS esistente fallita: {e}")
        existing_url, existing_item, existing_folder = None, None, None

    report["fs_exists"]  = bool(existing_url)
    report["fs_url"]     = existing_url
    report["fs_item_id"] = existing_item
    report["fs_folder"]  = existing_folder

    # Mismatch folder: l'utente ha scelto una folder diversa da quella in
    # cui vive l'FS esistente. Non è bloccante: è un'informazione utile
    # soprattutto in mode=replace (il nuovo FS sarà creato nella folder
    # richiesta, spostando di fatto il servizio
    # L'API ArcGIS ritorna `ownerFolder` come folder ID (hash) mentre il
    # frontend invia la folder come `title`. Prima di confrontare, risolviamo
    # l'id → title via _list_user_folders, così evitiamo falsi positivi
    if existing_url and folder:
        _req = (folder or "").strip() or None
        _cur_id = (existing_folder or "").strip() or None
        _cur_title = None
        if _cur_id:
            try:
                _folders = _list_user_folders(client, conn)
                _by_id = {f.get("id"): f.get("title") for f in _folders if f.get("id")}
                _cur_title = _by_id.get(_cur_id)
            except Exception as e:
                logger.warning(f"_list_user_folders fallita nel validator: {e}")
        # Rappresentazione user-friendly: preferiamo il title se disponibile
        _cur_display = _cur_title or _cur_id
        # Confronto: se il title risolto coincide con la request → match
        # Altrimenti fallback al confronto con l'id raw (retrocompat
        _match = (_cur_title is not None and _cur_title == _req) or (_cur_id == _req)
        if not _match:
            _add_top_issue(
                "warning", "FOLDER_MISMATCH",
                f"FS '{fs_name}' esiste nella folder '{_cur_display or 'root'}' "
                f"ma hai selezionato '{_req or 'root'}'. "
                f"In mode='{mode}' " + (
                    "il servizio verrà ricreato nella folder richiesta."
                    if mode == "replace"
                    else "la folder selezionata verrà ignorata (si opera sull'FS esistente)."
                )
            )

    # Le modalita' append/upsert/update richiedono un FS esistente
    if not existing_url and mode in ("append", "upsert", "update"):
        _add_top_issue(
            "error", "FS_NOT_FOUND",
            f"Modalita' '{mode}' richiede un FS esistente '{fs_name}' "
            f"che non e' stato trovato. Usa 'replace' per la prima pubblicazione."
        )

    # Controllo "zombie service": per mode=replace, se NON vediamo il portal
    # item come utente corrente ma il nome e' gia' riservato sul hosted server
    # createService fallira'. Rileviamolo ora cosi' l'utente lo sa subito
    if mode == "replace" and not existing_url:
        try:
            available, _err = _is_service_name_available(client, conn, fs_name)
            if not available:
                _add_top_issue(
                    "error", "FS_NAME_RESERVED",
                    f"Il nome '{fs_name}' e' gia' riservato nell'organizzazione "
                    f"ma non e' visibile all'utente '{conn.username}'. "
                    f"Possibili cause: servizio zombie dopo cancellazione parziale; "
                    f"item di un altro utente dell'org. "
                    f"Soluzione: scegli un nome diverso (es. '{fs_name}_v2') "
                    f"oppure contatta l'admin per rimuovere il residuo."
                )
        except Exception as e:
            logger.warning(f"isServiceNameAvailable check fallita: {e}")

    # Carica i layer del FS esistente (se presente) → mapping name → info
    # `existing_schema_by_layer[name]` = {field_name: FieldInfo dict} dove
    # FieldInfo contiene {coarse, length, nullable}. `existing_layer_meta`
    # tiene info per-layer (capabilities, spatial ref wkid
    existing_layers: Dict[str, dict] = {}
    existing_schema_by_layer: Dict[str, Dict[str, Dict[str, Any]]] = {}
    existing_layer_meta: Dict[str, Dict[str, Any]] = {}
    if existing_url:
        try:
            lst = client.list_layers(conn, existing_url) or []
            for lyr in lst:
                if lyr.get("name"):
                    existing_layers[lyr["name"]] = lyr
        except Exception as e:
            _add_top_issue("warning", "LIST_LAYERS_FAILED",
                           f"Impossibile listare i layer del FS esistente: {e}")

        # Per ogni layer trovato, estrai schema dettagliato (type + length +
        # nullable) + meta (capabilities, wkid) per i check a valle
        for name, lyr in existing_layers.items():
            try:
                schema = client.get_layer_schema(conn, lyr["url"])
                fields = schema.get("fields", []) or []
                # NOTA: il client usa la chiave "field_type" (non "type"
                # ma per retrocompat leggiamo entrambe
                existing_schema_by_layer[name] = {
                    f["name"]: {
                        "coarse":   _coarse_type_from_esri(
                            f.get("field_type") or f.get("type", "")
                        ),
                        "length":   f.get("length"),
                        "nullable": f.get("nullable", True),
                    }
                    for f in fields
                }
                sr = schema.get("spatial_reference") or {}
                existing_layer_meta[name] = {
                    "capabilities": schema.get("capabilities", "") or "",
                    "wkid": sr.get("latestWkid") or sr.get("wkid"),
                }
            except Exception as e:
                logger.warning(f"get_layer_schema '{name}' fallita: {e}")
                existing_schema_by_layer[name] = {}
                existing_layer_meta[name] = {"capabilities": "", "wkid": None}

    # ── CRS check: bundle è già in EPSG:4326 dopo load_imdf_layers
    # Se il FS è in un SRS diverso, ArcGIS fa una reprojezione server-side
    # al publish. Di solito fila, ma per sistemi molto anisotropi (es
    # proiezioni locali) può causare shift visibili. Warning informativo
    try:
        _sample_gdf = next(iter(bundle_layers.values()), None)
        _bundle_wkid = None
        if _sample_gdf is not None and getattr(_sample_gdf, "crs", None):
            try:
                _bundle_wkid = _sample_gdf.crs.to_epsg()
            except Exception:
                _bundle_wkid = None
        # Se il bundle ha wkid e il FS ha wkid, confronta (primo layer FS
        if _bundle_wkid and existing_layer_meta:
            _fs_wkid = next(
                (m.get("wkid") for m in existing_layer_meta.values() if m.get("wkid")),
                None,
            )
            if _fs_wkid and int(_fs_wkid) != int(_bundle_wkid):
                _add_top_issue(
                    "warning", "CRS_MISMATCH",
                    f"Bundle in EPSG:{_bundle_wkid}, FS in EPSG:{_fs_wkid}. "
                    f"ArcGIS reprojetterà le feature lato server: verifica "
                    f"che il risultato sia accettabile per la tua area."
                )
    except Exception as e:
        logger.warning(f"CRS check fallito: {e}")

    # ── Capabilities check: per mode che modificano FS esistente serve
    # il capability corrispondente. Se manca è un errore bloccante
    if existing_url and mode in ("append", "upsert", "update") and existing_layer_meta:
        _caps_sample = next(iter(existing_layer_meta.values())).get("capabilities", "")
        _caps_set = {c.strip().lower() for c in _caps_sample.split(",") if c.strip()}
        _needed = {
            "append": {"create"},
            "upsert": {"create", "delete"},
            "update": {"update"},
        }[mode]
        _missing_caps = sorted(_needed - _caps_set)
        if _missing_caps:
            _add_top_issue(
                "error", "MISSING_CAPABILITIES",
                f"Il FS '{fs_name}' non espone le capability "
                f"{_missing_caps} richieste per mode='{mode}'. "
                f"Capability attuali: '{_caps_sample or '(vuoto)'}'. "
                f"Contatta l'owner per abilitare Edit."
            )

    # ── 4. Per-layer report ────────────────────────────────────────
    for name in layers_present:
        gdf = bundle_layers[name]
        bundle_count = len(gdf)

        layer_report: Dict[str, Any] = {
            "name": name,
            "action": mode,
            "bundle_count": bundle_count,
            "fs_count": None,
            "fs_layer_id": None,
            "match_column": effective_match.get(name),
            "will_delete": 0,
            "will_add": 0,
            "will_update": 0,
            "will_skip": 0,
            "delta": "",
            "schema_compat": "ok",
            "schema_issues": [],
            "data_issues": [],
        }

        # Info layer esistente (se FS c'e'
        existing_info = existing_layers.get(name)
        if existing_info:
            layer_report["fs_layer_id"] = existing_info.get("id")
            fs_cnt = existing_info.get("feature_count")
            layer_report["fs_count"] = int(fs_cnt) if fs_cnt is not None else None

        # Action specifica
        if not report["fs_exists"] or existing_info is None:
            # Nessun FS o layer non esiste → sara' creato
            if mode == "replace":
                layer_report["action"] = "create" if not report["fs_exists"] else "create-layer"
                layer_report["will_add"] = bundle_count
                layer_report["delta"] = f"+{bundle_count}"
            elif mode in ("append", "upsert", "update"):
                # Bloccante: abbiamo gia' _add_top_issue FS_NOT_FOUND
                layer_report["action"] = "error"
                layer_report["schema_issues"].append({
                    "severity": "error", "code": "MISSING_FS_LAYER",
                    "message": f"Layer '{name}' non trovato nel FS (mode={mode})"
                })
        else:
            # Il layer esiste nel FS
            if mode == "replace":
                layer_report["action"] = "replace"
                layer_report["will_delete"] = layer_report["fs_count"] or 0
                layer_report["will_add"] = bundle_count
                d = bundle_count - (layer_report["fs_count"] or 0)
                layer_report["delta"] = (f"{'+' if d >= 0 else ''}{d}"
                                          if layer_report["fs_count"] is not None else f"+{bundle_count}")

            elif mode == "append":
                layer_report["action"] = "append"
                layer_report["will_add"] = bundle_count
                layer_report["delta"] = f"+{bundle_count}"

                # Check duplicati business key: ArcGIS append NON de-duplica
                # quindi se il bundle contiene chiavi già presenti sul FS il
                # risultato sono feature duplicate (stessa business key, diversi
                # OBJECTID). Emettiamo un warning quantitativo così l'utente sa
                # che sta creando duplicati consapevoli o deve usare upsert
                # Chunk più piccolo (50 vs 200 dell'upsert/update) per evitare
                # URL troppo lunghi: il controllo e' best-effort, se fallisce
                # il publish procede comunque
                match_col = layer_report["match_column"]
                if match_col and match_col in gdf.columns:
                    vals_series = gdf[match_col].dropna()
                    vals_unique = list(vals_series.unique().tolist())
                    if vals_unique:
                        total_overlap = 0
                        check_failed_msg = None
                        try:
                            # POST /query con returnCountOnly: più robusto di
                            # GET (URL length, content negotiation). Chunk 100
                            for batch in _chunked(vals_unique, 100):
                                where = (
                                    f"{match_col} IN ("
                                    f"{','.join(_quote_sql_value(v) for v in batch)}"
                                    f")"
                                )
                                total_overlap += _count_features_where_via_post(
                                    client, conn, existing_info["url"], where,
                                )
                        except Exception as e:
                            logger.warning(
                                f"APPEND dup check fallito per '{name}': {e} "
                                f"(match_col={match_col}, n_values={len(vals_unique)}, "
                                f"layer_url={existing_info.get('url')})"
                            )
                            check_failed_msg = str(e)
                            total_overlap = 0

                        if check_failed_msg is not None:
                            layer_report["data_issues"].append({
                                "severity": "warning",
                                "code": "APPEND_DUPLICATE_CHECK_FAILED",
                                "message": (
                                    f"Non è stato possibile verificare "
                                    f"preventivamente i duplicati su '{match_col}' "
                                    f"(query FS fallita). Il publish procederà "
                                    f"comunque: usa 'upsert' se vuoi essere "
                                    f"certo di non creare righe duplicate."
                                ),
                            })
                        elif total_overlap > 0:
                            layer_report["data_issues"].append({
                                "severity": "warning",
                                "code": "APPEND_DUPLICATE_KEYS",
                                "message": (
                                    f"{total_overlap} di {len(vals_unique)} valori "
                                    f"di '{match_col}' del bundle sono già presenti "
                                    f"sul FS. Append creerà feature duplicate "
                                    f"(stessa chiave, OBJECTID diversi). "
                                    f"Usa 'upsert' per sostituire le esistenti."
                                ),
                            })
                            layer_report["delta"] = (
                                f"+{bundle_count} (⚠ {total_overlap} duplicati)"
                            )

            elif mode in ("upsert", "update"):
                match_col = layer_report["match_column"]
                if not match_col or match_col not in gdf.columns:
                    layer_report["data_issues"].append({
                        "severity": "error",
                        "code": "MATCH_COL_MISSING",
                        "message": f"Colonna '{match_col}' assente nel GDB ({mode} impossibile)"
                    })
                else:
                    # Query live su FS per contare quante feature matchano
                    # usato sia per upsert.will_delete sia per update.will_update
                    # Usiamo POST /query con returnCountOnly (_count_features_where_via_post
                    # per evitare il bug GET/JSONDecodeError
                    vals_series = gdf[match_col].dropna()
                    vals_unique = vals_series.unique().tolist()
                    total_match_hits = 0
                    match_query_failed = False
                    try:
                        for batch in _chunked(vals_unique, 100):
                            where = f"{match_col} IN ({','.join(_quote_sql_value(v) for v in batch)})"
                            total_match_hits += _count_features_where_via_post(
                                client, conn, existing_info["url"], where,
                            )
                    except Exception as e:
                        logger.warning(
                            f"MATCH query fallita per '{name}': {e} "
                            f"(match_col={match_col}, n_values={len(vals_unique)}, "
                            f"layer_url={existing_info.get('url')})"
                        )
                        layer_report["data_issues"].append({
                            "severity": "warning", "code": "MATCH_QUERY_FAILED",
                            "message": (
                                f"Impossibile verificare preventivamente i match "
                                f"su '{match_col}' (query FS fallita). "
                                f"Il publish procederà ma il delta stimato sotto "
                                f"potrebbe non essere accurato."
                            ),
                        })
                        match_query_failed = True

                    if mode == "upsert":
                        layer_report["action"] = "upsert"
                        layer_report["will_delete"] = total_match_hits
                        layer_report["will_add"] = bundle_count
                        net = bundle_count - total_match_hits
                        layer_report["delta"] = (
                            f"+{bundle_count} / -{total_match_hits} "
                            f"(net {'+' if net>=0 else ''}{net})"
                        )
                    else:  # update — modalita' STRICT
                        # In update non tolleriamo ambiguita': qualunque
                        # match-key NULL, duplicato o riga senza match sul FS
                        # e' un errore bloccante (non silent-skip
                        # Le altre regole (NULL/duplicati sul match_col) sono
                        # gia' state aggiunte piu' sotto nella sezione "data
                        # quality" — qui promuoviamo eventuali warning a error
                        # per la mode update

                        # will_update = min(uniq_match_hits, bundle_count
                        # Se bundle ha duplicati, matched_bundle_rows >= uniq_hits
                        matched_unique_keys = total_match_hits
                        # righe del bundle che hanno un valore nel dict degli hit
                        # qui assumiamo bundle_count ~= righe, eventuali NULL
                        # verranno contati dai check sotto
                        unmatched_unique_keys = max(0, len(vals_unique) - total_match_hits)
                        layer_report["action"] = "update"
                        layer_report["will_update"] = min(bundle_count, total_match_hits)
                        layer_report["will_skip"]   = 0  # strict: skip = error
                        layer_report["delta"] = f"~{min(bundle_count, total_match_hits)} update"

                        # Non emettere UNMATCHED_BUNDLE_FEATURES se la query
                        # di match è fallita: il conteggio è inaffidabile e
                        # emetterebbe un falso positivo cascading
                        if unmatched_unique_keys > 0 and not match_query_failed:
                            layer_report["data_issues"].append({
                                "severity": "error",
                                "code": "UNMATCHED_BUNDLE_FEATURES",
                                "message": (
                                    f"{unmatched_unique_keys} valori di '{match_col}' "
                                    f"del bundle non hanno match nel FS. "
                                    f"update strict richiede che ogni feature del "
                                    f"bundle esista gia' nel FS."
                                )
                            })

        if existing_info and existing_schema_by_layer.get(name):
            fs_schema = existing_schema_by_layer[name]
            # Campi bundle (esclusa geometry). Per ogni campo raccogliamo
            # anche max_len (per stringhe) e null_count (per nullable check
            bundle_fields_info: Dict[str, Dict[str, Any]] = {}
            for col in gdf.columns:
                if col in ("geometry", "OBJECTID", "objectid"):
                    continue
                series = gdf[col]
                coarse = _coarse_type_from_dtype(series.dtype)
                info: Dict[str, Any] = {"coarse": coarse}
                # Max string length
                if coarse == "string":
                    try:
                        m = series.astype(str).str.len().max()
                        info["max_len"] = int(m) if m is not None else 0
                    except Exception:
                        info["max_len"] = None
                # Null count
                try:
                    info["null_count"] = int(series.isna().sum())
                except Exception:
                    info["null_count"] = 0
                bundle_fields_info[col] = info

            # Campi sul FS esistente (esclusi system). Normalizziamo il
            # nome in uppercase e togliamo separatori per matchare sia le
            # varianti Esri classiche (Shape_Area) sia quelle del nuovo
            # hosted FS (Shape__Area con doppio underscore
            # BE-IMDF-001: helper canonico in
            # `app.shared.esri_system_fields.is_esri_system_field`
            # Pattern di normalizzazione bit-per-bit invariato
            # `name.upper.replace("_", "")`
            from app.shared.esri_system_fields import is_esri_system_field as _is_system_field

            fs_fields_info = {k: v for k, v in fs_schema.items() if not _is_system_field(k)}
            # Alias: i check "presenza" devono essere case-insensitive
            # ArcGIS ignora il case sui nomi campo). Manteniamo però i
            # nomi originali per i messaggi all'utente
            fs_fields_lower = {k.lower(): k for k in fs_fields_info}
            bundle_fields_lower = {k.lower(): k for k in bundle_fields_info}

            fs_fields = {k: v["coarse"] for k, v in fs_fields_info.items()}
            bundle_fields = {k: v["coarse"] for k, v in bundle_fields_info.items()}

            strict_mode = (mode == "update")

            # 1. Campi nel bundle NON nel FS (match case-insensitive
            for fname, ftype in bundle_fields.items():
                if fname.lower() not in fs_fields_lower:
                    layer_report["schema_issues"].append({
                        "severity": "error" if strict_mode else "warning",
                        "code": "FIELD_MISSING_ON_FS",
                        "message": (
                            f"Campo '{fname}' presente nel bundle ma assente sul FS"
                            + (" — update strict non accetta campi extra"
                               if strict_mode else " (sara' droppato)")
                        )
                    })
            # 2. Campi nel FS NON nel bundle (match case-insensitive
            for fname in fs_fields:
                if fname.lower() not in bundle_fields_lower:
                    layer_report["schema_issues"].append({
                        "severity": "warning", "code": "FIELD_MISSING_ON_BUNDLE",
                        "message": (
                            f"Campo '{fname}' presente sul FS ma assente nel bundle"
                            + (" (rimane invariato in update)"
                               if mode == "update" else " (NULL)")
                        )
                    })
            # 2b. String truncation: per ogni campo string in comune, se
            # il bundle ha valori più lunghi del `length` del campo FS
            # ArcGIS tronca in silenzio. Warning quantitativo
            match_col_for_layer = layer_report.get("match_column")
            common_lower = set(bundle_fields_lower) & set(fs_fields_lower)
            for key in common_lower:
                bname = bundle_fields_lower[key]
                fname = fs_fields_lower[key]
                b_info = bundle_fields_info[bname]
                f_info = fs_fields_info[fname]
                if (b_info.get("coarse") == "string"
                        and f_info.get("coarse") == "string"):
                    b_max = b_info.get("max_len")
                    f_len = f_info.get("length")
                    if (b_max is not None and f_len is not None
                            and isinstance(f_len, int) and f_len > 0
                            and b_max > f_len):
                        is_mc = (bname == match_col_for_layer or fname == match_col_for_layer)
                        layer_report["data_issues"].append({
                            "severity": "error" if is_mc else "warning",
                            "code": "STRING_TRUNCATION",
                            "message": (
                                f"Campo '{fname}': il bundle ha valori fino a "
                                f"{b_max} caratteri ma il FS ammette max {f_len}. "
                                f"ArcGIS troncherà i valori più lunghi in silenzio."
                            ),
                        })

            # 2c. NULL su campi FS non-nullable: se il bundle contiene NULL
            # in un campo che sul FS è `nullable=False`, il publish può
            # rifiutare la riga (append/upsert) o fallire lo schema check
            # update). Error in append/upsert/update, warning in replace
            for key in common_lower:
                bname = bundle_fields_lower[key]
                fname = fs_fields_lower[key]
                f_info = fs_fields_info[fname]
                b_info = bundle_fields_info[bname]
                if f_info.get("nullable") is False:
                    null_cnt = b_info.get("null_count") or 0
                    if null_cnt > 0:
                        layer_report["data_issues"].append({
                            "severity": "error" if mode != "replace" else "warning",
                            "code": "NULL_ON_NON_NULLABLE",
                            "message": (
                                f"Campo '{fname}' è NOT NULL sul FS ma il bundle "
                                f"ha {null_cnt} valori NULL. "
                                f"Le righe NULL verranno rifiutate al publish."
                            ),
                        })

            # 3. Type mismatch (su campi in comune, match case-insensitive
            for key in common_lower:
                bname = bundle_fields_lower[key]
                fname = fs_fields_lower[key]
                bt = bundle_fields[bname]
                ft = fs_fields[fname]
                is_match_col = (bname == match_col_for_layer or fname == match_col_for_layer)

                if bt != ft:
                    other_involved = (bt == "other" or ft == "other")
                    if other_involved:
                        # Tipo non mappabile: è rumore fuorché sulla match
                        # column (dove il rischio di key mismatch è reale
                        # Sui campi normali silenziamo del tutto — l'utente
                        # non può fare nulla con un "tipo non riconosciuto"
                        if not is_match_col:
                            continue
                        sev = "error"
                        msg_suffix = " (match column, tipo non mappato)"
                    else:
                        sev = "error" if (strict_mode or is_match_col) else "warning"
                        msg_suffix = " (match column)" if is_match_col else ""
                    layer_report["schema_issues"].append({
                        "severity": sev, "code": "TYPE_MISMATCH",
                        "message": f"Campo '{fname}': bundle={bt}, FS={ft}{msg_suffix}"
                    })

            if any(i["severity"] == "error" for i in layer_report["schema_issues"]):
                layer_report["schema_compat"] = "mismatch"
            elif layer_report["schema_issues"]:
                layer_report["schema_compat"] = "warning"

        # ── 4c. Data quality sul bundle ──────────────────────────
        match_col = layer_report["match_column"]
        if match_col and match_col in gdf.columns:
            null_count = int(gdf[match_col].isna().sum())
            if null_count > 0:
                # update e upsert non tollerano NULL sulla business key
                sev = "error" if mode in ("upsert", "update") else "warning"
                layer_report["data_issues"].append({
                    "severity": sev, "code": "NULL_MATCH_KEY",
                    "message": (
                        f"{null_count} feature con '{match_col}' NULL"
                        + (" — non ammesso in update/upsert"
                           if mode in ("upsert", "update") else "")
                    )
                })
            dup_count = int(gdf[match_col].duplicated(keep=False).sum()
                            - gdf[match_col].isna().sum())
            if dup_count > 0:
                if mode == "update":
                    # Strict: duplicati → errore (race condition in updateFeatures
                    layer_report["data_issues"].append({
                        "severity": "error", "code": "DUPLICATE_MATCH_KEY",
                        "message": (
                            f"{dup_count} feature con '{match_col}' duplicato — "
                            f"update strict non ammette chiavi duplicate "
                            f"(l'ordine di updateFeatures non e' garantito)"
                        )
                    })
                elif mode == "upsert":
                    layer_report["data_issues"].append({
                        "severity": "warning", "code": "DUPLICATE_MATCH_KEY",
                        "message": f"{dup_count} feature con '{match_col}' duplicato "
                                 f"(upsert manterra' l'ultima)"
                    })

        # Geometry nulla
        if "geometry" in gdf.columns:
            null_geom = int(gdf["geometry"].isna().sum())
            if null_geom > 0:
                layer_report["data_issues"].append({
                    "severity": "warning", "code": "NULL_GEOMETRY",
                    "message": f"{null_geom} feature senza geometria"
                })

        # Tally errors/warnings per-layer → somma globale
        for issue in layer_report["schema_issues"] + layer_report["data_issues"]:
            if issue["severity"] == "error":
                report["summary"]["errors"] += 1
                report["ok"] = False
            elif issue["severity"] == "warning":
                report["summary"]["warnings"] += 1

        report["layers"].append(layer_report)

    return report
