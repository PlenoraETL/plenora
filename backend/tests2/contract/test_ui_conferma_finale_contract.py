"""UI conferma finale (06/11/2026): voci backend dell'elenco
docs/audits/ui-conferma-finale-2026-11-06.md. Un test per voce, che
fallirebbe senza la correzione:

- F3/F4 (migrazioni con lock e CREATE INDEX CONCURRENTLY all'avvio): tolte
  con la semplificazione H1–H7, test in
  tests2/contract/test_ui_semplificazione_h_contract.py;
- F9: la query delle sorgenti Manipola scorre i nodi una volta (LATERAL),
  niente sottoquery correlate;
- G5: «ha un manifest» = manifest NON VUOTO, una regola in SQL e in Python
  (parita' su PostgreSQL vero), il dettaglio la espone;
- G7: il fuso dell'healthcheck viene dal beat (heartbeat), il ripiego e'
  dichiarato (`timezone_source`).
"""

from __future__ import annotations

import inspect
import re
from types import SimpleNamespace

import pytest

from tests2.shared._real_pg import real_pg_marks, real_pg_schema

pytestmark = [pytest.mark.unit, pytest.mark.contract]


# ── F9 ─────────────────────────────────────────────────────────────────────

def test_manipola_source_refs_scan_the_nodes_once():
    from app.core.projects import db as projects_db

    sql = inspect.getsource(projects_db.ProjectRepository.list_manipola_source_refs)
    assert "LEFT JOIN LATERAL" in sql and sql.count("FILTER (WHERE") == 2
    # Prima: CTE dei nodi riletta da due sottoquery correlate (N x M).
    assert not re.search(r"WHERE\s+n\.id\s*=\s*p\.id", sql)
    assert sql.count("jsonb_array_elements(") == 1


# ── G5 ─────────────────────────────────────────────────────────────────────

MANIFEST_CASES = [
    ({}, False),
    (None, False),
    ({"inputs": []}, True),
    ({"schema_version": 1, "inputs": [{"key": "input_file", "kind": "file"}]}, True),
]


@pytest.mark.parametrize("manifest, present", MANIFEST_CASES)
def test_manifest_present_is_a_non_empty_manifest(manifest, present):
    from app.core.eseguibili import db as eseguibili_db

    assert eseguibili_db.manifest_present(manifest) is present


def test_detail_says_no_manifest_for_an_empty_one():
    from app.core.eseguibili import router as eseguibili_router

    record = SimpleNamespace(
        canvas={"nodes": [], "edges": []}, manifest={}, group_id="grp-1", id="e1", name="E",
        description="", expected_columns=[], default_output_format="xlsx", default_output_name=None,
        icon=None, source_canvas_id=None, created_by="u", created_at="", updated_at="",
        project_type="arricchisci", active_version=1, active_version_id="v1",
    )
    detail = eseguibili_router._to_details(record)
    assert detail["has_manifest"] is False and detail["manifest"] is None


def test_every_manifest_presence_check_uses_the_rule():
    from app.core.eseguibili import router as eseguibili_router

    src = inspect.getsource(eseguibili_router)
    assert not re.search(r"if\s+(not\s+)?(record|raw)_?\.?manifest\s*:", src)
    assert "manifest_present(" in src


@real_pg_marks
def test_manifest_rule_is_the_same_in_sql_and_python_on_real_postgres():
    import json

    from app.core.eseguibili import db as eseguibili_db

    with real_pg_schema() as conn:
        conn.execute("CREATE TABLE eseguibile_versions (id text, manifest jsonb)")
        for i, (manifest, _present) in enumerate(MANIFEST_CASES):
            conn.execute("INSERT INTO eseguibile_versions VALUES (%s, %s)",
                         (f"v{i}", None if manifest is None else json.dumps(manifest)))
        rows = conn.execute(
            f"SELECT v.id, {eseguibili_db.MANIFEST_PRESENT_SQL} AS present FROM eseguibile_versions v ORDER BY v.id"
        ).fetchall()
        # Versione assente (LEFT JOIN senza riga): nessun manifest.
        missing = conn.execute(
            f"SELECT {eseguibili_db.MANIFEST_PRESENT_SQL} AS present "
            "FROM (SELECT 1) x LEFT JOIN eseguibile_versions v ON false"
        ).fetchone()["present"]
    assert [r["present"] for r in rows] == [p for _m, p in MANIFEST_CASES]
    assert missing is False


# ── G7 ─────────────────────────────────────────────────────────────────────

def test_healthcheck_timezone_comes_from_the_beat_heartbeat(monkeypatch):
    from datetime import datetime, timezone

    from app.core.scheduler import cron_schedule, healthcheck_service

    api_tz = str(cron_schedule.beat_timezone())
    monkeypatch.setattr(healthcheck_service.scheduler_db, "count_enabled_schedules", lambda: 0)
    monkeypatch.setattr(healthcheck_service.scheduler_db, "count_stuck_schedules", lambda limit: 0)
    now = datetime.now(timezone.utc)
    monkeypatch.setattr(healthcheck_service.scheduler_db, "get_beat_heartbeat",
                        lambda: {"ts": now, "timezone": "America/New_York"})
    hc = healthcheck_service.get_beat_healthcheck()
    assert (hc.timezone, hc.timezone_source) == ("America/New_York", "beat")
    # Heartbeat senza fuso (beat non aggiornato) o fuso non valido: ripiego dichiarato.
    for row in ({"ts": now, "timezone": None}, {"ts": now, "timezone": "Marte/Base"}, None):
        monkeypatch.setattr(healthcheck_service.scheduler_db, "get_beat_heartbeat", lambda row=row: row)
        hc = healthcheck_service.get_beat_healthcheck()
        assert (hc.timezone, hc.timezone_source) == (api_tz, "api_config")


def test_beat_writes_its_timezone_with_the_heartbeat(monkeypatch):
    from zoneinfo import ZoneInfo

    import workers.beat_scheduler as beat

    calls: list = []
    monkeypatch.setattr(beat, "_pg_execute", lambda sql, params: calls.append((sql, params)))
    monkeypatch.setattr(beat, "beat_timezone", lambda: ZoneInfo("Asia/Tokyo"))
    beat.PlenoraBeatScheduler._write_heartbeat(SimpleNamespace())
    sql, params = calls[0]
    assert "timezone" in sql and params[1] == "Asia/Tokyo"
