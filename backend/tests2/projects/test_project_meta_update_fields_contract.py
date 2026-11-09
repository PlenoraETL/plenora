"""Ogni campo che PATCH /projects/{id} scrive passa da safe_set_clause (09/11/2026).

Collaudo in produzione: salvare i tag di un progetto rispondeva 500 da tempo,
perche' ``tags`` (come ``chain_title``, appena aggiunto) non era in
``sql_safety._ALLOWED_UPDATE_FIELDS["projects"]`` e ``safe_set_clause``
sollevava ValueError. I test dei metadati sostituivano ``update_project_row``
con un finto, quindi non lo vedevano: qui si passa dalla funzione VERA,
fermando solo l'esecuzione SQL.
"""

from __future__ import annotations

import inspect
import re

import pytest

from app.core.projects import db as project_db
from app.core.projects import service as project_service
from app.db.sql_safety import safe_set_clause


def _meta_fields() -> set[str]:
    """I campi che l'aggiornamento dei metadati puo' scrivere (letti dal codice)."""
    src = inspect.getsource(project_service)
    return set(re.findall(r'fields\["([a-z_]+)"\]\s*=', src))


def test_enumeration_finds_the_metadata_fields():
    assert {"name", "description", "tags", "chain_title"} <= _meta_fields()


@pytest.mark.parametrize("field", sorted(_meta_fields()))
def test_every_metadata_field_is_accepted_by_safe_set_clause(field):
    clause, values = safe_set_clause("projects", {field: "x"})
    assert field in clause and values == ("x",)


def test_update_project_row_with_tags_and_chain_title_reaches_sql(monkeypatch):
    executed: list[tuple[str, tuple]] = []
    monkeypatch.setattr(project_db, "_execute", lambda sql, params=(): executed.append((sql, params)))
    monkeypatch.setattr(project_db, "_fetchone", lambda *a, **k: None)
    project_db.update_project_row("p1", tags=["a", "b"], chain_title="Titolo")
    sql, params = executed[0]
    assert sql.startswith("UPDATE projects SET") and "tags" in sql and "chain_title" in sql
    assert '["a", "b"]' in params and "Titolo" in params
