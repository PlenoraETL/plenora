"""
Intermediate persistence policy.

Centralizza la decisione "questo snapshot va registrato? in che mode?".
Gli executor (MANIPOLA/ESTRAI/ARRICCHISCI) chiamano `record_*_snapshot()`
da `snapshot.py`, che a sua volta interroga `resolve_intermediate_policy`
per evitare lavoro inutile (no `df.to_dict`, no hash, no DB) quando la
policy globale dice di non registrare.

Mode supportati:
  - metadata_only (default produzione): solo node_meta + node_hash
  - preview_only (legacy/debug): meta + hash + prime N righe in node_rows
  - full_pg (legacy compat): tutte le righe in node_rows
  - full_artifact: meta + hash + Parquet/manifest su runtime object
    storage. Richiede `OBJECT_STORAGE_ENABLED=true` + bucket runtime;
    cross-validato in `validate_intermediate_settings`.

Il modulo contiene SOLO logica pura: lettura settings, validazione,
calcolo policy. Nessun side-effect (no DB, no I/O).

Vedi anche:
- orchestrate/phase_6_product_grade/intermediate_persistence_to_be.md
- orchestrate/phase_6_product_grade/manipola_checkpoint_policy.md
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


# Costanti / valori ammessi

# Mode globale di persistenza. Tutti supportati end-to-end:
# metadata_only/preview_only/full_pg via node_rows (PG); full_artifact
# via Parquet + manifest su runtime object storage.
MODE_METADATA_ONLY = "metadata_only"
MODE_PREVIEW_ONLY = "preview_only"
MODE_FULL_PG = "full_pg"
MODE_FULL_ARTIFACT = "full_artifact"

# Valori che la pipeline puo' effettivamente eseguire.
# `full_artifact` aggiunto nel batch object storage intermedi
# (2026-04-28): richiede OBJECT_STORAGE_ENABLED=true e bucket runtime
# configurato (vedi validate_intermediate_settings).
SUPPORTED_PERSISTENCE_MODES = frozenset({
    MODE_METADATA_ONLY,
    MODE_PREVIEW_ONLY,
    MODE_FULL_PG,
    MODE_FULL_ARTIFACT,
})

# Mode pianificate ma non ancora implementate (riservato per evitare
# typo). Mantenuto come hook per future estensioni.
PLANNED_PERSISTENCE_MODES = frozenset()

# MANIPOLA checkpoint policy. Vedi `manipola_checkpoint_policy.md`.
MANIPOLA_CHECKPOINTS_NONE = "none"
MANIPOLA_CHECKPOINTS_CRITICAL = "critical"
MANIPOLA_CHECKPOINTS_MARKED = "marked"
MANIPOLA_CHECKPOINTS_CRITICAL_OR_MARKED = "critical_or_marked"
MANIPOLA_CHECKPOINTS_ALL = "all"

SUPPORTED_MANIPOLA_CHECKPOINTS = frozenset({
    MANIPOLA_CHECKPOINTS_NONE,
    MANIPOLA_CHECKPOINTS_CRITICAL,
    MANIPOLA_CHECKPOINTS_MARKED,
    MANIPOLA_CHECKPOINTS_CRITICAL_OR_MARKED,
    MANIPOLA_CHECKPOINTS_ALL,
})

MODULE_CHECKPOINTS_FINAL_ONLY = "final_only"
SUPPORTED_FINAL_ONLY_CHECKPOINTS = frozenset({MODULE_CHECKPOINTS_FINAL_ONLY})

# Snapshot kind (parametro API).
SNAPSHOT_KIND_FINAL = "final"
SNAPSHOT_KIND_CHECKPOINT = "checkpoint"
SUPPORTED_SNAPSHOT_KINDS = frozenset({
    SNAPSHOT_KIND_FINAL,
    SNAPSHOT_KIND_CHECKPOINT,
})

# Modulo (parametro API).
MODULE_ESTRAI = "estrai"
MODULE_MANIPOLA = "manipola"
MODULE_ARRICCHISCI = "arricchisci"
SUPPORTED_MODULES = frozenset({
    MODULE_ESTRAI,
    MODULE_MANIPOLA,
    MODULE_ARRICCHISCI,
})

# Set transform "critical" MANIPOLA (vedi manipola_checkpoint_policy.md).
# Lista chiusa: ogni transform_op fuori da queste tre liste viene
# marcato `noncritical` di default. Aggiungere qui prima di taggare un
# transform come critical, mai inline negli executor.
MANIPOLA_CRITICAL_TRANSFORMS = frozenset({
    # cardinalita' / combinazione input
    "join", "concat", "cross_join", "table_diff",
    "aggregate", "pivot", "melt", "transpose",
    "distinct", "dedup_advanced", "filter", "sample",
    "drop_columns",
    # GIS critical
    "geo_clip", "geo_difference", "geo_intersection", "geo_overlay",
    "geo_union", "geo_symmetric_difference",
    "sjoin", "geo_nearest", "geo_within",
    "geo_count_points_in_polygons",
    "geo_dissolve", "geo_explode",
    "geo_line_builder", "geo_polygon_builder",
})

# Hash algo versione corrente. Persistito in node_meta.hash_algo.
# Bug #24 (2026-04-28): la mode globale `INTERMEDIATE_FINGERPRINT_MODE`
# decide quale algoritmo applicare al checkpoint:
#   - "sample" -> HASH_ALGO_PANDAS_SAMPLE_V1 (default operativo)
#   - "full"   -> HASH_ALGO_PANDAS_HASH_V1   (audit forte)
# `node_meta.hash_algo` riflette quello effettivamente usato per il
# record, cosi' un consumer downstream sa se confrontare apple-to-apple.
HASH_ALGO_PANDAS_HASH_V1 = "pandas_hash_v1"
HASH_ALGO_PANDAS_SAMPLE_V1 = "pandas_sample_hash_v1"

FINGERPRINT_MODE_SAMPLE = "sample"
FINGERPRINT_MODE_FULL = "full"
SUPPORTED_FINGERPRINT_MODES = frozenset({
    FINGERPRINT_MODE_SAMPLE,
    FINGERPRINT_MODE_FULL,
})

# Artifact status legali. Popolati da `snapshot._record_snapshot` quando
# mode=full_artifact: "complete" su upload Parquet+manifest riuscito,
# "failed" su errore best-effort (capability off, serialize/upload fail),
# "none" su tutti gli altri mode (metadata_only/preview_only/full_pg).
# "pending" riservato per future async writer.
ARTIFACT_STATUS_NONE = "none"
ARTIFACT_STATUS_PENDING = "pending"
ARTIFACT_STATUS_COMPLETE = "complete"
ARTIFACT_STATUS_FAILED = "failed"


# Dataclass output policy

@dataclass(frozen=True)
class IntermediatePolicy:
    """Decisione presa dalla policy per UN singolo snapshot.

    Letta dal layer `snapshot.py`:
      - se `should_record_meta` False -> ritorno immediato, no hash, no DB
      - se `should_record_meta` True ma `should_record_rows` False ->
        scrivi solo meta+hash (mode metadata_only)
      - se `should_record_rows` True con `preview_rows>0` -> persisti N
      - se `persistence_mode == "full_pg"` -> persisti tutte le righe

    `reason` e' una stringa breve diagnostica usata in log/test
    (es. "manipola_critical_join", "manipola_skip_noncritical_rename").
    """
    should_record_meta: bool
    should_record_rows: bool
    persistence_mode: str
    preview_rows: int
    reason: str

    @property
    def recorded(self) -> bool:
        return self.should_record_meta


# Sentinel "nessuna registrazione". Riusato per evitare allocazione di
# nuove istanze nel hot path checkpoint-skip.
_POLICY_SKIP_TEMPLATE = IntermediatePolicy(
    should_record_meta=False,
    should_record_rows=False,
    persistence_mode=MODE_METADATA_ONLY,
    preview_rows=0,
    reason="",
)


def _skip(reason: str) -> IntermediatePolicy:
    """Costruttore helper per le decisioni 'skip' con `reason` esplicita."""
    return IntermediatePolicy(
        should_record_meta=False,
        should_record_rows=False,
        persistence_mode=MODE_METADATA_ONLY,
        preview_rows=0,
        reason=reason,
    )


# Lettura settings (lazy)

def _read_settings() -> dict:
    """Snapshot dei valori settings rilevanti.

    Lettura lazy via `app.config.settings.get(...)` per rispettare
    eventuali override env injettati dopo l'import.
    """
    from app.config import settings
    return {
        "mode": settings.get("INTERMEDIATE_PERSISTENCE_MODE"),
        "preview_rows": settings.get("INTERMEDIATE_PREVIEW_ROWS"),
        "artifact_format": settings.get("INTERMEDIATE_FULL_ARTIFACT_FORMAT"),
        "manipola_checkpoints": settings.get("MANIPOLA_INTERMEDIATE_CHECKPOINTS"),
        "estrai_checkpoints": settings.get("ESTRAI_INTERMEDIATE_CHECKPOINTS"),
        "arricchisci_checkpoints": settings.get("ARRICCHISCI_INTERMEDIATE_CHECKPOINTS"),
        "artifact_bucket": settings.get("PLENORA_ARTIFACT_BUCKET"),
        "artifact_prefix": settings.get("INTERMEDIATE_ARTIFACT_PREFIX"),
        # Bug #24
        "fingerprint_mode": settings.get("INTERMEDIATE_FINGERPRINT_MODE"),
        "fingerprint_sample_rows": settings.get("INTERMEDIATE_FINGERPRINT_SAMPLE_ROWS"),
    }


# Validazione settings (fail-fast)

class IntermediateSettingsError(ValueError):
    """Settings persistenza intermedi non valide.

    Sottoclasse di `ValueError` per integrarsi con i test pattern e con
    `validate_storage_config` esistente, ma identificabile via isinstance
    quando serve distinguere la causa.
    """


def validate_intermediate_settings(values: Optional[dict] = None) -> None:
    """Valida le settings intermediate. Solleva su valore invalido.

    Pensata per essere chiamata:
      - esplicitamente al boot (futuro hook in main.py);
      - lazily al primo `resolve_intermediate_policy` (vedi sotto).

    `values` opzionale per testing; se None legge dai settings correnti.
    """
    cfg = values if values is not None else _read_settings()

    mode = cfg.get("mode")
    if mode in PLANNED_PERSISTENCE_MODES:
        raise IntermediateSettingsError(
            f"INTERMEDIATE_PERSISTENCE_MODE={mode!r} non e' ancora "
            f"implementato. Mode supportati: {sorted(SUPPORTED_PERSISTENCE_MODES)}."
        )
    if mode not in SUPPORTED_PERSISTENCE_MODES:
        raise IntermediateSettingsError(
            f"INTERMEDIATE_PERSISTENCE_MODE={mode!r} non riconosciuto. "
            f"Valori ammessi: {sorted(SUPPORTED_PERSISTENCE_MODES)}."
        )

    # Object storage capability cross-check (Batch object storage
    # intermedi, 2026-04-28). `full_artifact` richiede object storage
    # runtime attivo + bucket configurato. Senza, l'unica via e'
    # cambiare mode (es. metadata_only) o abilitare la capability.
    if mode == MODE_FULL_ARTIFACT:
        # Lazy import per evitare cicli (cloud_backends importa
        # eventualmente settings indirettamente).
        from app.shared.cloud_backends import object_storage_enabled
        if not object_storage_enabled():
            raise IntermediateSettingsError(
                "INTERMEDIATE_PERSISTENCE_MODE=full_artifact richiede "
                "OBJECT_STORAGE_ENABLED=true (capability runtime object "
                "storage). Imposta OBJECT_STORAGE_ENABLED=true e "
                "OBJECT_STORAGE_BUCKET, oppure cambia mode a "
                "metadata_only / preview_only / full_pg."
            )

    preview_rows = cfg.get("preview_rows")
    try:
        preview_rows_int = int(preview_rows)
    except (TypeError, ValueError):
        raise IntermediateSettingsError(
            f"INTERMEDIATE_PREVIEW_ROWS={preview_rows!r} non e' un intero."
        ) from None
    if preview_rows_int <= 0:
        raise IntermediateSettingsError(
            f"INTERMEDIATE_PREVIEW_ROWS={preview_rows_int} deve essere > 0."
        )

    manipola = cfg.get("manipola_checkpoints")
    if manipola not in SUPPORTED_MANIPOLA_CHECKPOINTS:
        raise IntermediateSettingsError(
            f"MANIPOLA_INTERMEDIATE_CHECKPOINTS={manipola!r} non riconosciuto. "
            f"Valori ammessi: {sorted(SUPPORTED_MANIPOLA_CHECKPOINTS)}."
        )

    estrai = cfg.get("estrai_checkpoints")
    if estrai not in SUPPORTED_FINAL_ONLY_CHECKPOINTS:
        raise IntermediateSettingsError(
            f"ESTRAI_INTERMEDIATE_CHECKPOINTS={estrai!r} non ammesso. "
            f"Valori ammessi: {sorted(SUPPORTED_FINAL_ONLY_CHECKPOINTS)}."
        )

    arricchisci = cfg.get("arricchisci_checkpoints")
    if arricchisci not in SUPPORTED_FINAL_ONLY_CHECKPOINTS:
        raise IntermediateSettingsError(
            f"ARRICCHISCI_INTERMEDIATE_CHECKPOINTS={arricchisci!r} non ammesso. "
            f"Valori ammessi: {sorted(SUPPORTED_FINAL_ONLY_CHECKPOINTS)}."
        )

    # Bug #24: fingerprint mode + sample rows.
    fp_mode = cfg.get("fingerprint_mode")
    if fp_mode not in SUPPORTED_FINGERPRINT_MODES:
        raise IntermediateSettingsError(
            f"INTERMEDIATE_FINGERPRINT_MODE={fp_mode!r} non riconosciuto. "
            f"Valori ammessi: {sorted(SUPPORTED_FINGERPRINT_MODES)}."
        )
    fp_sample = cfg.get("fingerprint_sample_rows")
    try:
        fp_sample_int = int(fp_sample)
    except (TypeError, ValueError):
        raise IntermediateSettingsError(
            f"INTERMEDIATE_FINGERPRINT_SAMPLE_ROWS={fp_sample!r} non e' un intero."
        ) from None
    if fp_sample_int <= 0:
        raise IntermediateSettingsError(
            f"INTERMEDIATE_FINGERPRINT_SAMPLE_ROWS={fp_sample_int} deve essere > 0."
        )

    # PLENORA_ARTIFACT_BUCKET e INTERMEDIATE_ARTIFACT_PREFIX sono ora
    # consumati dal path full_artifact:
    #   - bucket: fallback in `cloud_backends._resolve_runtime_bucket`
    #     dietro a OBJECT_STORAGE_BUCKET (priorita' 2);
    #   - prefix: usato da `artifact_writer.build_artifact_key_prefix`
    #     per il layout dell'object storage.
    # Niente validazione stringente qui: bucket puo' restare None se
    # OBJECT_STORAGE_BUCKET e' settato; prefix accetta qualunque stringa
    # (sanity check applicato lato writer).


# Cache: validazione idempotente, una volta sola per processo. Reset
# manuale via `reset_validation_cache()` per i test.
_validated_once = False


def _ensure_validated() -> None:
    global _validated_once
    if _validated_once:
        return
    validate_intermediate_settings()
    _validated_once = True


def reset_validation_cache() -> None:
    """Reset interno per testing. Forza la prossima resolve a rivalidare."""
    global _validated_once
    _validated_once = False


# Per-call mode override (R-25 PR-B1)

def resolve_intermediate_mode(
    default_mode: str,
    override_mode: Optional[str] = None,
) -> str:
    """Risolve la mode di persistenza effettiva per un singolo snapshot.

    Pure function: zero I/O, zero settings read, zero side-effect. Pensata
    per essere chiamata DENTRO `resolve_intermediate_policy` quando arriva
    un `mode_override` lungo la call-chain (introdotto in PR-B2 sul path
    estrai-final). Caller passa `default_mode=cfg["mode"]`, eventuale
    `override_mode` viene da kwarg propagato dall'alto.

    Args:
        default_mode: mode globale corrente (gia' validato a startup da
            `validate_intermediate_settings`). Caller responsability.
        override_mode: override per-call. None -> nessun override
            (ritorno bit-compatible con il default-path pre-PR-B1).

    Returns:
        Mode effettiva da applicare allo snapshot.

    Raises:
        IntermediateSettingsError: override_mode non None e non valido.
            Casi: tipo non-str, stringa vuota, valore non in
            `SUPPORTED_PERSISTENCE_MODES`, valore in
            `PLANNED_PERSISTENCE_MODES`. Fail-loud, mai fallback al
            default: un override sbagliato deve essere visibile al chiamante.

    Note:
        Capability runtime (es. `full_artifact` richiede object storage)
        NON e' verificata qui. Resta responsabilita' di
        `validate_intermediate_settings` a startup oppure del consumer
        (snapshot writer) che fail-closed se la capability manca al
        momento della scrittura. Questa funzione valida solo l'ENUM.
    """
    if override_mode is None:
        return default_mode

    # Type check esplicito: bool e' sottoclasse di int in Python ma
    # non vogliamo `True/False` accettati come override silenziosi.
    if not isinstance(override_mode, str) or isinstance(override_mode, bool):
        raise IntermediateSettingsError(
            f"intermediate_mode_override deve essere str o None, "
            f"ricevuto type={type(override_mode).__name__} value={override_mode!r}."
        )

    if override_mode in PLANNED_PERSISTENCE_MODES:
        raise IntermediateSettingsError(
            f"intermediate_mode_override={override_mode!r} non e' ancora "
            f"implementato. Mode supportati: {sorted(SUPPORTED_PERSISTENCE_MODES)}."
        )

    if override_mode not in SUPPORTED_PERSISTENCE_MODES:
        raise IntermediateSettingsError(
            f"intermediate_mode_override={override_mode!r} non riconosciuto. "
            f"Valori ammessi: {sorted(SUPPORTED_PERSISTENCE_MODES)}."
        )

    return override_mode


# Resolver policy

def _checkpoint_decision_manipola(
    node_type: str,
    node_data: Optional[dict],
    transform_id: Optional[str],
    checkpoints_setting: str,
) -> tuple[bool, str]:
    """Decide se un nodo MANIPOLA in modalita' `checkpoint` va registrato.

    Ritorna `(should_record, reason)`. Reason e' diagnostico, non
    user-facing: nome stabile per i test.
    """
    # `result` e' final, gestito dal caller -> qui non capita.
    if node_type == "result":
        return True, "manipola_result_final"

    # Policy `none`: nessun checkpoint, anche multi_join. Vince su tutto.
    if checkpoints_setting == MANIPOLA_CHECKPOINTS_NONE:
        return False, "manipola_skip_none_policy"

    # `multi_join` e' sempre critical, indipendentemente da transform_id.
    # (NB: `none` sopra ha gia' fatto opt-out; `marked` sotto puo' richiedere
    # save_intermediate, ma il design e2e considera multi_join come sempre
    # critico nelle policy non-none — vedi manipola_checkpoint_policy.md
    # "Implementazione proposta" §2: multi_join -> critical.)
    if node_type == "multi_join":
        return True, "manipola_multi_join"

    is_marked = bool((node_data or {}).get("save_intermediate"))
    is_critical = (
        transform_id is not None
        and transform_id in MANIPOLA_CRITICAL_TRANSFORMS
    )
    if checkpoints_setting == MANIPOLA_CHECKPOINTS_ALL:
        return True, "manipola_all_policy"
    if checkpoints_setting == MANIPOLA_CHECKPOINTS_MARKED:
        if is_marked:
            return True, "manipola_marked_only"
        return False, "manipola_skip_unmarked"
    if checkpoints_setting == MANIPOLA_CHECKPOINTS_CRITICAL:
        if is_critical:
            return True, "manipola_critical"
        return False, "manipola_skip_noncritical"
    if checkpoints_setting == MANIPOLA_CHECKPOINTS_CRITICAL_OR_MARKED:
        if is_critical:
            return True, "manipola_critical"
        if is_marked:
            return True, "manipola_marked"
        return False, "manipola_skip_noncritical_unmarked"

    # Settings dovrebbero essere gia' validate, ma fail-closed se
    # qualcuno chiama resolve senza passare da validate.
    return False, f"manipola_unknown_policy_{checkpoints_setting}"


def resolve_intermediate_policy(
    module: str,
    snapshot_kind: str,
    node_type: Optional[str],
    node_data: Optional[dict] = None,
    transform_id: Optional[str] = None,
    mode_override: Optional[str] = None,
) -> IntermediatePolicy:
    """Calcola la policy effettiva per uno snapshot.

    Args:
        module: "estrai" | "manipola" | "arricchisci"
        snapshot_kind: "final" | "checkpoint"
        node_type: tipo nodo canvas (es. "transform", "multi_join",
            "result", "api_enrich"). None ammesso per snapshot final
            generici dove il tipo non e' rilevante.
        node_data: dict opzionale del nodo (cerca `save_intermediate`).
        transform_id: per nodi MANIPOLA `transform`, l'id transform
            specifico (es. "join", "filter"). Usato solo per la
            classificazione critical/noncritical.
        mode_override: override per-call della
            `INTERMEDIATE_PERSISTENCE_MODE` globale. None = nessun
            override (bit-compat default-path). Validazione enum via
            `resolve_intermediate_mode` (fail-loud su valori invalidi:
            tipo non-str, stringa vuota, mode non riconosciuto, mode
            planned). Capability runtime (full_artifact + object
            storage) NON e' verificata qui — resta startup-side
            (`validate_intermediate_settings`) o write-time.

    Ritorna:
        IntermediatePolicy. Se la policy decide di NON registrare,
        `should_record_meta=False` e `should_record_rows=False`. Il
        caller deve uscire prima di calcolare hash/preview.

    Raises:
        IntermediateSettingsError: settings invalide al primo uso, o
            `mode_override` non valido (propagato da
            `resolve_intermediate_mode`).
        ValueError: module/snapshot_kind non riconosciuto.
    """
    if module not in SUPPORTED_MODULES:
        raise ValueError(
            f"module={module!r} non riconosciuto. "
            f"Ammessi: {sorted(SUPPORTED_MODULES)}."
        )
    if snapshot_kind not in SUPPORTED_SNAPSHOT_KINDS:
        raise ValueError(
            f"snapshot_kind={snapshot_kind!r} non riconosciuto. "
            f"Ammessi: {sorted(SUPPORTED_SNAPSHOT_KINDS)}."
        )

    _ensure_validated()
    cfg = _read_settings()

    # mode globale, eventualmente sostituita dall'override per-call.
    # `resolve_intermediate_mode` ritorna `cfg["mode"]` se override e' None,
    # altrimenti l'override valido. Su override invalido solleva
    # IntermediateSettingsError che si propaga al chiamante (fail-loud).
    mode = resolve_intermediate_mode(
        default_mode=cfg["mode"],
        override_mode=mode_override,
    )
    preview_rows = int(cfg["preview_rows"])

    if module in (MODULE_ESTRAI, MODULE_ARRICCHISCI):
        if snapshot_kind == SNAPSHOT_KIND_CHECKPOINT:
            return _skip(f"{module}_checkpoints_disabled_in_batch1")
        # final: registra secondo mode globale.
        return _build_record_policy(
            mode=mode,
            preview_rows=preview_rows,
            reason=f"{module}_final",
        )

    # MANIPOLA
    if module == MODULE_MANIPOLA:
        if snapshot_kind == SNAPSHOT_KIND_FINAL:
            return _build_record_policy(
                mode=mode,
                preview_rows=preview_rows,
                reason="manipola_final",
            )
        # checkpoint
        should, reason = _checkpoint_decision_manipola(
            node_type=node_type or "",
            node_data=node_data,
            transform_id=transform_id,
            checkpoints_setting=cfg["manipola_checkpoints"],
        )
        if not should:
            return _skip(reason)
        return _build_record_policy(
            mode=mode,
            preview_rows=preview_rows,
            reason=reason,
        )

    # Unreachable: SUPPORTED_MODULES e' chiuso.
    return _skip("module_unhandled")


def _build_record_policy(
    mode: str, preview_rows: int, reason: str,
) -> IntermediatePolicy:
    """Compone la policy di registrazione effettiva data la mode globale."""
    if mode == MODE_METADATA_ONLY:
        return IntermediatePolicy(
            should_record_meta=True,
            should_record_rows=False,
            persistence_mode=MODE_METADATA_ONLY,
            preview_rows=0,
            reason=reason,
        )
    if mode == MODE_PREVIEW_ONLY:
        return IntermediatePolicy(
            should_record_meta=True,
            should_record_rows=True,
            persistence_mode=MODE_PREVIEW_ONLY,
            preview_rows=preview_rows,
            reason=reason,
        )
    if mode == MODE_FULL_PG:
        return IntermediatePolicy(
            should_record_meta=True,
            should_record_rows=True,
            persistence_mode=MODE_FULL_PG,
            # preview_rows = 0 segnala "no head cap, scrivi tutto" al
            # writer. La distinzione effettiva e' su persistence_mode.
            preview_rows=0,
            reason=reason,
        )
    if mode == MODE_FULL_ARTIFACT:
        # full_artifact: meta + hash + artifact su object storage.
        # ZERO `node_rows` (lo snapshot full vive in object storage).
        # Il writer in `snapshot.py` gestisce la persistenza Parquet +
        # manifest e popola `node_meta.artifact_uri/_status/_format`.
        return IntermediatePolicy(
            should_record_meta=True,
            should_record_rows=False,
            persistence_mode=MODE_FULL_ARTIFACT,
            preview_rows=0,
            reason=reason,
        )
    # Validate dovrebbe gia' aver bloccato. Difensivo:
    raise IntermediateSettingsError(
        f"mode={mode!r} non gestito da _build_record_policy"
    )


# Lettura record legacy

def persistence_mode_for_record(node_meta_row: dict) -> str:
    """Risolve la `persistence_mode` apparente di un record `node_meta`.

    Usata dai reader (router/L2/diff) per sapere se trattare il record
    come legacy `full_pg` o secondo nuova policy. Record scritti prima
    di Batch 1 hanno tutte le colonne nuove NULL: per quelli ritorniamo
    `full_pg`, conservando il significato originale (tutte le righe in
    `node_rows`).
    """
    val = (node_meta_row or {}).get("persistence_mode")
    if val is None or val == "":
        return MODE_FULL_PG
    return str(val)
