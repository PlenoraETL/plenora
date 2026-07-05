"""
cloud_backends.py — astrazione cloud-agnostic per secrets + object storage.

Obiettivo: concentrare in UN punto il codice SDK-specifico (oggi boto3).
"""

from __future__ import annotations

import io
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, List, Protocol, runtime_checkable
from typing_extensions import TypedDict

from app import config as app_config

logger = logging.getLogger(__name__)


# Protocols (interface contract


class ObjectMeta(TypedDict):
    """Metadata di un oggetto su object storage. Shape comune a tutti i provider."""
    key: str
    last_modified: datetime
    size: int


@runtime_checkable
class SecretsBackend(Protocol):
    """Backend per recupero secrets (Parameter Store, Key Vault, Secret Manager)."""

    def get_secrets(self, prefix: str) -> dict:
        """Ritorna `{key: value}` per tutti i secret sotto `prefix`.

        Contract:
        - `key` e' il basename del path (prefix strippato, `/` -> `_` se sub-path)
        - `value` e' decrypted in chiaro
        - Prefix vuoto o senza match -> dict vuoto (NON solleva)
        - Errori di rete/permessi -> exception (chiamante decide policy)
        """
        ...


@runtime_checkable
class ObjectStorage(Protocol):
    """Backend object storage (S3, Azure Blob, GCS)."""

    def list_objects(self, prefix: str) -> List[ObjectMeta]:
        """Lista oggetti sotto `prefix`. Shape stabile attraverso provider."""
        ...

    def upload(self, local_path: Path, key: str) -> None:
        """Upload file locale come `key` nel bucket/container.
        Encryption at-rest usa il default del provider."""
        ...

    def upload_bytes(self, data: bytes, key: str) -> None:
        """Upload diretto di bytes in-memory come `key`. Zero FS staging.

        Contract cross-provider: l'impl interna gestisce chunking
        automatico se size lo richiede (S3 multipart, Azure Blob Block
        upload, GCS resumable). Il caller passa bytes di qualsiasi
        dimensione pratica; il limite reale e' la RAM del processo
        chiamante (se `data` entra in memoria, il provider la uploada).
        """
        ...

    def download(self, key: str, local_path: Path) -> None:
        """Download `key` su file locale. Fails se non esiste."""
        ...

    def get_object_stream(self, key: str) -> dict:
        """Apre uno stream per leggere `key` in chunks senza scaricare su disco.

        Ritorna dict con:
          - `body`: iterable di bytes chunks (streaming-friendly)
          - `size`: int, bytes totali
          - `content_type`: str (default "application/octet-stream")

        Uso tipico in FastAPI: StreamingResponse(resp["body"], ...).
        """
        ...

    def delete(self, key: str) -> None:
        """Cancella `key` dal bucket. Idempotente (no error se gia' assente)."""
        ...


# AWS credential error classification (helper boundary


# Messaggio operativo unico per tutti i casi di credenziali AWS scadute o
# non disponibili. Usato da results.blob_writer + admin_backups per uniformare
# l'errore percepito dall'utente. Mantenere conciso e azionabile
_AWS_CREDENTIALS_ERROR_HINT = (
    "AWS credentials expired/unavailable. "
    "Run 'aws sso login' or refresh AWS credentials, then retry."
)


def aws_credentials_error_message(exc: BaseException) -> "str | None":
    """Riconosce errori boto3/botocore di credenziali scadute o assenti
    e ritorna un messaggio operativo unico.

    Ritorna None se `exc` non e' un errore di credenziali AWS riconosciuto
    (il caller deve trattarlo come errore generico). Ritorna il messaggio
    da propagare all'utente se la causa e' SSO refresh expired, token
    retrieval fallito, credenziali assenti o un AccessDenied legato al
    refresh token.

    Riconosce:
      - botocore.exceptions.LoginRefreshRequired (SSO token scaduto)
      - botocore.exceptions.NoCredentialsError (~/.aws/credentials assente)
      - botocore.exceptions.TokenRetrievalError
      - botocore.exceptions.UnauthorizedSSOTokenError
      - botocore.exceptions.SSOTokenLoadError
      - botocore.exceptions.CredentialRetrievalError
      - botocore.exceptions.ClientError con codice AccessDeniedException
        e messaggio che menziona "refresh token" / "CreateOAuth2Token"
        (il provider SSO veicola la scadenza in questo modo)
    """
    try:
        from botocore.exceptions import (
            ClientError,
            CredentialRetrievalError,
            LoginRefreshRequired,
            NoCredentialsError,
            SSOTokenLoadError,
            TokenRetrievalError,
            UnauthorizedSSOTokenError,
        )
    except ImportError:
        # botocore non installato: il caller probabilmente non sta neanche
        # entrando in questo path; nessuna classificazione possibile
        return None

    credential_error_types = (
        LoginRefreshRequired,
        NoCredentialsError,
        TokenRetrievalError,
        UnauthorizedSSOTokenError,
        SSOTokenLoadError,
        CredentialRetrievalError,
    )
    if isinstance(exc, credential_error_types):
        return _AWS_CREDENTIALS_ERROR_HINT

    # AccessDeniedException con messaggio refresh-token-expired
    # botocore wrappa la risposta SSO in ClientError con Error.Code =
    # 'AccessDeniedException' e Message che cita 'refresh token has expired'
    # o 'CreateOAuth2Token'. Riconosciamo solo questa combinazione
    # un AccessDenied "vero" su una API S3 (es. policy bucket sbagliata
    # NON deve essere mappato come credenziali scadute
    if isinstance(exc, ClientError):
        err = getattr(exc, "response", {}) or {}
        info = err.get("Error", {}) or {}
        code = (info.get("Code") or "").strip()
        message = (info.get("Message") or "").lower()
        if code == "AccessDeniedException" and (
            "refresh token" in message or "createoauth2token" in message
        ):
            return _AWS_CREDENTIALS_ERROR_HINT

    return None


# AWS implementations


class AwsSsmSecretsBackend:
    """AWS Systems Manager Parameter Store.

    Legge SecureString via `get_parameters_by_path` paginato, con
    decifratura automatica (usa la KMS key del param — di default
    `alias/aws/ssm`). Credenziali risolte via catena standard boto3
    (IAM role EC2 in prod, AWS_PROFILE in dev).
    """

    def __init__(self, region: str):
        self.region = region

    def get_secrets(self, prefix: str) -> dict:
        import boto3
        client = boto3.client("ssm", region_name=self.region)
        out: dict = {}
        paginator = client.get_paginator("get_parameters_by_path")
        for page in paginator.paginate(Path=prefix, Recursive=True, WithDecryption=True):
            for param in page.get("Parameters", []):
                name = param["Name"]
                if not name.startswith(prefix):
                    continue
                key = name[len(prefix):].replace("/", "_")
                out[key] = param["Value"]
        return out


def _assert_upload_integrity(
    *,
    client: Any,
    bucket: str,
    key: str,
    expected_size: int,
) -> None:
    """Verifica post-upload che l'oggetto in bucket abbia esattamente la
    size attesa. Funzione pura riusabile da qualsiasi backend S3-compatible.

    In caso di mismatch tenta cleanup best-effort dell'oggetto corrotto
    (cosi' non resta orphan in bucket) e solleva `StorageIntegrityError`.
    Se anche il cleanup fallisce, l'oggetto resta in bucket ma viene
    comunque sollevata l'eccezione: il caller marca artifact_status=
    "failed" e il riconciliator notera' l'orphan al prossimo sweep.
    """
    try:
        head = client.head_object(Bucket=bucket, Key=key)
    except Exception as head_err:
        # head_object subito dopo upload deve sempre succeedere (eventual
        # consistency S3/MinIO e' read-after-write per nuovi oggetti).
        # Se fallisce, qualcosa di grave: cleanup + raise.
        try:
            client.delete_object(Bucket=bucket, Key=key)
        except Exception:
            pass
        raise StorageIntegrityError(
            f"head_object post-upload fallito per key={key!r}: "
            f"{type(head_err).__name__}: {head_err}"
        ) from head_err

    actual_size = head.get("ContentLength")
    if not isinstance(actual_size, int):
        logger.debug(
            "upload integrity size check skipped for key=%r: "
            "head_object ContentLength non numerico (%r)",
            key,
            actual_size,
        )
        return
    if actual_size != expected_size:
        try:
            client.delete_object(Bucket=bucket, Key=key)
        except Exception:
            pass
        raise StorageIntegrityError(
            f"Upload size mismatch per key={key!r}: "
            f"atteso {expected_size} bytes, head_object riporta "
            f"{actual_size} bytes. Oggetto eliminato per evitare orphan."
        )


class AwsS3ObjectStorage:
    """AWS S3.

    Upload con server-side encryption AES256 (SSE-S3, default bucket)
    e StorageClass STANDARD. Lifecycle/transition gestiti lato bucket,
    non da questo client.
    """

    def __init__(self, bucket: str, region: str):
        self.bucket = bucket
        self.region = region

    def _client(self):
        import boto3
        return boto3.client("s3", region_name=self.region)

    def _extra_args(self) -> dict:
        """ExtraArgs passati a boto3 upload/upload_fileobj.

        Override nei provider S3-compatible che non supportano SSE-S3
        out-of-the-box (es. MinIO vanilla). Base = SSE AES256 + STANDARD.
        """
        return {"ServerSideEncryption": "AES256", "StorageClass": "STANDARD"}

    def list_objects(self, prefix: str) -> List[ObjectMeta]:
        client = self._client()
        paginator = client.get_paginator("list_objects_v2")
        out: List[ObjectMeta] = []
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                out.append({
                    "key": obj["Key"],
                    "last_modified": obj["LastModified"],
                    "size": obj["Size"],
                })
        return out

    def upload(self, local_path: Path, key: str) -> None:
        """Upload file locale + integrity verification post-upload.

        Stesso pattern di `upload_bytes`: dopo `client.upload_file` (che
        usa S3Transfer internamente con multipart auto), `head_object`
        verifica `ContentLength == file size`. Su mismatch viene
        eliminato l'oggetto (best-effort) e sollevato `StorageIntegrityError`.
        Threat coperto: file truncation in upload, chunks corrotti su
        connessione instabile — boto3 non rileva senza checksum
        end-to-end esplicito.
        """
        client = self._client()
        expected_size = local_path.stat().st_size
        client.upload_file(
            str(local_path),
            self.bucket,
            key,
            ExtraArgs=self._extra_args(),
        )
        _assert_upload_integrity(
            client=client,
            bucket=self.bucket,
            key=key,
            expected_size=expected_size,
        )

    def upload_bytes(self, data: bytes, key: str) -> None:
        """Upload bytes via `upload_fileobj` + TransferConfig + integrity
        verification post-upload.

        Differenza con `put_object(Body=bytes)`:
          - put_object e' limitato a 5GB per PUT singolo
          - upload_fileobj usa S3Transfer internamente, fa multipart
            upload automatico sopra `multipart_threshold` (8MB default)
          - Nessun cap pratico: S3 multipart supporta fino a 5TB

        Integrity check: dopo l'upload, `head_object` verifica che
        `ContentLength` corrisponda a `len(data)`. Su mismatch viene
        eliminato l'oggetto (best-effort) e sollevato `StorageIntegrityError`.
        Threat coperto: silent data corruption che boto3 non rileva
        autonomamente (chunks troncati, streaming I/O instabile su MinIO,
        backend S3-compatible con checksum disabilitato).

        Equivalenti cross-provider:
          - Azure Blob: `BlobClient.upload_blob(data)` auto-chunking +
            `get_blob_properties` per size verification
          - GCS: `blob.upload_from_string(data)` resumable upload nativo +
            `blob.reload()` per size verification
        """
        from boto3.s3.transfer import TransferConfig
        client = self._client()
        config = TransferConfig(
            multipart_threshold=8 * 1024 * 1024,
            multipart_chunksize=8 * 1024 * 1024,
        )
        expected_size = len(data)
        client.upload_fileobj(
            io.BytesIO(data),
            self.bucket,
            key,
            ExtraArgs=self._extra_args(),
            Config=config,
        )
        _assert_upload_integrity(
            client=client,
            bucket=self.bucket,
            key=key,
            expected_size=expected_size,
        )

    def download(self, key: str, local_path: Path) -> None:
        client = self._client()
        client.download_file(self.bucket, key, str(local_path))

    def get_object_stream(self, key: str) -> dict:
        """GetObject e ritorna body streaming + metadata."""
        client = self._client()
        resp = client.get_object(Bucket=self.bucket, Key=key)
        body = resp["Body"]  # botocore StreamingBody — iterable di chunks
        return {
            "body": body.iter_chunks(chunk_size=64 * 1024),
            "size": resp.get("ContentLength", 0),
            "content_type": resp.get("ContentType", "application/octet-stream"),
        }

    def delete(self, key: str) -> None:
        """DELETE S3 object. Idempotente: S3 non errora se la key non esiste."""
        client = self._client()
        client.delete_object(Bucket=self.bucket, Key=key)

    def _readiness_client(self, timeout_seconds: int):
        """Boto3 client con timeout bounded e retry singolo, per /ready.

        runtime usa i default boto3 (60s connect, 60s read, 3 retry) che
        sommati possono superare i 6 minuti su un endpoint hung. Il
        readiness path usa invece un client one-shot bounded.

        `retries={"max_attempts": 1}` disabilita il retry exponenziale:
        /ready non vuole "ritenta su 5xx" — vuole sapere SUBITO se il
        provider non risponde dentro la finestra.
        """
        import boto3
        from botocore.config import Config

        return boto3.client(
            "s3",
            region_name=self.region,
            config=Config(
                connect_timeout=timeout_seconds,
                read_timeout=timeout_seconds,
                retries={"max_attempts": 1},
            ),
        )

    def readiness_probe(self, timeout_seconds: int) -> None:
        """Probe leggera del bucket con timeout bounded.

        Esegue `list_objects_v2` con `MaxKeys=1` su un prefisso dedicato
        normalmente vuoto. Bucket assente, credenziali scadute, network
        unreachable -> exception (il caller in `health.check_object_storage`
        la traduce in `(False, "<ExcType>: <msg>")`).

        Bound: `timeout_seconds` applicato sia al TCP handshake
        (`connect_timeout`) sia all'operazione (`read_timeout`); retry
        disabilitato (`max_attempts=1`). Worst case ~ 2 * timeout_seconds.
        """
        client = self._readiness_client(timeout_seconds)
        client.list_objects_v2(
            Bucket=self.bucket,
            Prefix=".plenora-readiness-probe/",
            MaxKeys=1,
        )


class MinioObjectStorage(AwsS3ObjectStorage):
    """MinIO via boto3 (S3-compatible endpoint).

    Riusa `AwsS3ObjectStorage` per tutti gli operatori S3 (list/upload/
    download/delete/stream). Override minimi:

    - `_client`: passa `endpoint_url` + credenziali esplicite +
      `addressing_style=path` (MinIO non supporta virtual-hosted style
      per default).
    - `_extra_args`: rimuove `ServerSideEncryption` dal default. MinIO
      vanilla non ha SSE configurato (richiede integrazione KES o
      auto-encryption abilitata lato bucket); forzarlo a livello client
      romperebbe l'upload su deploy minimi. Ops puo' abilitare SSE a
      livello bucket policy, indipendente dal client.

    """

    def __init__(
        self,
        bucket: str,
        endpoint_url: str,
        access_key: str,
        secret_key: str,
        region: str = "us-east-1",
    ):
        super().__init__(bucket=bucket, region=region)
        self.endpoint_url = endpoint_url
        self.access_key = access_key
        self.secret_key = secret_key

    def _client(self):
        import boto3
        from botocore.config import Config

        return boto3.client(
            "s3",
            region_name=self.region,
            endpoint_url=self.endpoint_url,
            aws_access_key_id=self.access_key,
            aws_secret_access_key=self.secret_key,
            config=Config(s3={"addressing_style": "path"}),
        )

    def _readiness_client(self, timeout_seconds: int):
        """Override del builder readiness per MinIO: stesso bound della
        parent (connect+read timeout, max_attempts=1) ma con
        endpoint_url + credenziali esplicite e addressing_style=path
        (MinIO non supporta virtual-hosted style)."""
        import boto3
        from botocore.config import Config

        return boto3.client(
            "s3",
            region_name=self.region,
            endpoint_url=self.endpoint_url,
            aws_access_key_id=self.access_key,
            aws_secret_access_key=self.secret_key,
            config=Config(
                s3={"addressing_style": "path"},
                connect_timeout=timeout_seconds,
                read_timeout=timeout_seconds,
                retries={"max_attempts": 1},
            ),
        )

    def _extra_args(self) -> dict:
        return {"StorageClass": "STANDARD"}


# Factory - dispatch via typed storage config


_TRUTHY = ("true", "1", "yes")


def _optional_str(value: Any) -> "str | None":
    if value is None:
        return None
    text = str(value).strip()
    return text or None


@dataclass(frozen=True)
class StorageBackendConfig:
    cloud_provider: str
    aws_region: str
    object_storage_enabled: bool
    object_storage_backend: "str | None"
    storage_policy: str
    object_storage_bucket: "str | None"
    plenora_artifact_bucket: "str | None"
    backup_bucket: "str | None"
    minio_endpoint_url: "str | None"
    minio_access_key: "str | None"
    minio_secret_key: "str | None"
    log_archive_enabled: bool
    log_archive_bucket: "str | None"
    log_archive_key_prefix: Any
    raw_result_s3_threshold_bytes: Any
    raw_file_source_s3_threshold_bytes: Any
    raw_result_db_hard_cap_bytes: Any
    raw_log_archive_batch_rows: Any
    raw_audit_retention_days: Any


def _settings():
    return app_config.settings


def _get_setting(name: str, default: Any = None) -> Any:
    settings_obj = _settings()
    getter = getattr(settings_obj, "get", None)
    if callable(getter):
        return getter(name, default)
    return getattr(settings_obj, name, default)


def _get_raw_setting(name: str, default: Any = None) -> Any:
    settings_obj = _settings()
    getter = getattr(settings_obj, "get_raw", None)
    if callable(getter):
        return getter(name, default)
    return getattr(settings_obj, name, default)


def _get_bool_setting(name: str, default: bool) -> bool:
    value = _get_setting(name, default)
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return str(value).strip().lower() in _TRUTHY


def _storage_config() -> StorageBackendConfig:
    return StorageBackendConfig(
        cloud_provider=(_optional_str(_get_setting("CLOUD_PROVIDER", "aws")) or "aws").lower(),
        aws_region=_optional_str(_get_setting("AWS_REGION", "eu-south-1")) or "eu-south-1",
        object_storage_enabled=_get_bool_setting("OBJECT_STORAGE_ENABLED", True),
        object_storage_backend=_optional_str(_get_setting("OBJECT_STORAGE_BACKEND")),
        storage_policy=(
            _optional_str(_get_setting("STORAGE_POLICY", "hybrid")) or "hybrid"
        ).lower(),
        object_storage_bucket=_optional_str(_get_setting("OBJECT_STORAGE_BUCKET")),
        plenora_artifact_bucket=_optional_str(_get_setting("PLENORA_ARTIFACT_BUCKET")),
        backup_bucket=_optional_str(_get_setting("BACKUP_BUCKET")),
        minio_endpoint_url=_optional_str(_get_setting("MINIO_ENDPOINT_URL")),
        minio_access_key=_optional_str(_get_setting("MINIO_ACCESS_KEY")),
        minio_secret_key=_optional_str(_get_setting("MINIO_SECRET_KEY")),
        log_archive_enabled=_get_bool_setting("LOG_ARCHIVE_ENABLED", False),
        log_archive_bucket=_optional_str(_get_setting("LOG_ARCHIVE_BUCKET")),
        # os.getenv("LOG_ARCHIVE_KEY_PREFIX") is intentionally represented
        # by _get_raw_setting to keep direct env reads centralized.
        log_archive_key_prefix=_get_raw_setting("LOG_ARCHIVE_KEY_PREFIX"),
        raw_result_s3_threshold_bytes=_get_raw_setting("RESULT_S3_THRESHOLD_BYTES"),
        raw_file_source_s3_threshold_bytes=_get_raw_setting(
            "FILE_SOURCE_S3_THRESHOLD_BYTES"
        ),
        raw_result_db_hard_cap_bytes=_get_raw_setting("RESULT_DB_HARD_CAP_BYTES"),
        raw_log_archive_batch_rows=_get_raw_setting("LOG_ARCHIVE_BATCH_ROWS"),
        # os.getenv("AUDIT_RETENTION_DAYS") is intentionally represented
        # by _get_raw_setting to keep direct env reads centralized.
        raw_audit_retention_days=_get_raw_setting("AUDIT_RETENTION_DAYS"),
    )


def _get_provider() -> str:
    return _storage_config().cloud_provider


def get_secrets_backend() -> SecretsBackend:
    """Ritorna il SecretsBackend appropriato per il provider configurato.

    Config via env:
      CLOUD_PROVIDER=aws (default)
        AWS_REGION (default eu-south-1)

      CLOUD_PROVIDER=azure (future)
        AZURE_KEY_VAULT_URL
        AZURE_TENANT_ID / credenziali Managed Identity

      CLOUD_PROVIDER=gcp (future)
        GCP_PROJECT
        credenziali Service Account
    """
    provider = _get_provider()
    if provider == "aws":
        return AwsSsmSecretsBackend(
            region=_storage_config().aws_region,
        )
    if provider == "minio":
        raise ValueError(
            "CLOUD_PROVIDER=minio non supporta SecretsBackend. MinIO e' "
            "solo object storage; secrets richiedono CLOUD_PROVIDER=aws. "
            "Decoupling secrets/object-storage provider e' follow-up A1."
        )
    # Future extension point: Azure Key Vault / GCP Secret Manager backends
    # when those providers are supported by deployment profiles.
    raise ValueError(f"CLOUD_PROVIDER non supportato: {provider!r}")


class ObjectStorageDisabledError(RuntimeError):
    """Raised quando un caller chiede object storage ma la capability
    globale e' disabilitata (`OBJECT_STORAGE_ENABLED=false` o
    `OBJECT_STORAGE_BACKEND=none`).

    I caller devono trattarlo come "feature non disponibile in questo
    deployment" e produrre una risposta operativa (503 / messaggio
    user-facing chiaro), non come bug applicativo.
    """


class StorageIntegrityError(RuntimeError):
    """Raised quando un upload completa senza eccezione boto3 ma la
    verifica post-upload rileva una corruzione (size mismatch, oggetto
    mancante, ETag inatteso).

    Threat model coperto: `upload_fileobj` di boto3 solleva su errori di
    rete/HTTP, ma NON su silent data corruption (chunks corrotti accettati
    da S3-compatibles con checksum disabilitato, byte mancanti per
    streaming I/O instabile, eccetera). Senza un check end-to-end
    l'artifact veniva marcato `artifact_status="complete"` anche su upload
    corrotto, e i downstream readers leggevano dati malformati.

    Il caller deve trattarlo come failure di upload: lo storage prova
    a fare cleanup dell'oggetto orphan ma la cancellazione e' best-effort.
    """


def _read_object_storage_capability() -> dict:
    """Lettura settings capability object storage.

    Fallback a CLOUD_PROVIDER quando `OBJECT_STORAGE_BACKEND` non e'
    settato.
    """
    cfg = _storage_config()
    enabled = cfg.object_storage_enabled
    backend = cfg.object_storage_backend
    if backend is None:
        provider = cfg.cloud_provider
        if provider == "aws":
            backend = "s3"
        elif provider == "minio":
            backend = "minio"
        elif provider == "none":
            backend = "none"
        else:
            backend = provider  # unknown -> let _build_object_storage raise
    backend = str(backend).strip().lower()

    return {"enabled": enabled, "backend": backend}


def _effective_object_storage_backend(config: StorageBackendConfig | None = None) -> str:
    cfg = config or _storage_config()
    backend = cfg.object_storage_backend
    if backend is None:
        provider = cfg.cloud_provider
        if provider == "aws":
            backend = "s3"
        elif provider == "minio":
            backend = "minio"
        elif provider == "none":
            backend = "none"
        else:
            backend = provider
    backend = str(backend).strip().lower()
    return "aws" if backend == "s3" else backend


def object_storage_enabled() -> bool:
    """True se la capability runtime object storage e' attiva.

    Ritorna False se `OBJECT_STORAGE_ENABLED=false` o se il backend
    risolto e' "none". I caller con fallback locale (IMDF list,
    file_sources delete cleanup) usano questo helper come gate
    invece di catturare eccezioni.
    """
    cap = _read_object_storage_capability()
    return cap["enabled"] and cap["backend"] not in ("none", "")


def _resolve_runtime_bucket(config: "StorageBackendConfig | None" = None) -> "str | None":
    """Bucket per runtime artifacts.

    Priorita':
      1. OBJECT_STORAGE_BUCKET (canonical, distinto da BACKUP_BUCKET)
      2. PLENORA_ARTIFACT_BUCKET
      3. BACKUP_BUCKET (retro-compat con WARNING: deployment che usavano lo
         stesso bucket per backup e runtime artifact)

    None se nessuno e' settato (factory solleva).
    """
    cfg = config or _storage_config()
    bucket = cfg.object_storage_bucket
    if bucket:
        return bucket
    bucket = cfg.plenora_artifact_bucket
    if bucket:
        return bucket
    if cfg.backup_bucket:
        # Fail-loud in ambiente hardened (prod/staging): mischiare runtime artifacts
        # e backup amministrativi viola IAM boundary + lifecycle policy (Backend
        # master Part II #5). Retro-compat solo in dev/test, con WARNING.
        from app.environment import is_hardened_environment

        if is_hardened_environment():
            raise StorageConfigError(
                "OBJECT_STORAGE_BUCKET non configurato in ambiente hardened: "
                "rifiuto il fallback su BACKUP_BUCKET per non mischiare runtime "
                "artifacts e backup amministrativi. Configurare OBJECT_STORAGE_BUCKET."
            )
        logger.warning(
            "OBJECT_STORAGE_BUCKET non configurato: fallback su "
            "BACKUP_BUCKET=%s per retro-compatibilita' (solo non-prod). Configurare "
            "OBJECT_STORAGE_BUCKET (runtime artifacts) distinto da "
            "BACKUP_BUCKET (backup amministrativi).",
            cfg.backup_bucket,
        )
        return cfg.backup_bucket
    return None


def _resolve_backup_bucket(config: "StorageBackendConfig | None" = None) -> "str | None":
    """Bucket dedicato backup amministrativi.

    Solo `BACKUP_BUCKET`. Mai fallback su OBJECT_STORAGE_BUCKET: i due
    bucket sono semanticamente distinti per IAM boundary e lifecycle policy.
    """
    cfg = config or _storage_config()
    return cfg.backup_bucket


def _build_object_storage(bucket: str, *, region_default: str) -> ObjectStorage:
    """Costruisce l'impl ObjectStorage per il backend configurato.

    Usa il backend object-storage effettivo: `OBJECT_STORAGE_BACKEND`
    se settato, altrimenti fallback legacy a `CLOUD_PROVIDER`.
    Caller assicura `object_storage_enabled=True` prima di chiamare.
    """
    cfg = _storage_config()
    provider = _effective_object_storage_backend(cfg)
    if provider == "aws":
        return AwsS3ObjectStorage(
            bucket=bucket,
            region=cfg.aws_region or region_default,
        )
    if provider == "minio":
        endpoint_url = cfg.minio_endpoint_url
        access_key = cfg.minio_access_key
        secret_key = cfg.minio_secret_key
        if not endpoint_url:
            raise ValueError("OBJECT_STORAGE_BACKEND=minio richiede MINIO_ENDPOINT_URL.")
        if not access_key:
            raise ValueError("OBJECT_STORAGE_BACKEND=minio richiede MINIO_ACCESS_KEY.")
        if not secret_key:
            raise ValueError("OBJECT_STORAGE_BACKEND=minio richiede MINIO_SECRET_KEY.")
        return MinioObjectStorage(
            bucket=bucket,
            endpoint_url=endpoint_url,
            access_key=access_key,
            secret_key=secret_key,
            region=cfg.aws_region or "us-east-1",
        )
    raise ValueError(f"OBJECT_STORAGE_BACKEND non supportato: {provider!r}")


def get_runtime_object_storage() -> ObjectStorage:
    """ObjectStorage per runtime artifacts (result, file_source,
    eseguibili, IMDF, intermediate full_artifact).

    Solleva `ObjectStorageDisabledError` se la capability e' off.

    Bucket:
      - OBJECT_STORAGE_BUCKET (canonical)
      - PLENORA_ARTIFACT_BUCKET
      - BACKUP_BUCKET (fallback con warning)
    """
    if not object_storage_enabled():
        raise ObjectStorageDisabledError(
            "Object storage disabilitato in questo deployment "
            "(OBJECT_STORAGE_ENABLED=false o OBJECT_STORAGE_BACKEND=none). "
            "I moduli che richiedono object storage runtime "
            "(result, file_source, eseguibili, IMDF) non sono operativi. "
            "Per abilitare: configura OBJECT_STORAGE_ENABLED=true + "
            "OBJECT_STORAGE_BUCKET + provider credentials."
        )
    bucket = _resolve_runtime_bucket()
    if not bucket:
        raise ValueError(
            "OBJECT_STORAGE_BUCKET non configurato (e nessun fallback "
            "PLENORA_ARTIFACT_BUCKET / BACKUP_BUCKET disponibile). "
            "Configurare OBJECT_STORAGE_BUCKET in env."
        )
    return _build_object_storage(bucket, region_default="eu-south-1")


def get_backup_object_storage() -> ObjectStorage:
    """ObjectStorage per backup amministrativi (admin_backups, backup_db.py).

    Bucket: solo `BACKUP_BUCKET`. Mai fallback su
    OBJECT_STORAGE_BUCKET: IAM boundary e lifecycle distinti.

    Solleva `ObjectStorageDisabledError` se la capability globale e' off:
    senza object storage non c'e' admin_backups list S3 sensata
    (admin_backups deve ritornare "feature disabilitata" lato router).
    """
    if not object_storage_enabled():
        raise ObjectStorageDisabledError(
            "Object storage disabilitato in questo deployment "
            "(OBJECT_STORAGE_ENABLED=false o OBJECT_STORAGE_BACKEND=none). "
            "Admin backups S3 list non disponibile. "
            "Per abilitare: configura OBJECT_STORAGE_ENABLED=true + "
            "BACKUP_BUCKET + provider credentials."
        )
    bucket = _resolve_backup_bucket()
    if not bucket:
        raise ValueError(
            "BACKUP_BUCKET non configurato."
        )
    return _build_object_storage(bucket, region_default="eu-south-1")


def get_log_archive_storage() -> ObjectStorage:
    """Ritorna l'ObjectStorage dedicato all'archive log.

    Gemella delle factory object-storage runtime/backup ma su bucket distinto: IAM boundary
    separato per archive log vs backup DB e' un invariante di design,
    non un'opzione. Per questo la factory e' dedicata e non parametrica
    su una factory condivisa.

    La coerenza config (ENABLED + provider reale + `LOG_ARCHIVE_BUCKET`
    distinto da `BACKUP_BUCKET` + prefix + batch_rows + audit retention)
    e' gia' enforcata al boot da `validate_storage_config` piu' in basso
    in questo file: questa factory presume la precondizione verificata.
    Il `ValueError` difensivo su bucket unset e' un guard ridondante per
    chiamate out-of-contract (es. `ENABLED=false` che tocca il hook).

    Config via env:
      CLOUD_PROVIDER=aws (default)
        LOG_ARCHIVE_BUCKET — required (nessun fallback: per design il bucket
                              archive e' dedicato)
        AWS_REGION — default eu-south-1
    """
    cfg = _storage_config()
    provider = cfg.cloud_provider
    if provider == "aws":
        bucket = cfg.log_archive_bucket
        if not bucket:
            raise ValueError(
                "LOG_ARCHIVE_BUCKET non configurato (richiesto quando "
                "LOG_ARCHIVE_ENABLED=True)."
            )
        return AwsS3ObjectStorage(
            bucket=bucket,
            region=cfg.aws_region or "eu-south-1",
        )
    # Estendere qui per Azure / GCP (stesso pattern delle factory storage)
    raise ValueError(f"CLOUD_PROVIDER non supportato: {provider!r}")


# Storage policy — boot-time validation (A1


VALID_STORAGE_POLICIES = ("db", "object_storage", "hybrid")
# Provider effettivamente implementati in cloud_backends
IMPLEMENTED_PROVIDERS = ("aws", "minio", "none")
# Provider noti all'enum: validate_storage_config li accetta solo se
# policy=db (= bucket non consultato), altrimenti fail-fast perche' la
# factory non li costruisce.
KNOWN_PROVIDERS = ("aws", "minio", "azure", "gcp", "none")


class StorageConfigError(ValueError):
    """Config di storage incoerente rilevata al boot.

    Sollevata da :func:`validate_storage_config` quando la combinazione
    di STORAGE_POLICY, CLOUD_PROVIDER, BACKUP_BUCKET e soglie non e'
    consistente. Fail-fast all'avvio e' preferito a lazy failure runtime
    (di solito in un task Celery oltre soglia).
    """


def validate_storage_config() -> None:
    """Fail-fast boot check per la policy di storage (A1).

    Verifica la sola **coerenza di config**, mai la raggiungibilita'
    del provider (nessun probe di rete). Chiamata da `app.main` subito
    dopo `settings.validate_startup`.

    Controlli:
      1. STORAGE_POLICY in {db, object_storage, hybrid}
      2. CLOUD_PROVIDER in enum noto + implementato in factory
      3. Se policy != db: BACKUP_BUCKET settato
         e CLOUD_PROVIDER != none
      4. Se policy == db con bucket+provider cloud: warning non-blocking
         (probabile cut-paste)
      5. Soglie `RESULT_S3_THRESHOLD_BYTES` / `FILE_SOURCE_S3_THRESHOLD_BYTES`
         (solo in hybrid) sono intere > 0 se settate
      6. `RESULT_DB_HARD_CAP_BYTES` (in db e hybrid) intero > 0 se settato

    Raises:
        StorageConfigError: config inconsistente — il processo deve terminare.
    """
    def _fail(msg: str) -> None:
        logger.critical("[BOOT] %s", msg)
        raise StorageConfigError(msg)

    def _check_positive_int(name: str) -> None:
        # `os.getenv` diretto per design: questo helper e' un boot-time
        # fail-fast validator
        # che deve distinguere "user ha settato l'env" da "user non l'ha
        # settato". Settings.get applicherebbe `_converti_tipo_env` che
        # silently swallowa valori malformati e ritorna il default
        # rompendo il fail-fast su typo (es. `LOG_ARCHIVE_BATCH_ROWS=abc`
        # passerebbe il boot, e in produzione l'archive userebbe 10000
        # invece di crashare loud al boot). Il runtime path consuma il
        # valore via `settings.LOG_ARCHIVE_BATCH_ROWS` property.
        # raw = os.getenv(name)
        raw = raw_values.get(name)
        if raw is None:
            return
        try:
            value = int(raw)
        except (TypeError, ValueError):
            _fail(f"{name}={raw!r} non e' un intero valido.")
            return  # unreachable: _fail solleva
        if value <= 0:
            _fail(f"{name}={value} deve essere > 0.")

    cfg = _storage_config()
    raw_values = {
        "RESULT_S3_THRESHOLD_BYTES": cfg.raw_result_s3_threshold_bytes,
        "FILE_SOURCE_S3_THRESHOLD_BYTES": cfg.raw_file_source_s3_threshold_bytes,
        "RESULT_DB_HARD_CAP_BYTES": cfg.raw_result_db_hard_cap_bytes,
        "LOG_ARCHIVE_BATCH_ROWS": cfg.raw_log_archive_batch_rows,
        "AUDIT_RETENTION_DAYS": cfg.raw_audit_retention_days,
    }

    policy = cfg.storage_policy
    if policy not in VALID_STORAGE_POLICIES:
        _fail(
            f"STORAGE_POLICY={policy!r} non valido. "
            f"Valori ammessi: {', '.join(VALID_STORAGE_POLICIES)}."
        )

    provider = cfg.cloud_provider
    object_provider = _effective_object_storage_backend(cfg)
    if provider not in KNOWN_PROVIDERS:
        _fail(
            f"CLOUD_PROVIDER={provider!r} sconosciuto. "
            f"Valori ammessi: {', '.join(KNOWN_PROVIDERS)}."
        )
    if provider not in IMPLEMENTED_PROVIDERS:
        _fail(
            f"CLOUD_PROVIDER={provider!r} non ancora implementato in "
            "cloud_backends. Provider disponibili oggi: "
            f"{', '.join(IMPLEMENTED_PROVIDERS)}."
        )

    bucket = _resolve_backup_bucket(cfg)

    if policy == "db":
        # NOTE: non-blocking: bucket + provider cloud settati ma policy=db
        # probabilmente e' cut-paste da altro env. Policy rispettata
        if bucket and provider != "none":
            logger.warning(
                f"[BOOT] STORAGE_POLICY=db ma CLOUD_PROVIDER={provider!r} e "
                "bucket configurati: policy rispettata (object storage "
                "ignorato), ma la combinazione suggerisce cut-paste — "
                "verifica l'intento."
            )
    else:
        # object_storage | hybrid: richiedono provider reale + bucket
        if object_provider == "none":
            _fail(
                f"STORAGE_POLICY={policy} richiede un object storage provider, "
                "ma OBJECT_STORAGE_BACKEND=none. Imposta OBJECT_STORAGE_BACKEND=s3/minio "
                "o cambia policy a db."
            )
        if not bucket:
            _fail(
                f"STORAGE_POLICY={policy} richiede un bucket object storage, "
                "ma BACKUP_BUCKET non e' configurato."
            )
        # Policy `object_storage` richiede bucket runtime esplicito
        # niente fallback silenzioso su BACKUP_BUCKET (semanticamente
        # diverso, IAM/lifecycle distinti). Con `hybrid` il fallback su
        # BACKUP_BUCKET resta consentito per deployment che non separano
        # ancora runtime artifacts e backup amministrativi.
        if policy == "object_storage":
            runtime_bucket = cfg.object_storage_bucket or cfg.plenora_artifact_bucket
            if not runtime_bucket:
                _fail(
                    "STORAGE_POLICY=object_storage richiede "
                    "OBJECT_STORAGE_BUCKET (o alias PLENORA_ARTIFACT_BUCKET) "
                    "configurato esplicitamente. Niente fallback su "
                    "BACKUP_BUCKET in questa policy: i due bucket sono "
                    "semanticamente distinti (runtime artifacts vs backup "
                    "amministrativi). Imposta OBJECT_STORAGE_BUCKET o "
                    "cambia policy a hybrid."
                )
            # OBJECT_STORAGE_ENABLED deve essere true: senza la capability
            # i writer dei nuovi result fallirebbero comunque al primo upload
            # meglio fail-fast al boot con messaggio operativo
            if not cfg.object_storage_enabled:
                _fail(
                    "STORAGE_POLICY=object_storage richiede "
                    "OBJECT_STORAGE_ENABLED=true. Con la capability "
                    "disabilitata il path runtime (result/file_source/eseguibili/"
                    "IMDF/intermediate) non e' operativo. Abilita la "
                    "capability o cambia policy a db/hybrid."
                )
        # A1-B2: MinIO richiede credenziali esplicite + endpoint (no IAM chain
        if object_provider == "minio":
            for name in (
                "MINIO_ENDPOINT_URL",
                "MINIO_ACCESS_KEY",
                "MINIO_SECRET_KEY",
            ):
                if not getattr(cfg, name.lower()):
                    _fail(
                        f"OBJECT_STORAGE_BACKEND=minio richiede {name} configurato."
                    )

    # Soglie dispatch: solo hybrid le consulta effettivamente
    if policy == "hybrid":
        _check_positive_int("RESULT_S3_THRESHOLD_BYTES")
        _check_positive_int("FILE_SOURCE_S3_THRESHOLD_BYTES")

    # Hard cap DB: attivo in db (primario) e in hybrid (guard ramo DB
    if policy in ("db", "hybrid"):
        _check_positive_int("RESULT_DB_HARD_CAP_BYTES")

    if cfg.log_archive_enabled:
        # 1. STORAGE_POLICY deve essere object_storage o hybrid
        if policy == "db":
            _fail(
                "LOG_ARCHIVE_ENABLED=true incompatibile con STORAGE_POLICY=db: "
                "archive non ha target object storage. Imposta "
                "STORAGE_POLICY=object_storage o hybrid, o disabilita "
                "LOG_ARCHIVE_ENABLED."
            )
        # 2. CLOUD_PROVIDER reale richiesto
        if object_provider == "none":
            _fail(
                "LOG_ARCHIVE_ENABLED=true richiede un object storage "
                "provider, ma OBJECT_STORAGE_BACKEND=none."
            )
        # 3. LOG_ARCHIVE_BUCKET required (intenzionalmente fuori da
        # Settings.DEFAULTS, required-if-enabled per design A3
        log_archive_bucket = cfg.log_archive_bucket
        if not log_archive_bucket:
            _fail(
                "LOG_ARCHIVE_ENABLED=true richiede LOG_ARCHIVE_BUCKET "
                "configurato (bucket dedicato archive, distinto da "
                "BACKUP_BUCKET)."
            )
        # 4. Bucket archive distinto da BACKUP_BUCKET.
        # IAM boundary dedicato. A questo punto
        # `bucket` e' guaranteed truthy: regola 1 ha escluso policy=db
        # e il ramo A1 else-branch (riga 395) fallisce gia' se bucket
        # e' unset con policy != db
        if log_archive_bucket == bucket:
            _fail(
                f"LOG_ARCHIVE_BUCKET={log_archive_bucket!r} coincide con "
                "BACKUP_BUCKET: IAM boundary richiesto "
                "distinto per archive log vs backup DB."
            )
        # 5. Formato key prefix (se settata): vuoto o endswith "/"
        # Coerente con log_archive._build_object_key:206-209
        # `os.getenv` diretto per boot-time fail-fast (vedi
        # `_check_positive_int`). Runtime via `settings.LOG_ARCHIVE_KEY_PREFIX`.
        log_archive_prefix = cfg.log_archive_key_prefix
        if log_archive_prefix and not log_archive_prefix.endswith("/"):
            _fail(
                f"LOG_ARCHIVE_KEY_PREFIX={log_archive_prefix!r} non valido: "
                "deve essere vuoto oppure terminare con '/'."
            )
        # 6. > 0 (riusa helper esistente
        _check_positive_int("LOG_ARCHIVE_BATCH_ROWS")
        # 7. AUDIT_RETENTION_DAYS >= 0 (inline parse: _check_positive_int
        # rigetta 0, ma qui 0 = retention disabilitata e' legittimo
        # simmetrico a SCHEDULE_LOGS_RETENTION_DAYS
        # `os.getenv` diretto per boot-time fail-fast (vedi
        # `_check_positive_int`). Runtime via `settings.AUDIT_RETENTION_DAYS`.
        audit_retention_raw = cfg.raw_audit_retention_days
        if audit_retention_raw is not None:
            try:
                audit_retention_value = int(audit_retention_raw)
            except (TypeError, ValueError):
                _fail(
                    f"AUDIT_RETENTION_DAYS={audit_retention_raw!r} non e' "
                    "un intero valido."
                )
            else:
                if audit_retention_value < 0:
                    _fail(
                        f"AUDIT_RETENTION_DAYS={audit_retention_value} deve "
                        "essere >= 0 (0 = retention disabilitata)."
                    )
