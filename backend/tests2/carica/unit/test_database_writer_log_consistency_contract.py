"""Phase 2 err-dbwriter commit C — normalize log levels in cleanup guards + log-only points.

Commit log-only: nessun cambio di flusso, nessun cambio di classe
exception, nessun cambio di return value. Verifica che:

1. `_geometry_to_wkt` su WKB malformato emette un log prima del
   `return value` (pre-fix: zero log, causa del fallimento persa).
   Il contratto di return resta invariato (stringa raw passata
   al DB a valle).
2. `_geometry_to_wkt` usa il logger di modulo (pre-fix: GeoJSON
   branch usava `logging.getLogger(__name__)` ad hoc — inconsistente).
3. `_prepare_dataframe` su ImportError di geopandas emette un log
   (pre-fix: `pass` silenzioso — invisibile in prod).
4. I cleanup guards `logger.debug` sono stati promossi a `logger.warning`:
   cleanup che lascia stato orfano e' operator-visible, deve essere
   chiaramente segnalato.

Rif. audit Batch 2 §4.1 commit C (smell K, L, M, N, I parziale, E parziale log).

NOTA sulla strategia di test: il modulo usa un `StructuredLogger` custom
(wrapper in `app/utils/logger.py`) con handler custom PostgreSQL —
`caplog` di pytest non riesce a intercettarne gli emit in questo ambiente
di test. Quindi per i punti log-only intercettiamo direttamente
`dw_module.logger` sostituendolo con un `MagicMock`. Verifichiamo
livello + contenuto del messaggio sulle call_args del mock. Questo
testa esattamente l'intento del commit C (cosa logga, a quale livello).
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.core.carica import database_writer as dw_module
from app.core.carica.database_writer import DatabaseWriter


pytestmark = [pytest.mark.unit, pytest.mark.carica, pytest.mark.contract]


def _all_call_messages(mock_method) -> list[str]:
    """Raccoglie i messaggi formattati di tutte le call a un mock logger method.

    I log nel modulo usano sia `logger.debug("fmt %s", arg)` sia
    `logger.warning(f"...{e}")`. Per uniformare, ricostruisco il
    messaggio applicando gli args al format string quando presenti.
    """
    msgs: list[str] = []
    for call in mock_method.call_args_list:
        args, _kwargs = call
        if not args:
            continue
        fmt = args[0]
        rest = args[1:]
        if rest:
            try:
                msgs.append(fmt % rest)
            except Exception:
                msgs.append(str(fmt) + " " + " ".join(str(a) for a in rest))
        else:
            msgs.append(str(fmt))
    return msgs


# =============================================================================
# _geometry_to_wkt — log behavior
# =============================================================================


def test_geometry_to_wkt_logs_on_wkb_fallback_exhaustion(monkeypatch):
    """Pre-fix: `except Exception: return value` → zero log.
    Post-fix: log.debug prima di return value (contratto return invariato).
    """
    mock_logger = MagicMock()
    monkeypatch.setattr(dw_module, "logger", mock_logger)

    writer = object.__new__(DatabaseWriter)
    # Stringa hex invalida → wkb.loads solleva → ramo fallback
    malformed_wkb_hex = "ZZZZNOT_A_VALID_WKB_HEX"

    result = writer._geometry_to_wkt(malformed_wkb_hex)

    # Contratto return INVARIATO: ritorna il valore raw
    assert result == malformed_wkb_hex, (
        "Commit C e' log-only: il return value di _geometry_to_wkt "
        "deve restare identico al pre-fix (stringa raw)."
    )

    # Un log deve essere stato emesso prima del return: debug o warning.
    # Pre-fix: zero call sul logger di modulo.
    debug_msgs = _all_call_messages(mock_logger.debug)
    warning_msgs = _all_call_messages(mock_logger.warning)
    all_msgs = debug_msgs + warning_msgs
    assert all_msgs, (
        "Atteso almeno un log sul logger di modulo dopo WKB fail. "
        "Pre-fix: zero log."
    )
    # Almeno un messaggio riferisce esplicitamente al ramo WKB
    assert any("WKB" in m or "wkb" in m.lower() for m in all_msgs), (
        f"Atteso messaggio che menziona WKB, ottenuto: {all_msgs!r}"
    )


def test_geometry_to_wkt_geojson_uses_module_logger(monkeypatch):
    """Pre-fix: `logging.getLogger(__name__).warning(...)` — un logger
    creato ad hoc nella funzione, inconsistente col resto del file.
    Post-fix: usa `logger` (alias di modulo), stesso logger di tutto
    il file.

    Verifica: il warning su GeoJSON malformato viene instradato sul
    `logger` di modulo. Se pre-fix intercettassimo `dw_module.logger`,
    la call NON arriverebbe al mock — il warning andrebbe su un altro
    logger separato, stesso nome ma istanza diversa.
    """
    mock_logger = MagicMock()
    monkeypatch.setattr(dw_module, "logger", mock_logger)

    writer = object.__new__(DatabaseWriter)
    # Stringa che sembra GeoJSON (parte con '{') ma JSON invalido
    malformed_geojson = '{not valid json'

    result = writer._geometry_to_wkt(malformed_geojson)

    # Contratto: cade al ramo WKB che pure fallisce → return raw
    assert result == malformed_geojson

    warning_msgs = _all_call_messages(mock_logger.warning)
    assert warning_msgs, (
        "Atteso warning sul logger di modulo per GeoJSON fallito. "
        "Pre-fix: il log andava su un logger ad-hoc "
        "`logging.getLogger(__name__)` creato nella funzione — "
        "stesso nome ma istanza separata, inconsistente col pattern "
        "del file e invisibile se patchiamo il logger di modulo."
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


# =============================================================================
# _prepare_dataframe — log behavior su ImportError geopandas
# =============================================================================


def test_prepare_dataframe_logs_when_geopandas_missing(monkeypatch):
    """Pre-fix: `except ImportError: pass` — skip silenzioso.
    Post-fix: logger.debug informativo. Return invariato.

    Simulo geopandas mancante via monkeypatch su sys.modules: mettendo
    `None` come valore, Python solleva ImportError al prossimo
    `import geopandas` dentro _prepare_dataframe.
    """
    import sys
    import pandas as pd

    # Forza ImportError anche se geopandas e' gia' stato caricato
    # nel test runner
    monkeypatch.setitem(sys.modules, "geopandas", None)

    # Intercetta le call sul logger di modulo
    mock_logger = MagicMock()
    monkeypatch.setattr(dw_module, "logger", mock_logger)

    # Writer minimale: _prepare_dataframe non usa self.config per un
    # DF senza colonne geometriche complesse. Uso object.__new__.
    writer = object.__new__(DatabaseWriter)

    df = pd.DataFrame({"id": [1, 2], "name": ["a", "b"]})

    result = writer._prepare_dataframe(df)

    # Contratto return: DataFrame prepared esce comunque
    assert isinstance(result, pd.DataFrame)
    assert len(result) == 2

    # Log atteso sul modulo (debug, livello scelto dal commit C perche'
    # dependency opzionale: non e' un errore da urlare, ma neanche un
    # silenzio totale).
    debug_msgs = _all_call_messages(mock_logger.debug)
    assert any("geopandas" in m for m in debug_msgs), (
        f"Atteso log che menziona 'geopandas' quando la dependency manca. "
        f"Pre-fix: `pass` silenzioso → invisibile in prod. "
        f"Ottenuto debug calls: {debug_msgs!r}"
    )


# =============================================================================
# Cleanup guards log level — verifica che il modulo usa WARNING non DEBUG
# =============================================================================


def test_cleanup_guards_use_warning_not_debug_on_orphan_leftover():
    """Verifica statica sul sorgente: i sei cleanup guards elencati
    nell'audit §4.1 commit C sono promossi da `logger.debug` a
    `logger.warning`. Stateful log check e' fragile (i cleanup sono
    no-op in test senza DB reale); quindi assert testuale sul file.

    La ragione: cleanup che fallisce lascia stato orfano (tabella
    stg_/bak_/interim_ non droppata) visibile all'operatore nel
    DB target. Deve essere chiaramente segnalato, non relegato
    a debug-level (che in prod di default non viene emesso).
    """
    import inspect

    source = inspect.getsource(dw_module)

    expected_warning_contexts = [
        "[STAGE+SWAP] cleanup shadow post-CREATE fallito",
        "[REPLACE ATOMIC] cleanup difensivo orfani stg/bak fallito",
        "[REPLACE ATOMIC] cleanup shadow post-INSERT fail fallito",
        "[ORACLE REDEF] cleanup difensivo interim orfana fallito",
        "[ORACLE REDEF] cleanup interim post-START_REDEF fail fallito",
        "[ORACLE REDEF] drop interim post-ABORT fallito",
    ]

    for ctx in expected_warning_contexts:
        # Ogni contesto deve apparire associato a logger.warning
        # (non a logger.debug).
        idx = source.find(ctx)
        assert idx != -1, f"Contesto mancante nel sorgente: {ctx!r}"
        # Guarda ~80 char indietro per trovare il livello di logger
        window = source[max(0, idx - 80):idx]
        assert "logger.warning" in window, (
            f"Atteso logger.warning prima del contesto {ctx!r}; "
            f"ottenuto: {window!r}"
        )
        assert "logger.debug" not in window, (
            f"Contesto {ctx!r} dovrebbe essere promosso a warning, "
            f"trovato ancora debug: {window!r}"
        )
