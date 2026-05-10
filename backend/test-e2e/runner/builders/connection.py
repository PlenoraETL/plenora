"""Builder per payload `ConnectionCreate` (POST /api/connections).

Le factory `ConnectionBuilder.<dialect>(...)` ritornano dict pronti
per l'invio HTTP al backend plenora. I default sono allineati col
docker-compose `test-e2e/docker/` (host=plenora-e2e-<dialect>, ecc.).

I nomi dei campi rispecchiano `app/core/connections/models.py::ConnectionCreate`:
    name, description, type, host, port, database, username, password.

Pure function: nessun side-effect, nessuna I/O. Input -> dict.
"""
from __future__ import annotations


class ConnectionBuilder:
    """Factory di payload per `POST /api/connections`.

    Ogni metodo statico ritorna un dict con i campi che il backend
    plenora si aspetta in `ConnectionCreate`. I default sono pensati
    per i container del compose di test (`test-e2e/docker/`).
    """

    @staticmethod
    def postgres(
        *,
        host: str = "plenora-e2e-postgres",
        port: int = 5432,
        database: str = "plenora_test",
        username: str = "plenora",
        password: str = "plenora_test_pwd",
        name: str = "e2e-postgres",
        description: str = "e2e postgres connection",
    ) -> dict:
        """Connection PostgreSQL (type=`postgres`)."""
        return {
            "name": name,
            "description": description,
            "type": "postgres",
            "host": host,
            "port": port,
            "database": database,
            "username": username,
            "password": password,
        }

    @staticmethod
    def mysql(
        *,
        host: str = "plenora-e2e-mysql",
        port: int = 3306,
        database: str = "plenora_test",
        username: str = "plenora",
        password: str = "plenora_test_pwd",
        name: str = "e2e-mysql",
        description: str = "e2e mysql connection",
    ) -> dict:
        """Connection MySQL (type=`mysql`)."""
        return {
            "name": name,
            "description": description,
            "type": "mysql",
            "host": host,
            "port": port,
            "database": database,
            "username": username,
            "password": password,
        }

    @staticmethod
    def mssql(
        *,
        host: str = "plenora-e2e-sqlserver",
        port: int = 1433,
        database: str = "plenora_test",
        username: str = "sa",
        password: str = "PlenoraTest!2026",
        name: str = "e2e-mssql",
        description: str = "e2e sqlserver connection",
    ) -> dict:
        """Connection SQL Server (type=`mssql`, NON `sqlserver`)."""
        return {
            "name": name,
            "description": description,
            "type": "mssql",
            "host": host,
            "port": port,
            "database": database,
            "username": username,
            "password": password,
        }

    @staticmethod
    def oracle(
        *,
        host: str = "plenora-e2e-oracle",
        port: int = 1521,
        database: str = "plenora_test",
        username: str = "plenora",
        password: str = "plenora_test_pwd",
        name: str = "e2e-oracle",
        description: str = "e2e oracle connection",
    ) -> dict:
        """Connection Oracle (type=`oracle`)."""
        return {
            "name": name,
            "description": description,
            "type": "oracle",
            "host": host,
            "port": port,
            "database": database,
            "username": username,
            "password": password,
        }

    @staticmethod
    def sqlite(
        *,
        path: str,
        name: str = "e2e-sqlite",
        description: str = "e2e sqlite connection",
    ) -> dict:
        """Connection SQLite (type=`sqlite`).

        SQLite non ha host/port/credentials; mappiamo ``path`` su
        ``database`` (path al file .db) e usiamo placeholder per i
        campi obbligatori del contratto ConnectionCreate.
        """
        return {
            "name": name,
            "description": description,
            "type": "sqlite",
            "host": "localhost",
            "port": 0,
            "database": path,
            "username": "",
            "password": "",
        }
