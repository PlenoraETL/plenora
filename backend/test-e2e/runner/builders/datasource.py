"""Builder per payload `DataSourceCreate` (POST /api/datasources).

I nomi dei campi sono allineati a `app/core/datasources/models.py::DataSourceCreate`:
    name, description, type, root_path, host, port, username, password,
    private_key, host_key_policy, tls, passive, ...

Pure function: nessun side-effect, nessuna I/O.
"""
from __future__ import annotations


class DatasourceBuilder:
    """Factory di payload per `POST /api/datasources`."""

    @staticmethod
    def ftp(
        *,
        host: str = "plenora-e2e-ftp",
        port: int = 21,
        username: str = "tester",
        password: str = "plenora_ftp_pwd",
        root_path: str = "/fixtures",
        passive: bool = True,
        tls: bool = False,
        name: str = "e2e-ftp",
        description: str = "e2e FTP fixture",
    ) -> dict:
        """Datasource FTP (type=`ftp`)."""
        return {
            "name": name,
            "description": description,
            "type": "ftp",
            "host": host,
            "port": port,
            "username": username,
            "password": password,
            "root_path": root_path,
            "passive": passive,
            "tls": tls,
        }

    @staticmethod
    def sftp(
        *,
        host: str = "plenora-e2e-sftp",
        port: int = 22,
        username: str = "tester",
        password: str = "plenora_sftp_pwd",
        root_path: str = "/fixtures",
        private_key: str | None = None,
        host_key_policy: str = "auto_add",
        name: str = "e2e-sftp",
        description: str = "e2e SFTP fixture",
    ) -> dict:
        """Datasource SFTP (type=`sftp`).

        Se ``private_key`` e' fornita (PEM string), il backend la usa
        al posto della password. ``host_key_policy`` controlla la
        verifica della host key (``auto_add`` per i test).
        """
        payload: dict = {
            "name": name,
            "description": description,
            "type": "sftp",
            "host": host,
            "port": port,
            "username": username,
            "password": password,
            "root_path": root_path,
            "host_key_policy": host_key_policy,
        }
        if private_key is not None:
            payload["private_key"] = private_key
        return payload

    @staticmethod
    def local(
        *,
        root_path: str,
        name: str = "e2e-local",
        description: str = "e2e local datasource fixture",
    ) -> dict:
        """Datasource locale (type=`local`).

        Solo ``root_path`` e' significativo (path su disco accessibile
        al backend); i restanti campi (host/port/credenziali) restano
        a default `None`.
        """
        return {
            "name": name,
            "description": description,
            "type": "local",
            "root_path": root_path,
        }
