"""cloud_backends.py — astrazione cloud-agnostic per secrets + object storage.

Obiettivo: concentrare in UN punto il codice SDK-specifico (oggi boto3).
Consumer (backup_db, test_restore, load_secrets) parlano solo al
Protocol, mai direttamente a boto3. Migrazione Azure/GCP = aggiungere
una classe, aggiornare la factory, zero change nei consumer.

Interface minime (YAGNI):
  - SecretsBackend.get_secrets(prefix) -> dict[str, str]
  - ObjectStorage.list_objects(prefix) -> list[dict]
  - ObjectStorage.upload(path, key)
  - ObjectStorage.download(key, path)

Scelta provider via env `CLOUD_PROVIDER` (default `aws`). Estensione
futura: `azure` -> Key Vault + Blob Storage; `gcp` -> Secret Manager +
Cloud Storage. Ogni nuovo provider tocca solo questo file + la IAM
policy del nuovo cloud.

Le factory ritornano istanze singleton-friendly ma NON cachate: costo
trascurabile (client AWS SDK ha la sua pool), semplicita' > micro-ott.
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import List, Protocol, TypedDict, runtime_checkable


# ---------------------------------------------------------------------------
# Protocols (interface contract)
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# AWS implementations
# ---------------------------------------------------------------------------


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
            ExtraArgs={
                "ServerSideEncryption": "AES256",
                "StorageClass": "STANDARD",
            },
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
        import io
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
            ExtraArgs={
                "ServerSideEncryption": "AES256",
                "StorageClass": "STANDARD",
            },
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


# ---------------------------------------------------------------------------
# Factory — dispatch via CLOUD_PROVIDER env
# ---------------------------------------------------------------------------


def _get_provider() -> str:
    return os.getenv("CLOUD_PROVIDER", "aws").lower()


def get_secrets_backend() -> SecretsBackend:
    """Ritorna il SecretsBackend appropriato per il provider configurato.

    Config via env:
      CLOUD_PROVIDER=aws (default)
        AWS_REGION (default eu-south-1)

      CLOUD_PROVIDER=azure  (future)
        AZURE_KEY_VAULT_URL
        AZURE_TENANT_ID / credenziali Managed Identity

      CLOUD_PROVIDER=gcp    (future)
        GCP_PROJECT
        credenziali Service Account
    """
    provider = _get_provider()
    if provider == "aws":
        return AwsSsmSecretsBackend(
            region=os.getenv("AWS_REGION", "eu-south-1"),
        )
    # Estendere qui per Azure / GCP:
    # if provider == "azure":
    #     from azure_backends import AzureKeyVaultSecretsBackend  # lazy import
    #     return AzureKeyVaultSecretsBackend(vault_url=os.getenv("AZURE_KEY_VAULT_URL"))
    # if provider == "gcp":
    #     from gcp_backends import GcpSecretManagerBackend
    #     return GcpSecretManagerBackend(project=os.getenv("GCP_PROJECT"))
    raise ValueError(f"CLOUD_PROVIDER non supportato: {provider!r}")


def get_object_storage() -> ObjectStorage:
    """Ritorna l'ObjectStorage appropriato per il provider configurato.

    Config via env:
      CLOUD_PROVIDER=aws (default)
        BACKUP_S3_BUCKET (legacy) / BACKUP_BUCKET (nuovo, cross-provider)
        AWS_REGION (default eu-south-1)
    """
    provider = _get_provider()
    if provider == "aws":
        bucket = os.getenv("BACKUP_BUCKET") or os.getenv("BACKUP_S3_BUCKET")
        if not bucket:
            raise ValueError(
                "BACKUP_BUCKET (o BACKUP_S3_BUCKET legacy) non configurato."
            )
        return AwsS3ObjectStorage(
            bucket=bucket,
            region=os.getenv("AWS_REGION", "eu-south-1"),
        )
    # Estendere qui per Azure / GCP (vedi get_secrets_backend).
    raise ValueError(f"CLOUD_PROVIDER non supportato: {provider!r}")
