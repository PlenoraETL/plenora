"""
PLENORA-ETL — Data Source Factory

Crea istanze dei backend a partire da una configurazione dizionario.
"""

from app.core.datasources.interface import BackendConfig, DataSourceInterface
from app.core.datasources.local import LocalDataSource
from app.core.datasources.ftp import FTPDataSource
from app.core.datasources.sftp import SFTPDataSource
from app.core.datasources.sharepoint import SharePointDataSource


# Mappa tipo → classe backend
_BACKENDS: dict[str, type[DataSourceInterface]] = {
    "local":      LocalDataSource,
    "ftp":        FTPDataSource,
    "sftp":       SFTPDataSource,
    "sharepoint": SharePointDataSource,
}


def create_backend(config_dict: dict) -> DataSourceInterface:
    """
    Istanzia il backend corretto a partire dal dizionario di configurazione
    (così come salvato nel file JSON su filesystem).

    Args:
        config_dict: Dizionario con almeno le chiavi:
            - id (str)
            - type ("local"|"ftp"|"sftp"|"sharepoint")
            - root_path (str)
            - tutti gli altri campi diventano config.extra

    Returns:
        Istanza del backend appropriato, pronta per il context manager.

    Raises:
        ValueError se il tipo non è supportato.
    """
    ds_type = config_dict.get("type", "")
    backend_cls = _BACKENDS.get(ds_type)
    if backend_cls is None:
        raise ValueError(
            f"Tipo Data Source non supportato: {ds_type!r}. "
            f"Valori validi: {list(_BACKENDS.keys())}"
        )

    # Campi "strutturati" che vanno in BackendConfig direttamente
    structured_keys = {"id", "type", "root_path", "name", "description", "created_at", "updated_at"}

    config = BackendConfig(
        id=config_dict.get("id", ""),
        type=ds_type,
        root_path=config_dict.get("root_path", ""),
        extra={k: v for k, v in config_dict.items() if k not in structured_keys and v is not None},
    )

    return backend_cls(config)


def supported_types() -> list[str]:
    """Lista dei tipi di Data Source supportati."""
    return list(_BACKENDS.keys())
