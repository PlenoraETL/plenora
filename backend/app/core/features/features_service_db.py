"""
Patch per FeaturesService — sostituisce le operazioni CRUD su file JSON
con operazioni su SQLite (tabella arcgis_connections).

Solo i metodi CRUD vengono riscritti.
Tutti gli altri metodi (test, browse, query, write) rimangono invariati.
"""

import json
import uuid
from datetime import datetime, timezone
from typing import List, Optional

import app.db.db_service as db
from app.utils.logger import get_logger
from app.shared.crypto import encrypt as _encrypt, decrypt as _decrypt
from app.core.features.models import (
    ArcGISConnection, ArcGISCreate, ArcGISUpdate, ArcGISList, ArcGISTestResult,
)

logger = get_logger(__name__)
DEFAULT_GROUP = "default"

# Campi sensibili cifrati a riposo: senza questa lista, password e
# client_secret verrebbero scritti in chiaro su arcgis_connections e
# chiunque leggesse il DB avrebbe le credenziali live. Usiamo lo stesso
# pattern di ConnectionService (app.shared.crypto Fernet, prefisso
# "enc:", idempotente).
_SENSITIVE_FIELDS = ("password", "client_secret")


def _ensure_default_group():
    now = datetime.now(timezone.utc).isoformat()
    db.execute("INSERT INTO groups (id, name, description, created_at, updated_at) VALUES (?,?,?,?,?) ON CONFLICT (id) DO NOTHING",
               (DEFAULT_GROUP, "Default", "Gruppo default", now, now))


def _row_to_connection(row: dict) -> ArcGISConnection:
    # decrypt() e' idempotente su valori legacy in chiaro (nessun prefisso
    # "enc:"): li restituisce tal quali, permettendo una migrazione lazy.
    return ArcGISConnection(
        id=row["id"],
        group_id=row.get("group_id") or DEFAULT_GROUP,
        name=row["name"],
        description=row.get("description") or "",
        server_url=row["server_url"],
        server_type=row.get("server_type") or "arcgis_server",
        auth_type=row.get("auth_type") or "token",
        username=row.get("username") or "",
        password=_decrypt(row.get("password") or "") or "",
        client_id=row.get("client_id") or "",
        client_secret=_decrypt(row.get("client_secret") or "") or "",
        token_url=row.get("token_url") or None,
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
        created_by=row.get("created_by"),
    )


def _generate_id(name: str) -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    short_uuid = str(uuid.uuid4())[:8]
    safe_name = "".join(c for c in name if c.isalnum() or c in (' ', '_')).strip()
    safe_name = safe_name.replace(' ', '_')[:30]
    return f"arcgis_{safe_name}_{timestamp}_{short_uuid}"


def create_connection_db(data: ArcGISCreate, group_id: str = DEFAULT_GROUP,
                          created_by: str = None) -> ArcGISConnection:
    _ensure_default_group()
    connection_id = _generate_id(data.name)
    now = datetime.now(timezone.utc).isoformat()

    # Auto-detect server_type
    server_type = data.server_type
    if not server_type:
        url = (data.server_url or "").lower()
        if "arcgis.com" in url:
            server_type = "arcgis_online"
        elif "portal" in url or "enterprise" in url:
            server_type = "arcgis_enterprise"
        else:
            server_type = "arcgis_server"

    # Cifra i campi sensibili prima dell'INSERT. encrypt() e' idempotente.
    enc_password = _encrypt(data.password or "") or ""
    enc_client_secret = _encrypt(data.client_secret or "") or ""

    db.execute("""
        INSERT INTO arcgis_connections
        (id, group_id, name, description, server_url, server_type, auth_type,
         username, password, client_id, client_secret, token_url, created_at, updated_at, created_by)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (connection_id, group_id, data.name, data.description or "",
          data.server_url, server_type, data.auth_type,
          data.username or "", enc_password,
          data.client_id or "", enc_client_secret,
          data.token_url or "", now, now, created_by))

    logger.info(f"Connessione ArcGIS creata: {connection_id} (group {group_id})", op="create")
    return _row_to_connection(dict(db.fetchone("SELECT * FROM arcgis_connections WHERE id = ?", (connection_id,))))


def list_connections_db(group_id: Optional[str] = None) -> ArcGISList:
    """
    Lista connessioni ArcGIS.
    - group_id None     -> tutte (uso superadmin)
    - group_id "abc"    -> solo del gruppo
    """
    if group_id is None:
        rows = db.fetchall("SELECT * FROM arcgis_connections ORDER BY updated_at DESC")
    else:
        rows = db.fetchall(
            "SELECT * FROM arcgis_connections WHERE group_id = ? ORDER BY updated_at DESC",
            (group_id,),
        )
    return ArcGISList(connections=[_row_to_connection(dict(r)) for r in rows])


def get_connection_db(connection_id: str) -> Optional[ArcGISConnection]:
    """
    Ritorna la connessione (senza filtro gruppo).
    Il group check va fatto dal chiamante con `assert_group_access`.
    """
    row = db.fetchone("SELECT * FROM arcgis_connections WHERE id = ?", (connection_id,))
    return _row_to_connection(dict(row)) if row else None


def update_connection_db(connection_id: str, data: ArcGISUpdate) -> Optional[ArcGISConnection]:
    if not db.fetchone("SELECT id FROM arcgis_connections WHERE id = ?", (connection_id,)):
        return None

    update_data = data.model_dump(exclude_unset=True)
    now = datetime.now(timezone.utc).isoformat()

    # Bugfix: il frontend riceve i campi sensibili mascherati come "***" nelle
    # list/get e li rispedisce inalterati se l'utente non li modifica.
    # Skipparli per non sovrascrivere i valori reali con la maschera.
    _SENSITIVE = {"password", "client_secret"}
    for sf in list(update_data.keys()):
        if sf in _SENSITIVE and update_data.get(sf) in (None, "", "***"):
            del update_data[sf]

    field_map = {
        "name": "name", "description": "description", "server_url": "server_url",
        "server_type": "server_type", "auth_type": "auth_type",
        "username": "username", "password": "password",
        "client_id": "client_id", "client_secret": "client_secret", "token_url": "token_url",
    }

    sets, params = [], []
    for pf, col in field_map.items():
        if pf in update_data:
            sets.append(f"{col} = ?")
            val = update_data[pf]
            # Cifratura at-rest per i campi sensibili (idempotente su valori
            # gia' cifrati grazie al prefisso "enc:" riconosciuto dal modulo
            # crypto).
            if pf in _SENSITIVE_FIELDS and isinstance(val, str) and val:
                val = _encrypt(val)
            params.append(val)

    if not sets:
        return get_connection_db(connection_id)

    sets.append("updated_at = ?")
    params.extend([now, connection_id])
    db.execute(f"UPDATE arcgis_connections SET {', '.join(sets)} WHERE id = ?", params)
    return get_connection_db(connection_id)


def delete_connection_db(connection_id: str) -> bool:
    if not db.fetchone("SELECT id FROM arcgis_connections WHERE id = ?", (connection_id,)):
        return False
    db.execute("DELETE FROM arcgis_connections WHERE id = ?", (connection_id,))
    logger.info(f"Connessione ArcGIS eliminata: {connection_id}", op="delete")
    return True
