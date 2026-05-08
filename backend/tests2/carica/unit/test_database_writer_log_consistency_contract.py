"""
Phase 2 err-dbwriter commit C — normalize log levels in cleanup guards + log-only points.

Commit log-only: nessun cambio di flusso, nessun cambio di classe
"""

from __future__ import annotations

import inspect
import logging
from typing import List

import pytest

from app.core.carica.database_writer import DatabaseWriter
from app.core.carica.writer_oracle_redef import execute_oracle_redefinition


pytestmark = [pytest.mark.unit, pytest.mark.carica, pytest.mark.contract]


# Logger name canonico del cluster carica/writer. Tutti i moduli del
# cluster devono usare `get_logger("app.core.carica.writer")` per
# produrre log uniformemente catturabili. Pre-fix il branch GeoJSON
# di `_geometry_to_wkt` faceva `logging.getLogger(__name__)` (nome
# diverso) — il test #2 verifica anti-regressione.
_WRITER_LOGGER_NAME = "app.core.carica.writer"


class _RecordCollector(logging.Handler):
    """Handler logging che accumula i LogRecord per ispezione dal test."""

    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.records: List[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:  # noqa: D401
        self.records.append(record)


@pytest.fixture
def captured_writer_logs():
    """Cattura tutti i LogRecord emessi sul logger canonico
    `"app.core.carica.writer"`.

    Approccio: aggancio un handler stdlib al logger named-canonical.
    Lo `StructuredLogger` wrapper di `app/utils/logger.py` delega a
    `self._logger.log(...)` sul logger stdlib omonimo; quindi
    indipendentemente da DOVE vive il metodo che chiama
    `logger.warning(...)`, finche' il logger e' stato ottenuto via
    `get_logger("app.core.carica.writer")` il record arriva qui.

    Imposto `setLevel(DEBUG)` DOPO `addHandler` perche' `get_logger`
    inizializza il logger a INFO; senza questa riga i log debug
    non arriverebbero al collector.
    """
    stdlib_logger = logging.getLogger(_WRITER_LOGGER_NAME)
    handler = _RecordCollector()
    prev_level = stdlib_logger.level
    stdlib_logger.addHandler(handler)
    stdlib_logger.setLevel(logging.DEBUG)
    try:
        yield handler
    finally:
        stdlib_logger.removeHandler(handler)
        stdlib_logger.setLevel(prev_level)


def _messages_at(handler: _RecordCollector, level: int) -> List[str]:
    """Estrae i messaggi formattati dei record a un dato livello."""
    return [r.getMessage() for r in handler.records if r.levelno == level]


def _all_messages(handler: _RecordCollector) -> List[str]:
    return [r.getMessage() for r in handler.records]


# _geometry_to_wkt — log behavior osservabile


def test_geometry_to_wkt_logs_on_wkb_fallback_exhaustion(captured_writer_logs):
    """Pre-fix: `except Exception: return value` → zero log.
    Post-fix: log.debug prima di return value (contratto return invariato).

    Modernization B2.0: contract = "almeno 1 LogRecord sul logger canonico
    `app.core.carica.writer` con messaggio che menziona WKB". Indipendente
    dalla posizione fisica del metodo.
    """
    writer = object.__new__(DatabaseWriter)
    # Stringa hex invalida → wkb.loads solleva → ramo fallback
    malformed_wkb_hex = "ZZZZNOT_A_VALID_WKB_HEX"

    result = writer._geometry_to_wkt(malformed_wkb_hex)

    # Contratto return INVARIATO: ritorna il valore raw
    assert result == malformed_wkb_hex, (
        "Commit C e' log-only: il return value di _geometry_to_wkt "
        "deve restare identico al pre-fix (stringa raw)."
    )

    # Almeno un log deve essere stato emesso prima del return: debug o warning.
    # Pre-fix: zero record sul logger canonico.
    msgs = _all_messages(captured_writer_logs)
    assert msgs, (
        "Atteso almeno un LogRecord sul logger 'app.core.carica.writer' "
        "dopo WKB fail. Pre-fix: zero log."
    )
    assert any("WKB" in m or "wkb" in m.lower() for m in msgs), (
        f"Atteso messaggio che menziona WKB, ottenuto: {msgs!r}"
    )


def test_geometry_to_wkt_geojson_uses_module_logger(captured_writer_logs):
    """Pre-fix: `logging.getLogger(__name__).warning(...)` — un logger
    creato ad hoc nella funzione, nome diverso dal logger canonico
    `app.core.carica.writer`. Post-fix: usa `logger` di modulo, stesso
    nome canonico per tutto il file.

    Modernization B2.0: contract = "il warning su GeoJSON malformato
    viene emesso sul logger canonico per nome". Se un giorno il branch
    GeoJSON ricominciasse a usare `logging.getLogger("...database_writer")`
    o un altro nome, il record non arriverebbe a questo handler (loggers
    con nomi diversi sono separati: la sibling-propagation non esiste,
    solo verso parent → root). Test fallirebbe come voluto.
    """
    writer = object.__new__(DatabaseWriter)
    # Stringa che sembra GeoJSON (parte con '{') ma JSON invalido
    malformed_geojson = '{not valid json'

    result = writer._geometry_to_wkt(malformed_geojson)

    # Contratto: cade al ramo WKB che pure fallisce → return raw
    assert result == malformed_geojson

    warning_msgs = _messages_at(captured_writer_logs, logging.WARNING)
    assert warning_msgs, (
        f"Atteso warning sul logger canonico '{_WRITER_LOGGER_NAME}' per "
        f"GeoJSON fallito. Pre-fix: il log andava su un logger ad-hoc "
        f"`logging.getLogger(__name__)` — nome diverso, non catturato qui. "
        f"Tutti i record visti: {_all_messages(captured_writer_logs)!r}"
    )
    assert any("GeoJSON" in m for m in warning_msgs), (
        f"Atteso messaggio che menziona 'GeoJSON', ottenuto: {warning_msgs!r}"
    )


def test_geometry_to_wkt_return_contract_invariant_on_valid_wkt():
    """Sanity: per WKT gia' ben formato, la funzione ritorna la stringa
    trimmed come pre-fix. Commit C non deve alterare il happy path.
    """
    writer = object.__new__(DatabaseWriter)
    valid_wkt = "POINT (1 2)"
    result = writer._geometry_to_wkt(valid_wkt)
    assert result == valid_wkt


# _prepare_dataframe — log behavior su ImportError geopandas


def test_prepare_dataframe_logs_when_geopandas_missing(monkeypatch, captured_writer_logs):
    """Pre-fix: `except ImportError: pass` — skip silenzioso.
    Post-fix: logger.debug informativo. Return invariato.

    Modernization B2.0: contract = "almeno 1 LogRecord DEBUG sul
    logger canonico con messaggio che menziona 'geopandas'".

    Simulo geopandas mancante via monkeypatch su sys.modules: mettendo
    `None` come valore, Python solleva ImportError al prossimo
    `import geopandas` dentro _prepare_dataframe.
    """
    import sys
    import pandas as pd

    # Forza ImportError anche se geopandas e' gia' stato caricato
    # nel test runner
    monkeypatch.setitem(sys.modules, "geopandas", None)

    # Writer minimale: _prepare_dataframe non usa self.config per un
    # DF senza colonne geometriche complesse. Uso object.__new__.
    writer = object.__new__(DatabaseWriter)

    df = pd.DataFrame({"id": [1, 2], "name": ["a", "b"]})

    result = writer._prepare_dataframe(df)

    # Contratto return: DataFrame prepared esce comunque
    assert isinstance(result, pd.DataFrame)
    assert len(result) == 2

    # Log atteso sul logger canonico (debug, livello scelto dal commit C
    # perche' dependency opzionale: non e' un errore da urlare, ma neanche
    # un silenzio totale).
    debug_msgs = _messages_at(captured_writer_logs, logging.DEBUG)
    assert any("geopandas" in m for m in debug_msgs), (
        f"Atteso log DEBUG che menziona 'geopandas' quando la dependency "
        f"manca. Pre-fix: `pass` silenzioso → invisibile in prod. "
        f"Ottenuto debug: {debug_msgs!r} (tutti record: "
        f"{_all_messages(captured_writer_logs)!r})"
    )


# Cleanup guards log level — verifica che i metodi usano WARNING non DEBUG


def test_cleanup_guards_use_warning_not_debug_on_orphan_leftover():
    """Verifica statica sul sorgente delle FUNZIONI: i sei cleanup guards
    elencati nell'audit §4.1 commit C sono associati a `logger.warning`.

    La ragione: cleanup che fallisce lascia stato orfano (tabella
    stg_/bak_/interim_ non droppata) visibile all'operatore nel
    DB target. Deve essere chiaramente segnalato, non relegato
    a debug-level (che in prod di default non viene emesso).

    Modernization B2.0: pre-B2.0 questo test ispezionava
    `inspect.getsource(dw_module)` — il sorgente del modulo. Questo legava
    il test alla posizione fisica dei 3 metodi (se spostati in mixin
    diversi, il source del modulo facade non li conterrebbe piu' →
    stringhe missing → test rosso).

    Post-B2.0: ispeziono `inspect.getsource(<unbound_method>)` per ciascuno
    dei 3 metodi che contengono le cleanup guards. `inspect.getsource`
    legge via `__code__.co_filename` + line numbers, quindi trova il
    source dovunque il metodo sia fisicamente definito (modulo facade,
    mixin, sotto-modulo). Il contract bit-per-bit "logger.warning prima
    del contesto, non logger.debug" e' preservato.
    """
    # Mappa: metodo → contesti (cleanup guard messages) attesi nel suo body.
    # Suddivisione presa dall'audit §4.1 commit C, distribuzione storica.
    method_to_contexts = {
        DatabaseWriter._truncate_insert_stage_swap: [
            "[STAGE+SWAP] cleanup shadow post-CREATE fallito",
        ],
        DatabaseWriter._replace_atomic: [
            "[REPLACE ATOMIC] cleanup difensivo orfani stg/bak fallito",
            "[REPLACE ATOMIC] cleanup shadow post-INSERT fail fallito",
        ],
        execute_oracle_redefinition: [
            "[ORACLE REDEF] cleanup difensivo interim orfana fallito",
            "[ORACLE REDEF] cleanup interim post-START_REDEF fail fallito",
            "[ORACLE REDEF] drop interim post-ABORT fallito",
        ],
    }

    for method, contexts in method_to_contexts.items():
        try:
            source = inspect.getsource(method)
        except (OSError, TypeError) as e:
            pytest.fail(
                f"inspect.getsource({method.__qualname__}) ha fallito: {e}. "
                f"Se il metodo e' stato rinominato/spostato/wrappato, "
                f"aggiornare il riferimento nel test (NON rimuovere il "
                f"contract: la cleanup guard deve restare a logger.warning)."
            )

        for ctx in contexts:
            # Ogni contesto deve apparire associato a logger.warning
            # (non a logger.debug).
            idx = source.find(ctx)
            assert idx != -1, (
                f"Contesto cleanup mancante nel sorgente di "
                f"{method.__qualname__}: {ctx!r}"
            )
            # Guarda ~80 char indietro per trovare il livello di logger
            window = source[max(0, idx - 80):idx]
            assert "logger.warning" in window, (
                f"Atteso `logger.warning` prima del contesto {ctx!r} "
                f"in {method.__qualname__}; ottenuto: {window!r}"
            )
            assert "logger.debug" not in window, (
                f"Contesto {ctx!r} in {method.__qualname__} dovrebbe essere "
                f"promosso a warning, trovato ancora debug: {window!r}"
            )
