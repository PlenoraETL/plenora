"""
PLENORA-ETL — Data Source Service
CRUD configurazioni Data Source → PostgreSQL via app.db.db_service.
Scan state → filesystem (dati di runtime effimeri).
"""

import json
import uuid
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional

from app.config import settings
from app.utils.logger import get_logger
from app.shared.crypto import (
    encrypt_dict_fields as _encrypt_dict_fields,
    decrypt_dict_fields as _decrypt_dict_fields,
)
from app.core.datasources.models import (
    DataSource,
    DataSourceCreate,
    DataSourceUpdate,
    DataSourceList,
    DataSourceTestResult,
    FileInfoResponse,
    ScanState,
)
from app.core.datasources.factory import create_backend

logger = get_logger(__name__)

# Campi sensibili. Usati sia per la redaction in UI (_to_model) sia per la
# cifratura at-rest (encrypt_dict_fields/decrypt_dict_fields). encrypt_dict_fields
# vuole una list, _to_model itera sul set: list() lo accetta entrambi.
_SENSITIVE_FIELDS = ["password", "private_key", "private_key_passphrase", "client_secret"]


class DataSourceService:
    """
    CRUD + operazioni file per i Data Source.
    Configurazioni nel DB (PostgreSQL), scan_state su filesystem (runtime).
    """

    def __init__(self):
        # Solo per scan_state (runtime)
        self._scan_dir: Path = settings.DATA_DIR / "datasources_scan"

    # ── CRUD ──────────────────────────────────────────────────────────────

    def list_datasources(self, group_id: str = None) -> DataSourceList:
        # group_id=None → no filter (superadmin o path allowed_groups)
        # Fix N+1: fetch tutte le row in una query, parse config in memoria
        import app.db.config_db_service as _cfg_db
        if group_id is None:
            rows = _cfg_db.fetchall(
                "SELECT * FROM datasources ORDER BY name"
            )
        else:
            rows = _cfg_db.fetchall(
                "SELECT * FROM datasources WHERE group_id = %s ORDER BY name",
                (group_id,)
            )
        result = []
        for row in rows:
            config = self._row_to_config(row)
            if config:
                result.append(self._to_model(config, redact=True))
        return DataSourceList(datasources=result)

    def _row_to_config(self, row: dict) -> Optional[dict]:
        """Estrae il config dict da una row già fetchata (no re-query).

        Equivalente in-memory di _get_full_config() ma senza re-fetch dal DB.
        Usato da list_datasources per evitare N+1 query.
        """
        if not row:
            return None
        config = row.get("config") or {}
        if isinstance(config, str):
            try:
                config = json.loads(config)
            except json.JSONDecodeError as e:
                logger.warning(f"Config corrotta per datasource {row.get('id')}: {e}")
                return None
        if not isinstance(config, dict):
            return None
        # Decifra campi sensibili at-rest
        _decrypt_dict_fields(config, _SENSITIVE_FIELDS)
        # Inietta group_id e created_by dal record DB (stesso logic di _get_full_config)
        config["group_id"] = row.get("group_id", "")
        config["created_by"] = row.get("created_by")
        return config

    def get_datasource(self, ds_id: str) -> Optional[DataSource]:
        config = self._get_full_config(ds_id)
        if not config:
            return None
        return self._to_model(config, redact=True)

    def create_datasource(self, data: DataSourceCreate, group_id: str = None,
                          created_by: str = None) -> DataSource:
        import app.db.db_service as db
        ds_id = self._generate_id(data.name)
        now = datetime.now(timezone.utc)

        config = {
            "id": ds_id,
            "name": data.name,
            "description": data.description,
            "type": data.type,
            "root_path": data.root_path,
            "host": data.host,
            "port": data.port,
            "username": data.username,
            "password": data.password,
            "private_key": data.private_key,
            "private_key_passphrase": data.private_key_passphrase,
            "host_key_policy": data.host_key_policy,
            "tls": data.tls,
            "passive": data.passive,
            "tenant_id": data.tenant_id,
            "client_id": data.client_id,
            "client_secret": data.client_secret,
            "site_id": data.site_id,
            "site_url": data.site_url,
            "drive_id": data.drive_id,
            "created_at": now.isoformat(),
            "updated_at": now.isoformat(),
        }
        config = {k: v for k, v in config.items() if v is not None}

        # Cifra at-rest i campi sensibili (password, private_key, ...) prima del save.
        # NOTA: passiamo una COPIA per non mutare il dict che restituiamo al caller
        # (_to_model si aspetta plaintext per fare redaction, non valori cifrati).
        config_to_save = dict(config)
        _encrypt_dict_fields(config_to_save, _SENSITIVE_FIELDS)

        db.save_datasource(
            ds_id=ds_id,
            group_id=group_id or settings.DEFAULT_GROUP,
            name=data.name,
            ds_type=data.type,
            config=config_to_save,
            created_by=created_by,
        )

        # Inietta created_by nel config in-memory per il _to_model che popola
        # la response (il re-read dal DB avrebbe lo stesso effetto, ma costa un round trip).
        config["group_id"] = group_id or settings.DEFAULT_GROUP
        config["created_by"] = created_by

        logger.info(f"Data Source creato: {ds_id} (tipo={data.type})")
        return self._to_model(config, redact=True)

    def update_datasource(self, ds_id: str, data: DataSourceUpdate) -> Optional[DataSource]:
        import app.db.db_service as db
        config = self._get_full_config(ds_id)  # già decifrato
        if not config:
            return None

        updates = data.model_dump(exclude_none=True)
        # Filtra i campi sensibili mascherati: il client invia "***" per i
        # campi non modificati e quel valore non deve sovrascrivere il reale.
        for sf in _SENSITIVE_FIELDS:
            if sf in updates and updates[sf] == "***":
                del updates[sf]
        config.update(updates)
        config["updated_at"] = datetime.now(timezone.utc).isoformat()

        # Re-cifra i campi sensibili prima di salvare. Lavoro su una copia
        # per non mutare il dict che _to_model deve usare per la redaction.
        # Rimuovi group_id/created_by dal blob config: sono colonne DB separate,
        # non vanno duplicate nel JSON.
        config_to_save = dict(config)
        config_to_save.pop("group_id", None)
        config_to_save.pop("created_by", None)
        _encrypt_dict_fields(config_to_save, _SENSITIVE_FIELDS)
        db.update_datasource(ds_id, config=config_to_save)
        logger.info(f"Data Source aggiornato: {ds_id}")
        return self._to_model(config, redact=True)

    def delete_datasource(self, ds_id: str) -> bool:
        import app.db.db_service as db
        if not db.get_datasource(ds_id):
            return False
        db.delete_datasource(ds_id)
        # Pulizia scan_state su filesystem
        scan_path = self._scan_state_path(ds_id)
        if scan_path.exists():
            scan_path.unlink()
        logger.info(f"Data Source eliminato: {ds_id}")
        return True

    # ── Test connessione ──────────────────────────────────────────────────

    def test_connection(self, ds_id: str) -> DataSourceTestResult:
        # Bug #2 (2026-04-23): strict=True → se un campo auth cifrato non e'
        # decifrabile, CredentialUnavailableError propagato al router per
        # HTTP 422. Evita 200-OK fasullo con secret silent-None.
        config = self._get_full_config(ds_id, strict=True)
        if not config:
            return DataSourceTestResult(
                success=False,
                message=f"Data Source '{ds_id}' non trovato",
            )
        try:
            backend = create_backend(config)
            result = backend.test_connection()
            return DataSourceTestResult(
                success=result.get("ok", False),
                message=result.get("message", ""),
                details=result.get("details"),
            )
        # intent: boundary / outcome: fail-closed (success=False sanitizzato)
        # Non propagare str(e) nella response: gli SDK di provider (Office365,
        # paramiko, ftplib) possono includere URL autenticati, token OAuth,
        # credenziali parziali o path interni. Stacktrace preservato lato log.
        except Exception as e:
            logger.error(
                f"[GESTITO] Errore test connessione {ds_id}: {e}",
                exc_info=True,
            )
            return DataSourceTestResult(
                success=False,
                message="Impossibile testare la connessione: riprova piu' tardi",
            )

    # ── Operazioni file ───────────────────────────────────────────────────

    def list_files(self, ds_id, path="", pattern=None, include_hash=False, recursive=False):
        backend = self._get_backend(ds_id)
        with backend as ds:
            files = ds.list_files(path=path, pattern=pattern, include_hash=include_hash, recursive=recursive)
        return [FileInfoResponse(name=f.name, path=f.path, size=f.size,
                                  modified_date=f.modified_date, type=f.type, hash=f.hash)
                for f in files]

    def file_info(self, ds_id: str, path: str) -> FileInfoResponse:
        backend = self._get_backend(ds_id)
        with backend as ds:
            f = ds.file_info(path)
        return FileInfoResponse(name=f.name, path=f.path, size=f.size,
                                 modified_date=f.modified_date, type=f.type, hash=f.hash)

    def read_file(self, ds_id: str, path: str) -> bytes:
        backend = self._get_backend(ds_id)
        with backend as ds:
            return ds.read_file(path)

    def write_file(self, ds_id: str, path: str, content: bytes) -> None:
        backend = self._get_backend(ds_id)
        with backend as ds:
            ds.write_file(path, content)

    def delete_file(self, ds_id: str, path: str) -> None:
        backend = self._get_backend(ds_id)
        with backend as ds:
            ds.delete_file(path)

    def file_exists(self, ds_id: str, path: str) -> bool:
        backend = self._get_backend(ds_id)
        with backend as ds:
            return ds.file_exists(path)

    # ── Scan State (filesystem — dati runtime) ────────────────────────────

    def get_scan_state(self, ds_id: str) -> ScanState:
        path = self._scan_state_path(ds_id)
        if not path.exists():
            return ScanState()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return ScanState(**data)
        except Exception as e:
            # Prima: warning + fallback silenzioso → ogni chiamata successiva
            # ritrova il file corrotto e ritorna ScanState vuoto, ma il file
            # corrotto NON viene mai sostituito → "scan rotto fino al reset
            # manuale dell'admin".
            #
            # Ora: log a ERROR + rinomina il file corrotto in `.broken.<ts>`
            # così la prossima chiamata vede "no scan state" e riparte pulita.
            # L'admin trova il file corrotto sul disco se vuole investigare.
            ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            broken_path = path.with_name(f"{path.name}.broken.{ts}")
            try:
                path.rename(broken_path)
                logger.error(
                    f"[GESTITO] Scan state corrotto per {ds_id}: {e}. "
                    f"File rinominato in {broken_path.name}, scan riparte da zero.",
                    op="get_scan_state"
                )
            except Exception as rename_err:
                logger.error(
                    f"[GESTITO] Scan state corrotto per {ds_id}: {e}. "
                    f"Rename fallito ({rename_err}): manual cleanup necessario.",
                    op="get_scan_state"
                )
            return ScanState()

    def save_scan_state(self, ds_id: str, state: ScanState) -> None:
        path = self._scan_state_path(ds_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(state.model_dump(mode="json"), indent=2, ensure_ascii=False, default=str),
            encoding="utf-8"
        )

    def reset_scan_state(self, ds_id: str) -> None:
        self.save_scan_state(ds_id, ScanState())
        logger.info(f"Scan state azzerato per {ds_id}")

    # ── Rilevamento file nuovi ────────────────────────────────────────────

    def find_new_files(self, ds_id, path="", pattern=None, strategy="modified_date"):
        state = self.get_scan_state(ds_id)
        files = self.list_files(ds_id, path=path, pattern=pattern)
        processed_names = {p.name for p in state.processed}

        if strategy == "modified_date":
            new_files = files if state.last_check is None else [
                f for f in files if f.modified_date > state.last_check
            ]
        elif strategy == "inventory":
            snapshot_keys = {(s.name, s.size) for s in state.snapshot}
            new_files = [f for f in files if (f.name, f.size) not in snapshot_keys]
        elif strategy == "pattern":
            new_files = [f for f in files if f.name not in processed_names]
        else:
            logger.warning(f"Strategia sconosciuta {strategy!r}, uso 'modified_date'")
            new_files = files

        return [f for f in new_files if f.name not in processed_names]

    def mark_as_processed(self, ds_id: str, filename: str, pipeline_id: str) -> None:
        from app.core.datasources.models import ProcessedEntry, SnapshotEntry
        state = self.get_scan_state(ds_id)
        now = datetime.now(timezone.utc)
        state.last_check = now

        processed_names = {p.name for p in state.processed}
        snapshot_names = {s.name for s in state.snapshot}
        if filename not in processed_names:
            state.processed.append(ProcessedEntry(
                name=filename, processed_at=now, pipeline_id=pipeline_id,
            ))
        # intent: optional feature / outcome: fail-open (snapshot entry derivata, non critica)
        try:
            fi = self.file_info(ds_id, filename)
            if filename not in snapshot_names:
                state.snapshot.append(SnapshotEntry(
                    name=filename, size=fi.size, modified=fi.modified_date.isoformat(),
                ))
        except Exception as e:
            logger.debug(
                "mark_as_processed: snapshot entry skipped per %s/%s: %s: %s",
                ds_id, filename, type(e).__name__, e,
            )

        self.save_scan_state(ds_id, state)
        logger.info(f"File processato: {filename} (ds={ds_id}, pipeline={pipeline_id})")

    # ── Helpers privati ───────────────────────────────────────────────────

    def _get_full_config(self, ds_id: str, *, strict: bool = False) -> Optional[dict]:
        """Recupera la config completa (con credenziali) dal DB.

        I campi sensibili (password, private_key, ...) sono cifrati at-rest
        e vengono decifrati qui prima di essere restituiti al caller. Tutti
        gli usi interni (test_connection, get_backend, update_datasource)
        ricevono quindi i valori in chiaro pronti all'uso.

        Su config corrotto in DB, ritorna None (datasource trattata come non
        trovata) loggando warning, invece di sollevare 500.

        Se ``strict=True``, il decrypt di un campo cifrato che torna None
        (chiave crypto mancante/sbagliata o token Fernet corrotto) solleva
        ``CredentialUnavailableError``. Usato dal path test_connection
        (Bug #2, 2026-04-23) per non rispondere 200-OK con secret silent-None.
        """
        import app.db.db_service as db
        row = db.get_datasource(ds_id)
        if not row:
            return None
        config = row.get("config") or {}
        if isinstance(config, str):
            try:
                config = json.loads(config)
            except json.JSONDecodeError as e:
                logger.warning(
                    f"Config corrotta per datasource {ds_id}: {e}. "
                    f"Datasource trattata come non trovata."
                )
                return None
        if not isinstance(config, dict):
            logger.warning(
                f"Config con tipo inatteso per datasource {ds_id}: "
                f"{type(config).__name__}. Datasource trattata come non trovata."
            )
            return None
        # Decifra at-rest i campi sensibili (strict propagato)
        _decrypt_dict_fields(config, _SENSITIVE_FIELDS, strict=strict)
        # Inietta group_id e created_by dal record DB nel config dict
        config["group_id"] = row.get("group_id", "")
        config["created_by"] = row.get("created_by")
        return config

    def _get_backend(self, ds_id: str):
        config = self._get_full_config(ds_id)
        if not config:
            from app.core.datasources.interface import DataSourceError
            raise DataSourceError(f"Data Source '{ds_id}' non trovato")
        return create_backend(config)

    def _scan_state_path(self, ds_id: str) -> Path:
        return self._scan_dir / f"{ds_id}.json"

    def _generate_id(self, name: str) -> str:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        short_uuid = str(uuid.uuid4())[:8]
        safe = "".join(c for c in name if c.isalnum() or c in (" ", "_")).strip()
        safe = safe.replace(" ", "_")[:30]
        return f"ds_{safe}_{timestamp}_{short_uuid}"

    def _to_model(self, data: dict, redact: bool = True) -> DataSource:
        d = data.copy()
        if redact:
            for field in _SENSITIVE_FIELDS:
                if d.get(field):
                    d[field] = "***"
        for ts_field in ("created_at", "updated_at"):
            if isinstance(d.get(ts_field), str):
                try:
                    d[ts_field] = datetime.fromisoformat(d[ts_field])
                except ValueError:
                    d[ts_field] = datetime.now(timezone.utc)
        return DataSource(**{k: v for k, v in d.items() if k in DataSource.model_fields})


# Singleton
datasource_service = DataSourceService()
