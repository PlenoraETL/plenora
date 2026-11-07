"""Testi di avanzamento che le esecuzioni mostrano all'utente (collaudo live L4).

Il messaggio di avanzamento di un task (``report_task_progress``) arriva cosi'
com'e' nella finestra dell'esecuzione, anche all'esecutore di /run. Prima i
testi erano scritti a mano nei worker, fuori dal glossario dell'interfaccia
(«Esecuzione pipeline...», «Preview completata», «Finalizzazione output...»,
id di Data Source, numeri non formattati).

Regola:
- ogni testo e' una riga di ``PROGRESS_MESSAGES``; i worker passano
  ``progress_text(chiave, ...)`` (o il testo di un errore gia' pronto in una
  variabile), mai una stringa scritta li': lo verifica
  ``tests2/contract/test_ui_collaudo_live_contract.py``;
- la tabella e' letta dal test dei termini del frontend
  (``frontend/tests/shared/terms.contract.test.js``): stesso glossario
  dell'interfaccia;
- niente id nei testi; i numeri passano da ``progress_text``, che li scrive
  come il frontend (``it-IT``: 238.044).
"""

from __future__ import annotations

from types import MappingProxyType
from typing import Any, Mapping

PROGRESS_MESSAGES: Mapping[str, str] = MappingProxyType({
    # Esecuzione di un progetto (workers/tasks/pipeline_task.py)
    "run_start": "Avvio dell'esecuzione...",
    "run_loading": "Lettura del progetto...",
    "run_working": "Elaborazione dei dati...",
    "run_saving": "Salvataggio del risultato...",
    "connection_lost": "Connessione al database persa: {detail}",
    # Anteprima (workers/tasks/pipeline_task.py)
    "preview_start": "Avvio dell'anteprima...",
    "preview_working": "Calcolo dell'anteprima...",
    "preview_done": "Anteprima pronta",
    # Carica (workers/tasks/carica_task.py, core/carica/dispatch.py)
    "load_start": "Avvio del caricamento...",
    "load_reading": "Lettura dei dati dell'Output...",
    "load_read": "Lette {rows} righe: scrittura in corso...",
    "load_busy": "La destinazione è già in scrittura da un altro caricamento: riprova quando sarà finito.",
    "load_serializing": "Preparazione dei dati ({format})...",
    "load_datasource": "Scrittura sul Data Source...",
    "load_rest_prepare": "Preparazione della scrittura REST...",
    "load_rest": "Scrittura REST...",
    "load_bundle": "Pacchetto con {layers} layer: pubblicazione su ArcGIS...",
    "load_arcgis_prepare":"Preparazione della scrittura su ArcGIS...",
    "load_arcgis": "Scrittura su ArcGIS ({operation})...",
    "load_arcgis_service": "Creazione del servizio «{service}»...",
    "load_arcgis_features": "Scrittura degli elementi ({operation})...",
    # Arricchisci (workers/tasks/enrichment_task.py, core/arricchisci/enrichment_service.py)
    "enrich_start": "Avvio dell'esecuzione...",
    "enrich_loading": "Lettura dei dati...",
    "enrich_ready": "Connessioni API pronte: inizio dell'elaborazione...",
    "enrich_source_project": "Lettura del progetto sorgente...",
    "enrich_source_file": "Lettura del file...",
    "enrich_static": "Lettura dei valori statici...",
    "enrich_step": "Elaborazione del passo...",
    "enrich_calls": "{api}: {processed} di {total} righe ({success} riuscite, {cached} dalla cache, {errors} errori)",
    "enrich_saving": "Salvataggio dei risultati...",
    "done": "Completato",
    "failed": "Errore: {detail}",
})


def _shown(value: Any) -> str:
    # Numeri come il frontend (formatNumber, it-IT): 238.044.
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, int):
        return f"{value:,}".replace(",", ".")
    return str(value)


def progress_text(key: str, **values: Any) -> str:
    """Il testo di avanzamento ``key`` con i valori al loro posto."""
    template = PROGRESS_MESSAGES[key]
    return template.format(**{name: _shown(value) for name, value in values.items()})
