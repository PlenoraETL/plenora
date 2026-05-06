"""
CRUD per la tabella `eseguibili` (storage: PostgreSQL via
`app.db.pg_connection`).

Lo schema PG e' creato dalle migrazioni in `backend/migrations/`. Il file
storico `app/db/config_schema.sql` e' mantenuto come reference legacy
(vedi header del file). Questo modulo contiene solo le funzioni di accesso.

Migrato da `app.db.db_service` a `app.db.pg_connection` da
BE-GO-READY-DB-ENGINE-011: gli unici simboli usati qui
(`execute`, `fetchone`, `fetchall`) sono primitive DAL la cui canonical home
e' `pg_connection`; il facade `db_service` li re-esporta solo per
back-compat. Zero monkeypatch coupling sul nome `eseguibili.db.db.<x>`,
quindi la migrazione e' un'edit di import senza rotture downstream.
"""

import json
import uuid
from datetime import datetime, timezone
from typing import List, Optional, Dict, Any

from app.db import pg_connection as db


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_row(row) -> Optional[Dict[str, Any]]:
    """Converte una row DB in dict Python con JSON deserializzato."""
    if row is None:
        return None
    d = dict(row)
    # canvas e expected_columns sono JSON in TEXT
    if d.get("canvas"):
        try:
            d["canvas"] = json.loads(d["canvas"]) if isinstance(d["canvas"], str) else d["canvas"]
        except json.JSONDecodeError:
            d["canvas"] = {}
    if d.get("expected_columns"):
        try:
            d["expected_columns"] = json.loads(d["expected_columns"]) if isinstance(d["expected_columns"], str) else d["expected_columns"]
        except json.JSONDecodeError:
            d["expected_columns"] = []
    else:
        d["expected_columns"] = []
    return d


def create_eseguibile(
    name: str,
    canvas: Dict[str, Any],
    expected_columns: List[Dict[str, Any]],
    group_id: str,
    created_by: str,
    description: str = "",
    default_output_format: str = "xlsx",
    default_output_name: Optional[str] = None,
    source_canvas_id: Optional[str] = None,
    icon: Optional[str] = None,
) -> Dict[str, Any]:
    """Crea un nuovo eseguibile. Ritorna il record creato."""
    eid = f"exec_{uuid.uuid4().hex[:12]}"
    now = _now()
    db.execute(
        """
        INSERT INTO eseguibili (
            id, name, description, canvas, expected_columns,
            default_output_format, default_output_name, icon, source_canvas_id,
            group_id, created_by, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            eid, name, description,
            json.dumps(canvas, ensure_ascii=False),
            json.dumps(expected_columns, ensure_ascii=False),
            default_output_format, default_output_name, icon, source_canvas_id,
            group_id, created_by, now, now,
        )
    )
    return get_eseguibile(eid)


def get_eseguibile(eid: str, group_id: str = None) -> Optional[Dict[str, Any]]:
    """Ritorna l'eseguibile con canvas e expected_columns deserializzati.

    group_id=None → no filter (backward compat per uso interno).
    Passare group_id dal router per defense-in-depth multi-tenant.
    """
    if group_id:
        row = db.fetchone(
            "SELECT * FROM eseguibili WHERE id = ? AND group_id = ?",
            (eid, group_id)
        )
    else:
        row = db.fetchone("SELECT * FROM eseguibili WHERE id = ?", (eid,))
    return _parse_row(row)


def list_eseguibili(group_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """Lista eseguibili, opzionalmente filtrati per gruppo.

    Non carica canvas/expected_columns per la summary (prestazioni).
    Usa get_eseguibile() per i dettagli completi.
    """
    if group_id:
        rows = db.fetchall(
            """SELECT id, name, description, default_output_format, default_output_name,
                      icon, source_canvas_id, group_id, created_by, created_at, updated_at,
                      expected_columns
               FROM eseguibili WHERE group_id = ? ORDER BY created_at DESC""",
            (group_id,)
        )
    else:
        rows = db.fetchall(
            """SELECT id, name, description, default_output_format, default_output_name,
                      icon, source_canvas_id, group_id, created_by, created_at, updated_at,
                      expected_columns
               FROM eseguibili ORDER BY created_at DESC"""
        )
    result = []
    for row in rows:
        d = dict(row)
        # parsing minimale: solo count di expected_columns per la summary
        try:
            cols = json.loads(d.get("expected_columns") or "[]")
            d["expected_columns_count"] = len(cols)
        except Exception:
            d["expected_columns_count"] = 0
        d.pop("expected_columns", None)  # non serve nella summary
        result.append(d)
    return result


def list_eseguibili_by_source(source_canvas_id: str) -> List[Dict[str, Any]]:
    """Ritorna gli eseguibili pubblicati a partire dal progetto indicato.

    Restituisce solo id e name (sufficienti per messaggi di errore).
    """
    rows = db.fetchall(
        "SELECT id, name FROM eseguibili WHERE source_canvas_id = ?",
        (source_canvas_id,)
    )
    return [dict(row) for row in rows]


def update_eseguibile(eid: str, **fields) -> Optional[Dict[str, Any]]:
    """Aggiorna i campi passati (solo metadata, non canvas/expected_columns)."""
    allowed = {"name", "description", "default_output_format", "default_output_name", "icon"}
    to_update = {k: v for k, v in fields.items() if k in allowed and v is not None}
    if not to_update:
        return get_eseguibile(eid)
    to_update["updated_at"] = _now()
    set_clause = ", ".join(f"{k} = ?" for k in to_update.keys())
    values = list(to_update.values()) + [eid]
    db.execute(f"UPDATE eseguibili SET {set_clause} WHERE id = ?", tuple(values))
    return get_eseguibile(eid)


def delete_eseguibile(eid: str) -> bool:
    """Elimina un eseguibile. Ritorna True se almeno una riga è stata cancellata."""
    # db.execute non restituisce rowcount in modo uniforme, verifichiamo prima
    if get_eseguibile(eid) is None:
        return False
    db.execute("DELETE FROM eseguibili WHERE id = ?", (eid,))
    return True
