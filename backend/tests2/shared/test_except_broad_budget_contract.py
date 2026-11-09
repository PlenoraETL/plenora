"""
Contract test "budget" per `except Exception` broad-catch.

L'audit cross-modulo ha contato 208 occorrenze di `except Exception`
in 50 file dell'app. Un narrow case-by-case e' impraticabile (e in
maggioranza non-desiderabile: la maggior parte sono safety-net
intenzionali su boundary I/O — Redis, FTP, file system, HTTP — e
narrow significherebbe enumerare tutte le possibili exception per
ogni call site, generando codice fragile).

Approccio "budget": lock-in del count attuale. Ogni nuovo `except
Exception` deve essere accompagnato dalla riduzione del budget
(narrowing di un altro broad-catch in altro file), oppure aumento
esplicito del budget con commit message che giustifica il broad
intent. Questo evita la regressione silente "qualcuno aggiunge un
catch broad senza motivo" e produce pressione gradual a narrowing.

Pattern d'uso quando si aggiunge un nuovo `except Exception`:
  1. Verifica che il broad sia INTENZIONALE (safety-net documentato,
     I/O boundary che fallback, top-level handler).
  2. Aggiungi commento adiacente che giustifica il broad.
  3. Bumpa `_EXPECT_BROAD_BUDGET` qui di +N (= numero net di nuovi
     broad-catch nel commit).

Quando si fa narrow di un broad esistente:
  1. Restringi a eccezioni specifiche.
  2. Decrementa `_EXPECT_BROAD_BUDGET` di -N.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.contract]


# Lock attuale: 724 occorrenze totali in `app/` (esclusi tests/,
# scripts/, migrations/). Diminuirebbe ad ogni narrow incrementale;
# aumentarne il valore richiede commit con motivazione esplicita.
# 736 -> 724: cutover IMDF a plenora.indoor.bundle (rimossi loader.py,
# persist_imdf_bundle, open_gdb_in_vsimem e i branch legacy GDB).
# 724 -> 697: collasso IMDF a una-run (rimossi validator, storage, persist,
# arcgis_uploader, publish_dispatcher, bundle_store, bundle_access, router).
# 697 -> 705: safety-net I/O non-bloccanti landati nei commit di
# orizzontalizzazione / dispatch carica / RAM-eviction (+6) e i due fallback
# di EnrichmentRunMode.begin_run (apertura run / lookup group_id eseguibile, +2).
# 705 -> 716: chiusura Sprint 2 — endpoint API ai boundary con catch
# difensivo che ritorna {success:false, message} invece di 500:
# preview-formula (#26), latest-by-project (#11), clone/rename/versions (#8/#12),
# canvas_sync applied_migrations (#2). +11.
# +2 (2026-06-13, Plenora 2.0 Fase 2 F2.2): task_progress.report_task_progress
# e finalize_task_result — safety-net best-effort sulla scrittura stato/esito
# task su PG; un errore di persistenza (DB giu', serializzazione) non deve mai
# far fallire il task (il polling Celery resta valido finche' la lettura non
# migra a PG in F2.3).
# +1 (2026-06-13, Plenora 2.0 Fase 1 cutover): DramatiqTaskRuntime._actor_queue_name
# risolve la coda dell'actor con fallback "default" se il broker non espone
# get_actor (fake nei test) o l'actor non e' registrato — best-effort di routing.
# +7 (2026-06-13, Plenora 2.0 Fase 4 F4.1): PgKeyValueStore (app/shared/kv/pg_store.py)
# — boundary KV su PG: letture fail-soft (None/[]/False), scritture fail-loud
# (KeyValueStoreUnavailable). 7 metodi del protocol
# (get/set/delete/exists/scan_keys/acquire_lock/release_lock).
# 732 -> 721 (2026-06-13, Plenora 2.0 Fase 5 F5.1): rimossi RedisKeyValueStore
# (app/shared/kv/redis_store.py) e il ramo redis.exceptions di resilience/transient
# — il backend KV e' interamente su PostgreSQL, niente piu' dipendenza redis-py.
# +1 (2026-06-13, Plenora 2.0 Fase 5 F5.2): PgTaskRevocationStore.is_revoked —
# fail-soft sulla lettura del flag di annullamento (un errore di lettura NON
# deve revocare un task valido); boundary infrastrutturale intenzionale.
# +3 (2026-06-13, Plenora 2.0 Fase 5 F5.3a): probe broker RabbitMQ per /ready —
# probes._pika_module (SDK opzionale), RabbitmqRuntimePort.ping close best-effort,
# health.check_broker fail-closed. Boundary infrastrutturale (rimpiazzo della
# PING Redis sul path readiness).
# 725 -> 719 (2026-06-13, Plenora 2.0 Fase 5 F5.3b): osservabilita' admin
# PG-derived. Rimossi i catch di get_redis_status, get_workers_status (celery
# inspect) e delle funzioni Redis/Celery di probes.py; net -6.
# +2 (2026-06-13, Plenora 2.0 Fase 5 F5.4): cron_schedule.beat_timezone —
# fallback su ZoneInfo (settings assente / tz invalida) -> default Europe/Rome.
# 721 -> 720 (2026-06-13, Plenora 2.0 Fase 5 F5.6): rimosso list_active_tasks
# (dead code, inspect worker Celery) col suo catch broad.
# 2026-07-14 audit: narrowed five deterministic config/filesystem catches.
# The remaining catches are boundary safety-nets around HTTP/DB/geo/storage
# libraries or API exception translation; 749 is the new shrink-only ratchet.
# +2 (2026-07-14): explorer object storage superadmin. I due catch sono il
# boundary HTTP fail-safe per provider S3 compatibili, che espongono famiglie
# di eccezioni diverse; list risponde 503 e download 404 senza leak interni.
# 751 -> 775 (2026-07-18): riallineamento dopo i boundary introdotti dai
# runtime eseguibili versionati, cleanup storage e lifecycle Dramatiq. Sono
# safety-net su DB/object storage/provider e cleanup secondari: non devono
# mascherare l'esito primario o propagare dettagli interni. Il valore resta un
# ratchet shrink-only: ogni successivo narrow deve abbassarlo.
# 775 -> 783 (2026-07-20): correzione del conteggio della baseline precedente.
# HEAD contiene gia' 783 occorrenze e il worktree non ne introduce di nuove;
# resta invariato il vincolo shrink-only per le modifiche successive.
# +1: per-node schema inference safety-net. Arbitrary handler failures are
# logged and fall back to the first input; other project nodes remain available.
# +1 (2026-11-05): connection_tests.results._after_effect. Registrazione e
# azzeramento dell'esito del «Test connessione» seguono un effetto gia'
# compiuto: qualunque errore si logga ERROR con lo stack e non arriva all'utente.
# +1 (2026-11-09): dashboard.graph.build_dashboard_graph, una parte della home
# per catene (collegamenti, esecuzioni, Eseguibili, pianificazioni) che fallisce
# va in `unavailable` e le altre restano, come GET /dashboard/counts.
_EXPECT_BROAD_BUDGET = 786

_BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent
_APP_ROOT = _BACKEND_ROOT / "app"

# Pattern: `except Exception` con o senza alias (`as e`) e con
# eventuali parentesi multiple (`except (Exception, X)`). Quest'ultimo
# caso e' GIA` narrow (lista esplicita), quindi NON conta come broad.
_BROAD_EXCEPT_RE = re.compile(
    r"^\s*except\s+Exception\s*(?:as\s+\w+)?\s*:",
    re.MULTILINE,
)


def _count_broad_except_in_app() -> tuple[int, dict[str, int]]:
    """Ritorna (total, per_file) dei broad-catch `except Exception:`."""
    per_file: dict[str, int] = {}
    total = 0
    for py_file in _APP_ROOT.rglob("*.py"):
        source = py_file.read_text(encoding="utf-8", errors="ignore")
        matches = _BROAD_EXCEPT_RE.findall(source)
        if matches:
            rel = py_file.relative_to(_BACKEND_ROOT).as_posix()
            per_file[rel] = len(matches)
            total += len(matches)
    return total, per_file


def test_except_broad_budget_not_exceeded():
    """Il count totale non deve superare il budget hardcoded."""
    total, per_file = _count_broad_except_in_app()

    if total > _EXPECT_BROAD_BUDGET:
        top_offenders = sorted(per_file.items(), key=lambda x: -x[1])[:10]
        lines = [f"  {count:>3}  {rel}" for rel, count in top_offenders]
        raise AssertionError(
            f"`except Exception:` count = {total}, budget = {_EXPECT_BROAD_BUDGET}\n"
            f"Hai aggiunto {total - _EXPECT_BROAD_BUDGET} nuovi broad-catch senza "
            f"riduzione compensativa.\n\n"
            f"Top 10 offender:\n" + "\n".join(lines) + "\n\n"
            f"Se il nuovo broad e' INTENZIONALE (safety-net, I/O fallback):\n"
            f"  1. aggiungi commento adiacente che giustifica\n"
            f"  2. bumpa `_EXPECT_BROAD_BUDGET` a {total} in questo file\n"
            f"Altrimenti narrow l'eccezione a tipi specifici."
        )


def test_except_broad_budget_not_stale():
    """Il budget non e' sotto-stimato: se sei sotto budget, abbassalo.

    Soft check: tolleranza di -5 per evitare flake su narrow paralleli.
    Lo scopo e' costringere a abbassare il budget quando si narrow.
    """
    total, _ = _count_broad_except_in_app()
    drift = _EXPECT_BROAD_BUDGET - total
    if drift > 5:
        raise AssertionError(
            f"`except Exception:` count = {total}, budget = {_EXPECT_BROAD_BUDGET}\n"
            f"Sei sotto-budget di {drift} (hai narrow ma non hai aggiornato il\n"
            f"contract). Abbassa `_EXPECT_BROAD_BUDGET` a {total} in questo file\n"
            f"per fissare il nuovo limite."
        )
