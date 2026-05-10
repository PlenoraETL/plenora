"""
cloud_backends.py — astrazione cloud-agnostic per secrets + object storage.

Obiettivo: concentrare in UN punto il codice SDK-specifico (oggi boto3).
"""

from __future__ import annotations

import io
import warnings as _warnings

import functools
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import List, Protocol, TypedDict, runtime_checkable

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
# non disponibili. Usato da result_store + admin_backups per uniformare
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
        client = self._client()
        client.upload_file(
            str(local_path),
            self.bucket,
            key,
            ExtraArgs=self._extra_args(),
        )

    def upload_bytes(self, data: bytes, key: str) -> None:
        """Upload bytes via `upload_fileobj` + TransferConfig.

        Differenza con `put_object(Body=bytes)` (che avevo prima):
          - put_object e' limitato a 5GB per PUT singolo
          - upload_fileobj usa S3Transfer internamente, fa multipart
            upload automatico sopra `multipart_threshold` (8MB default)
          - Nessun cap pratico: S3 multipart supporta fino a 5TB

        Equivalenti cross-provider:
          - Azure Blob: `BlobClient.upload_blob(data)` auto-chunking
          - GCS: `blob.upload_from_string(data)` resumable upload nativo
        """
        from boto3.s3.transfer import TransferConfig
        client = self._client()
        config = TransferConfig(
            multipart_threshold=8 * 1024 * 1024,
            multipart_chunksize=8 * 1024 * 1024,
        )
        client.upload_fileobj(
            io.BytesIO(data),
            self.bucket,
            key,
            ExtraArgs=self._extra_args(),
            Config=config,
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

    Ratifica: ADR-0015 (A1-B2, 2026-04-22).
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


# Factory — dispatch via CLOUD_PROVIDER env


def _get_provider() -> str:
    return os.getenv("CLOUD_PROVIDER", "aws").lower()


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
            region=os.getenv("AWS_REGION", "eu-south-1"),
        )
    if provider == "minio":
        raise ValueError(
            "CLOUD_PROVIDER=minio non supporta SecretsBackend. MinIO e' "
            "solo object storage; secrets richiedono CLOUD_PROVIDER=aws. "
            "Decoupling secrets/object-storage provider e' follow-up A1."
        )
    # TODO: add Azure Key Vault / GCP Secret Manager backends when those
    # providers are supported by deployment profiles
    raise ValueError(f"CLOUD_PROVIDER non supportato: {provider!r}")


class ObjectStorageDisabledError(RuntimeError):
    """Raised quando un caller chiede object storage ma la capability
    globale e' disabilitata (`OBJECT_STORAGE_ENABLED=false` o
    `OBJECT_STORAGE_BACKEND=none`).

    I caller devono trattarlo come "feature non disponibile in questo
    deployment" e produrre una risposta operativa (503 / messaggio
    user-facing chiaro), non come bug applicativo.
    """


def _read_object_storage_capability() -> dict:
    """Lettura settings capability object storage.

    Usa `os.getenv` direttamente (con default coerenti con
    `app.config.Settings.DEFAULTS`) per non dipendere dal singleton
    Settings: alcuni test patchano `app.config.settings` con stub
    minimali che non implementano `.get`. La capability deve restare
    valutabile in quei contesti senza false positive.

    Fallback a CLOUD_PROVIDER quando `OBJECT_STORAGE_BACKEND` non e'
    settato.
    """
    enabled_raw = os.getenv("OBJECT_STORAGE_ENABLED")
    if enabled_raw is None:
        enabled = True
    else:
        enabled = str(enabled_raw).strip().lower() in ("true", "1", "yes")

    backend = os.getenv("OBJECT_STORAGE_BACKEND") or None
    if backend is None:
        provider = _get_provider()
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


def object_storage_enabled() -> bool:
    """True se la capability runtime object storage e' attiva.

    Ritorna False se `OBJECT_STORAGE_ENABLED=false` o se il backend
    risolto e' "none". I caller con fallback locale (IMDF list,
    file_sources delete cleanup) usano questo helper come gate
    invece di catturare eccezioni.
    """
    cap = _read_object_storage_capability()
    return cap["enabled"] and cap["backend"] not in ("none", "")


def _resolve_runtime_bucket() -> "str | None":
    """Bucket per runtime artifacts.

    Priorita':
      1. OBJECT_STORAGE_BUCKET (canonical, distinto da BACKUP_BUCKET)
      2. PLENORA_ARTIFACT_BUCKET
      3. BACKUP_BUCKET / BACKUP_S3_BUCKET (retro-compat con WARNING:
         deployment legacy che usavano lo stesso bucket per backup e
         runtime artifact)

    None se nessuno e' settato (factory solleva).
    """
    bucket = os.getenv("OBJECT_STORAGE_BUCKET")
    if bucket:
        return str(bucket)
    bucket = os.getenv("PLENORA_ARTIFACT_BUCKET")
    if bucket:
        return str(bucket)
    legacy = os.getenv("BACKUP_BUCKET") or os.getenv("BACKUP_S3_BUCKET")
    if legacy:
        logger.warning(
            "OBJECT_STORAGE_BUCKET non configurato: fallback su "
            "BACKUP_BUCKET=%s per retro-compatibilita'. Configurare "
            "OBJECT_STORAGE_BUCKET (runtime artifacts) distinto da "
            "BACKUP_BUCKET (backup amministrativi).",
            legacy,
        )
        return legacy
    return None


def _resolve_backup_bucket() -> "str | None":
    """Bucket dedicato backup amministrativi.

    Solo `BACKUP_BUCKET` / `BACKUP_S3_BUCKET` (legacy). Mai fallback
    su OBJECT_STORAGE_BUCKET: i due bucket sono semanticamente distinti
    per IAM boundary e lifecycle policy.
    """
    return os.getenv("BACKUP_BUCKET") or os.getenv("BACKUP_S3_BUCKET")


def _build_object_storage(bucket: str, *, region_default: str) -> ObjectStorage:
    """Costruisce l'impl ObjectStorage per il backend configurato.

    Riusa la classificazione `_get_provider` esistente per backward
    compat: `aws -> AwsS3ObjectStorage`, `minio -> MinioObjectStorage`.
    Caller assicura `object_storage_enabled=True` prima di chiamare.
    """
    provider = _get_provider()
    if provider == "aws":
        return AwsS3ObjectStorage(
            bucket=bucket,
            region=os.getenv("AWS_REGION", region_default),
        )
    if provider == "minio":
        endpoint_url = os.getenv("MINIO_ENDPOINT_URL")
        access_key = os.getenv("MINIO_ACCESS_KEY")
        secret_key = os.getenv("MINIO_SECRET_KEY")
        if not endpoint_url:
            raise ValueError("CLOUD_PROVIDER=minio richiede MINIO_ENDPOINT_URL.")
        if not access_key:
            raise ValueError("CLOUD_PROVIDER=minio richiede MINIO_ACCESS_KEY.")
        if not secret_key:
            raise ValueError("CLOUD_PROVIDER=minio richiede MINIO_SECRET_KEY.")
        return MinioObjectStorage(
            bucket=bucket,
            endpoint_url=endpoint_url,
            access_key=access_key,
            secret_key=secret_key,
            region=os.getenv("AWS_REGION", "us-east-1"),
        )
    raise ValueError(f"CLOUD_PROVIDER non supportato: {provider!r}")


def get_runtime_object_storage() -> ObjectStorage:
    """ObjectStorage per runtime artifacts (result, file_source,
    eseguibili, IMDF, intermediate full_artifact).

    Solleva `ObjectStorageDisabledError` se la capability e' off.

    Bucket:
      - OBJECT_STORAGE_BUCKET (canonical)
      - PLENORA_ARTIFACT_BUCKET
      - BACKUP_BUCKET (legacy fallback con warning)
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

    Bucket: solo `BACKUP_BUCKET` / `BACKUP_S3_BUCKET`. Mai fallback su
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
            "BACKUP_BUCKET (o BACKUP_S3_BUCKET legacy) non configurato."
        )
    return _build_object_storage(bucket, region_default="eu-south-1")


@functools.lru_cache(maxsize=1)
def _warn_get_object_storage_deprecated_once() -> None:
    """Emit-once warning per ``get_object_storage`` (R-22, 2026-05-01).

    Cache LRU per non spammare il logger ops in caller hot-path. Per i
    test: ``_warn_get_object_storage_deprecated_once.cache_clear``.
    """
    msg = (
        "[DEPRECATED] cloud_backends.get_object_storage e' un alias legacy. "
        "Migrare a get_runtime_object_storage() o get_backup_object_storage() "
        "esplicitamente. Removal target: 2026-Q4."
    )
    logger.warning(msg)
    _warnings.warn(msg, DeprecationWarning, stacklevel=3)


def get_object_storage() -> ObjectStorage:
    """[DEPRECATED] Alias di `get_runtime_object_storage`.

    Mantenuto come compat shim per i callsite che non distinguevano runtime
    vs backup. I nuovi callsite devono usare `get_runtime_object_storage`
    o `get_backup_object_storage` esplicitamente. Behavior identico
    a `get_runtime_object_storage` (capability check + runtime bucket
    resolution con fallback BACKUP_BUCKET legacy).

    R-22 (2026-05-01): emette ``DeprecationWarning`` + logger.warning
    emit-once. Removal target: 2026-Q4 (post-migrazione caller interni).

    Config via env:
      CLOUD_PROVIDER=aws (default)
        BACKUP_S3_BUCKET (legacy) / BACKUP_BUCKET (nuovo, cross-provider)
        AWS_REGION (default eu-south-1)

      CLOUD_PROVIDER=minio (A1-B2, ADR-0015)
        BACKUP_BUCKET — required
        MINIO_ENDPOINT_URL — required (es. http://minio.internal:9000)
        MINIO_ACCESS_KEY — required
        MINIO_SECRET_KEY — required
        AWS_REGION — default us-east-1 (MinIO ignora region)
    """
    _warn_get_object_storage_deprecated_once()
    return get_runtime_object_storage()


def get_log_archive_storage() -> ObjectStorage:
    """Ritorna l'ObjectStorage dedicato all'archive log (O-ARC, ADR-0014 §A3).

    Gemella di `get_object_storage` ma su bucket distinto: IAM boundary
    separato per archive log vs backup DB e' un invariante di design,
    non un'opzione. Per questo la factory e' dedicata e non parametrica
    su `get_object_storage`.

    La coerenza config (ENABLED + provider reale + `LOG_ARCHIVE_BUCKET`
    distinto da `BACKUP_BUCKET` + prefix + batch_rows + audit retention)
    e' gia' enforcata al boot da `validate_storage_config` piu' in basso
    in questo file: questa factory presume la precondizione verificata.
    Il `ValueError` difensivo su bucket unset e' un guard ridondante per
    chiamate out-of-contract (es. `ENABLED=false` che tocca il hook).

    Config via env:
      CLOUD_PROVIDER=aws (default)
        LOG_ARCHIVE_BUCKET — required (nessun fallback legacy: per
                              design il bucket archive e' nuovo)
        AWS_REGION — default eu-south-1
    """
    provider = _get_provider()
    if provider == "aws":
        bucket = os.getenv("LOG_ARCHIVE_BUCKET")
        if not bucket:
            raise ValueError(
                "LOG_ARCHIVE_BUCKET non configurato (richiesto quando "
                "LOG_ARCHIVE_ENABLED=True)."
            )
        return AwsS3ObjectStorage(
            bucket=bucket,
            region=os.getenv("AWS_REGION", "eu-south-1"),
        )
    # Estendere qui per Azure / GCP (stesso pattern di get_object_storage
    raise ValueError(f"CLOUD_PROVIDER non supportato: {provider!r}")


# Storage policy — boot-time validation (A1


VALID_STORAGE_POLICIES = ("db", "object_storage", "hybrid")
# Provider effettivamente implementati in cloud_backends
IMPLEMENTED_PROVIDERS = ("aws", "minio", "none")
# Provider noti all'enum (inclusi quelli roadmap P1/P2): dichiararli e'
# accettato in validate_storage_config solo se policy=db (= bucket non
# consultato), altrimenti fail-fast perche' la factory non li costruisce
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
      3. Se policy != db: BACKUP_BUCKET (o legacy BACKUP_S3_BUCKET) settato
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
        # NOTA (BE-CONFIG-001E + ADR-0021 allowlist): `os.getenv` diretto
        # per design — questo helper e' un boot-time fail-fast validator
        # che deve distinguere "user ha settato l'env" da "user non l'ha
        # settato". Settings.get applicherebbe `_converti_tipo_env` che
        # silently swallowa valori malformati e ritorna il default
        # rompendo il fail-fast su typo (es. `LOG_ARCHIVE_BATCH_ROWS=abc`
        # passerebbe il boot, e in produzione l'archive userebbe 10000
        # invece di crashare loud al boot). Il runtime path consuma il
        # valore via `settings.LOG_ARCHIVE_BATCH_ROWS` property come da
        # ADR-0021
        raw = os.getenv(name)
        if raw is None:
            return
        try:
            value = int(raw)
        except (TypeError, ValueError):
            _fail(f"{name}={raw!r} non e' un intero valido.")
            return  # unreachable: _fail solleva
        if value <= 0:
            _fail(f"{name}={value} deve essere > 0.")

    policy = (os.getenv("STORAGE_POLICY") or "hybrid").strip().lower()
    if policy not in VALID_STORAGE_POLICIES:
        _fail(
            f"STORAGE_POLICY={policy!r} non valido. "
            f"Valori ammessi: {', '.join(VALID_STORAGE_POLICIES)}."
        )

    provider = (os.getenv("CLOUD_PROVIDER") or "aws").strip().lower()
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

    bucket = os.getenv("BACKUP_BUCKET") or os.getenv("BACKUP_S3_BUCKET")

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
        if provider == "none":
            _fail(
                f"STORAGE_POLICY={policy} richiede un object storage provider, "
                "ma CLOUD_PROVIDER=none. Imposta CLOUD_PROVIDER=aws o cambia "
                "policy a db."
            )
        if not bucket:
            _fail(
                f"STORAGE_POLICY={policy} richiede un bucket object storage, "
                "ma ne' BACKUP_BUCKET ne' BACKUP_S3_BUCKET (legacy) sono "
                "configurati."
            )
        # Policy `object_storage` richiede bucket runtime esplicito
        # niente fallback silenzioso su BACKUP_BUCKET (semanticamente
        # diverso, IAM/lifecycle distinti). Con `hybrid` il fallback
        # NOTE: legacy resta consentito per retro-compat dev/test
        if policy == "object_storage":
            runtime_bucket = (
                os.getenv("OBJECT_STORAGE_BUCKET")
                or os.getenv("PLENORA_ARTIFACT_BUCKET")
            )
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
            enabled_raw = (os.getenv("OBJECT_STORAGE_ENABLED") or "true").strip().lower()
            if enabled_raw not in ("true", "1", "yes"):
                _fail(
                    "STORAGE_POLICY=object_storage richiede "
                    "OBJECT_STORAGE_ENABLED=true. Con la capability "
                    "disabilitata il path runtime (result/file_source/eseguibili/"
                    "IMDF/intermediate) non e' operativo. Abilita la "
                    "capability o cambia policy a db/hybrid."
                )
        # A1-B2: MinIO richiede credenziali esplicite + endpoint (no IAM chain
        if provider == "minio":
            for name in (
                "MINIO_ENDPOINT_URL",
                "MINIO_ACCESS_KEY",
                "MINIO_SECRET_KEY",
            ):
                if not os.getenv(name):
                    _fail(
                        f"CLOUD_PROVIDER=minio richiede {name} configurato."
                    )

    # Soglie dispatch: solo hybrid le consulta effettivamente
    if policy == "hybrid":
        _check_positive_int("RESULT_S3_THRESHOLD_BYTES")
        _check_positive_int("FILE_SOURCE_S3_THRESHOLD_BYTES")

    # Hard cap DB: attivo in db (primario) e in hybrid (guard ramo DB
    if policy in ("db", "hybrid"):
        _check_positive_int("RESULT_DB_HARD_CAP_BYTES")

    archive_enabled_raw = (os.getenv("LOG_ARCHIVE_ENABLED") or "false").strip().lower()
    if archive_enabled_raw in ("true", "1", "yes"):
        # 1. STORAGE_POLICY deve essere object_storage o hybrid
        if policy == "db":
            _fail(
                "LOG_ARCHIVE_ENABLED=true incompatibile con STORAGE_POLICY=db: "
                "archive non ha target object storage. Imposta "
                "STORAGE_POLICY=object_storage o hybrid, o disabilita "
                "LOG_ARCHIVE_ENABLED."
            )
        # 2. CLOUD_PROVIDER reale richiesto
        if provider == "none":
            _fail(
                "LOG_ARCHIVE_ENABLED=true richiede un object storage "
                "provider, ma CLOUD_PROVIDER=none."
            )
        # 3. LOG_ARCHIVE_BUCKET required (intenzionalmente fuori da
        # Settings.DEFAULTS, required-if-enabled per design A3
        log_archive_bucket = os.getenv("LOG_ARCHIVE_BUCKET")
        if not log_archive_bucket:
            _fail(
                "LOG_ARCHIVE_ENABLED=true richiede LOG_ARCHIVE_BUCKET "
                "configurato (bucket dedicato archive, distinto da "
                "BACKUP_BUCKET)."
            )
        # 4. Bucket archive distinto da BACKUP_BUCKET/BACKUP_S3_BUCKET
        # IAM boundary (O-ARC §5.4, ADR-0014 §A3). A questo punto
        # `bucket` e' guaranteed truthy: regola 1 ha escluso policy=db
        # e il ramo A1 else-branch (riga 395) fallisce gia' se bucket
        # e' unset con policy != db
        if log_archive_bucket == bucket:
            _fail(
                f"LOG_ARCHIVE_BUCKET={log_archive_bucket!r} coincide con "
                "BACKUP_BUCKET/BACKUP_S3_BUCKET: IAM boundary richiesto "
                "distinto per archive log vs backup DB (O-ARC §5.4)."
            )
        # 5. Formato key prefix (se settata): vuoto o endswith "/"
        # Coerente con log_archive._build_object_key:206-209
        # NOTA (BE-CONFIG-001E + ADR-0021): `os.getenv` diretto per design
        # boot-time fail-fast (vedi `_check_positive_int`). Runtime via
        # `settings.LOG_ARCHIVE_KEY_PREFIX` property
        log_archive_prefix = os.getenv("LOG_ARCHIVE_KEY_PREFIX")
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
        # NOTA (BE-CONFIG-001E + ADR-0021): `os.getenv` diretto per design
        # boot-time fail-fast (vedi `_check_positive_int`). Runtime via
        # `settings.AUDIT_RETENTION_DAYS` property
        audit_retention_raw = os.getenv("AUDIT_RETENTION_DAYS")
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
