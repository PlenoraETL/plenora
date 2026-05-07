"""
Processore di variabili template per PLENORA-ETL.

Keeps this module's responsibility explicit and isolated from adjacent code.
"""

import re
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Any

from app.utils.logger import get_logger

logger = get_logger(__name__)

# Espressione regolare per riconoscere le variabili template
# Cattura: gruppo 1 = nome variabile (es. NOW), gruppo 2 = offset opzionale (es. +2h
TEMPLATE_REGEX = r"\{\{([A-Z]+)([+-]\d+[hdmsMHDMS])?\}\}"


# FORMATTAZIONE DATA/ORA

def format_datetime(dt: datetime) -> str:
    """Formatta come YYYY-MM-DD HH:MM (formato standard per le API)."""
    return dt.strftime("%Y-%m-%d %H:%M")


def format_date(dt: datetime) -> str:
    """Formatta solo la data come YYYY-MM-DD."""
    return dt.strftime("%Y-%m-%d")


def format_time(dt: datetime) -> str:
    """Formatta solo l'ora come HH:MM."""
    return dt.strftime("%H:%M")


# CALCOLO DATA BASE E OFFSET

def get_base_date(var_name: str, reference_date: datetime = None) -> datetime:
    """
    Restituisce la data/ora base per una variabile template.

    Args:
        var_name: Nome della variabile (NOW, TODAY, YESTERDAY, TOMORROW).
        reference_date: Data di riferimento anziché l'ora corrente (per test).

    Returns:
        Oggetto datetime corrispondente alla variabile.
    """
    now = reference_date or datetime.now(timezone.utc)

    if var_name == "NOW":
        return now
    elif var_name == "TODAY":
        return now.replace(hour=0, minute=0, second=0, microsecond=0)
    elif var_name == "YESTERDAY":
        ieri = now - timedelta(days=1)
        return ieri.replace(hour=0, minute=0, second=0, microsecond=0)
    elif var_name == "TOMORROW":
        domani = now + timedelta(days=1)
        return domani.replace(hour=0, minute=0, second=0, microsecond=0)
    else:
        # Per DATE, TIME, TIMESTAMP, YEAR, MONTH, DAY: usa ora corrente
        return now


def apply_offset(dt: datetime, offset_str: str) -> datetime:
    """
    Applica un offset temporale a una data.

    L'offset è una stringa nel formato ±NUNità (es. "+2h", "-30m", "-1d").
    Unità supportate (case-insensitive): s=secondi, m=minuti, h=ore, d=giorni.

    Args:
        dt: Data/ora di partenza.
        offset_str: Stringa offset (es. "+2h", "-30m"). Se None/vuoto, nessun effetto.

    Returns:
        Data/ora con l'offset applicato.
    """
    if not offset_str:
        return dt

    match = re.match(r"([+-])(\d+)([hdmsMHDMS])", offset_str)
    if not match:
        logger.warning(f"Offset template non valido: '{offset_str}' (formato atteso: ±N[hdms])")
        return dt

    segno, quantita, unita = match.groups()
    moltiplicatore = 1 if segno == "+" else -1
    valore = int(quantita) * moltiplicatore

    unita_lower = unita.lower()

    if unita_lower == "s":
        return dt + timedelta(seconds=valore)
    elif unita_lower == "m":
        return dt + timedelta(minutes=valore)
    elif unita_lower == "h":
        return dt + timedelta(hours=valore)
    elif unita_lower == "d":
        return dt + timedelta(days=valore)

    return dt


def format_value(dt: datetime, var_name: str) -> str:
    """
    Formatta la data/ora finale in base al tipo di variabile.

    Args:
        dt: Data/ora da formattare (con eventuali offset già applicati).
        var_name: Nome della variabile originale (determina il formato output).

    Returns:
        Stringa formattata secondo il tipo di variabile.
    """
    if var_name == "DATE":
        return format_date(dt)
    elif var_name == "TIME":
        return format_time(dt)
    elif var_name == "TIMESTAMP":
        return str(int(dt.timestamp() * 1000))  # Millisecondi Unix
    elif var_name == "YEAR":
        return str(dt.year)
    elif var_name == "MONTH":
        return f"{dt.month:02d}"
    elif var_name == "DAY":
        return f"{dt.day:02d}"
    else:
        # NOW, TODAY, YESTERDAY, TOMORROW → formato completo
        return format_datetime(dt)


# PROCESSAMENTO VARIABILI

def process_template_variables(text: str, reference_date: datetime = None) -> str:
    """
    Sostituisce tutte le variabili template in una stringa con i valori calcolati.

    Esempio:
        >>> process_template_variables("reftime:>={{NOW-2h}}")
        'reftime:>=2026-02-02 09:30'

    Args:
        text: Testo contenente variabili template {{...}}.
        reference_date: Data di riferimento (default: datetime.now(timezone.utc)).

    Returns:
        Testo con tutte le variabili sostituite. In caso di errore
        nella sostituzione di una variabile, mantiene la stringa originale.
    """
    if not text or not isinstance(text, str):
        return text

    def _sostituisci(match):
        nome_variabile = match.group(1)
        offset = match.group(2)

        try:
            dt = get_base_date(nome_variabile, reference_date)
            dt = apply_offset(dt, offset)
            return format_value(dt, nome_variabile)
        except Exception as e:
            # In caso di errore, mantieni la variabile originale non risolta
            logger.warning("Risoluzione variabile template %r fallita: %s", nome_variabile, e)
            return match.group(0)

    return re.sub(TEMPLATE_REGEX, _sostituisci, text)


def process_static_fields(
    fields: List[Dict[str, Any]],
    reference_date: datetime = None
) -> Dict[str, Any]:
    """
    Processa una lista di campi statici risolvendo le variabili template.

    Usato dal nodo StaticValues del canvas per generare i valori finali
    dei parametri prima dell'invio alle API o dell'inserimento nelle query.

    Esempio:
        >>> fields = [{'name': 'q', 'value': 'reftime:>={{NOW-2h}}'}]
        >>> process_static_fields(fields)
        {'q': 'reftime:>=2026-02-02 09:30'}

    Args:
        fields: Lista di campi [{'name': str, 'value': str}, ...].
        reference_date: Data di riferimento per le variabili.

    Returns:
        Dizionario {nome_campo: valore_con_variabili_risolte}.
    """
    risultato = {}

    for campo in fields:
        nome = campo.get("name")
        valore = campo.get("value", "")
        field_type = campo.get("field_type", "string")  # string | integer | float | boolean

        if not nome:
            continue

        processed = process_template_variables(valore, reference_date)

        # Cast al tipo esplicito definito dall'utente nel nodo
        try:
            if field_type == "integer":
                processed = int(float(processed)) if processed not in (None, "") else None
            elif field_type == "float":
                processed = float(processed) if processed not in (None, "") else None
            elif field_type == "boolean":
                processed = processed.lower() in ("true", "1", "si", "yes") if isinstance(processed, str) else bool(processed)
        except (ValueError, TypeError) as e:
            logger.debug("Cast tipo %r fallito per campo %r, mantenuto come stringa: %s", field_type, nome, e)

        risultato[nome] = processed

    return risultato


# CATALOGO VARIABILI (per documentazione e autocomplete frontend

AVAILABLE_VARIABLES = [
    {"name": "{{NOW}}",       "description": "Data/ora corrente",    "example": "2026-02-02 11:30"},
    {"name": "{{NOW-1h}}",    "description": "Un'ora fa",            "example": "2026-02-02 10:30"},
    {"name": "{{NOW-2h}}",    "description": "Due ore fa",           "example": "2026-02-02 09:30"},
    {"name": "{{NOW+1h}}",    "description": "Tra un'ora",           "example": "2026-02-02 12:30"},
    {"name": "{{NOW-1d}}",    "description": "Ieri stessa ora",      "example": "2026-02-01 11:30"},
    {"name": "{{TODAY}}",     "description": "Oggi alle 00:00",      "example": "2026-02-02 00:00"},
    {"name": "{{YESTERDAY}}", "description": "Ieri alle 00:00",      "example": "2026-02-01 00:00"},
    {"name": "{{TOMORROW}}",  "description": "Domani alle 00:00",    "example": "2026-02-03 00:00"},
    {"name": "{{DATE}}",      "description": "Solo data",            "example": "2026-02-02"},
    {"name": "{{TIME}}",      "description": "Solo ora",             "example": "11:30"},
    {"name": "{{TIMESTAMP}}", "description": "Unix timestamp (ms)",  "example": "1769504400000"},
    {"name": "{{YEAR}}",      "description": "Anno corrente",        "example": "2026"},
    {"name": "{{MONTH}}",     "description": "Mese corrente",        "example": "02"},
    {"name": "{{DAY}}",       "description": "Giorno corrente",      "example": "02"},
]
