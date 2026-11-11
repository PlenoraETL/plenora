"""Dashboard di partenza di un Eseguibile e colonne che una dashboard usa.

Dashboard generata (riferimento: spike ``setup_grafana.build_dashboard``):
- variabili textbox NASCOSTE (``hide=2``) ``exec`` (task) e ``pass``
  (lasciapassare), valorizzate dall'URL dell'iframe (``var-exec``,
  ``var-pass``);
- UNA sola query Infinity nel pannello tabella; il lasciapassare viaggia
  nell'intestazione ``X-Dashboard-Pass`` (``url_options.headers``, Infinity
  interpola le variabili anche li': provato dal vivo con Grafana 13.3 e
  Infinity 4.1.6), MAI nell'URL: ne' nella stringa ``url`` ne' nei parametri
  (finirebbe nel log di accesso del backend e nei log di Infinity);
- gli altri pannelli usano la stessa query (datasource «-- Dashboard --»):
  statistica «Righe totali» e, se c'e' una colonna adatta (regola sotto) e
  un'altra colonna, grafico a barre E torta del conteggio per quella colonna
  (trasformazione «Group by» + «Count», come la torta fatta a mano);
- controlli del tempo nascosti (``timepicker.hidden``): i dati non hanno un
  intervallo di tempo (correzioni minori 11/11/2026, B1). Per le dashboard
  gia' create vale anche il parametro ``_dash.hideTimePicker`` dell'iframe
  (``eseguibili.dashboard.iframe_path``), senza toccarle.

Colonna dei grafici di partenza (B2, tabella dichiarativa
:data:`CHART_COLUMN_RULES`, nell'ordine): esclusa se e' legata all'«Utente
che esegue» (``excluded``: la colonna filtrata dall'input, letta dal canvas;
e i nomi tipici dell'istituto: chi esegue ne vede un solo valore), se e' un
identificativo (id, codici univoci), una data o non e' testo; tra le restanti
vince la prima che corrisponde a un nome a poche categorie note
(:data:`CHART_PREFERRED_NAMES`, nell'ordine), altrimenti la prima rimasta;
nessuna rimasta = nessun grafico.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Optional, Sequence

from app.shared.contracts.payloads import JsonObject

DASHBOARD_TAGS = ("plenora", "generata")
_UID_SAFE = re.compile(r"[^A-Za-z0-9_-]")
GRAFANA_UID_MAX = 40
# Intestazione del lasciapassare nella richiesta di Infinity all'endpoint dati.
PASS_HEADER = "X-Dashboard-Pass"


@dataclass(frozen=True)
class OutputColumnSpec:
    name: str
    logical: str = "string"  # string | integer | number | boolean | datetime | geometry


# Tipi che possono fare da categoria in un grafico di conteggio.
CHART_LOGICAL_TYPES = frozenset({"string", "boolean"})
# Esclusioni (motivo, espressione sul nome in minuscolo): una colonna che
# corrisponde non e' mai la categoria dei grafici di partenza.
CHART_COLUMN_RULES: tuple[tuple[str, str], ...] = (
    ("utente che esegue", r"istitut|(^|_)ac_id$|(^|_)utente(_|$)|username|(^|_)user(_|$)|(^|_)owner(_|$)"),
    ("identificativo", r"(^|_)(id|uuid|guid|pk|key|chiave|cod|codice|code|matricola|progressivo)(_|$)|^id[a-z]"),
    ("data", r"(^|_)(data|date|datetime|ora|time|timestamp|anno|mese|giorno|dt)(_|$)"),
)
# Nomi a poche categorie note, in ordine di preferenza.
CHART_PREFERRED_NAMES: tuple[str, ...] = (
    r"catast",
    r"(^|_)stato(_|$)|(^|_)status(_|$)",
    r"(^|_)tipo|tipolog|(^|_)type(_|$)",
    r"categori",
    r"comun",
    r"provinc",
    r"region",
    r"classe|classif",
    r"destinaz|(^|_)uso(_|$)",
)


def chart_exclusion(column: OutputColumnSpec, excluded: frozenset[str] = frozenset()) -> Optional[str]:
    """Perche' ``column`` non fa da categoria dei grafici (None = puo')."""
    if column.name in excluded:
        return "utente che esegue"
    if column.logical not in CHART_LOGICAL_TYPES:
        return "tipo"
    name = column.name.lower()
    for reason, pattern in CHART_COLUMN_RULES:
        if re.search(pattern, name):
            return reason
    return None


def chart_column(
    columns: Sequence[OutputColumnSpec], excluded: Iterable[str] = (),
) -> Optional[OutputColumnSpec]:
    """La colonna categoria dei grafici di partenza (regola nel docstring)."""
    excluded_set = frozenset(excluded)
    candidates = [col for col in columns if chart_exclusion(col, excluded_set) is None]
    for pattern in CHART_PREFERRED_NAMES:
        for col in candidates:
            if re.search(pattern, col.name.lower()):
                return col
    return candidates[0] if candidates else None


def _bounded_uid(prefix: str, raw: str) -> str:
    safe = _UID_SAFE.sub("-", raw) or "x"
    uid = f"{prefix}{safe}"
    if len(uid) <= GRAFANA_UID_MAX:
        return uid
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]
    return f"{prefix}{safe[: GRAFANA_UID_MAX - len(prefix) - 13]}-{digest}"


def dashboard_uid(eseguibile_id: str) -> str:
    return _bounded_uid("plenora-", eseguibile_id)


def folder_uid(eseguibile_id: str) -> str:
    """Una cartella per Eseguibile: i permessi della cartella sono quelli
    della SOLA dashboard di quell'Eseguibile (``grafana.sync.FolderAccess``)."""
    return _bounded_uid("plenora-e-", eseguibile_id)


def _infinity_type(logical: str) -> str:
    if logical in {"integer", "number"}:
        return "number"
    if logical == "datetime":
        return "timestamp"
    return "string"


def build_dashboard(
    *,
    uid: str,
    title: str,
    columns: Sequence[OutputColumnSpec],
    data_url: str,
    datasource_uid: str,
    excluded_chart_columns: Iterable[str] = (),
) -> JsonObject:
    """Dashboard di partenza dalle colonne dell'output (geometrie escluse).
    ``excluded_chart_columns``: colonne legate all'«Utente che esegue» lette
    dal canvas (mai categoria dei grafici)."""
    datasource = {"type": "yesoreyeram-infinity-datasource", "uid": datasource_uid}
    usable = [col for col in columns if col.name and col.logical != "geometry"]
    query: dict[str, Any] = {
        "refId": "A",
        "datasource": datasource,
        "type": "json",
        "source": "url",
        "format": "table",
        "parser": "backend",
        "url": f"{data_url.rstrip('/')}/${{exec}}/rows",
        "url_options": {"method": "GET", "headers": [{"key": PASS_HEADER, "value": "${pass}"}]},
        "root_selector": "",
        "columns": [
            {"selector": col.name, "text": col.name, "type": _infinity_type(col.logical)} for col in usable
        ],
    }
    shared = {"type": "datasource", "uid": "-- Dashboard --"}
    count_column = chart_column(usable, excluded_chart_columns)
    other = next((col for col in usable if count_column is not None and col.name != count_column.name), None)
    charts = count_column is not None and other is not None
    top = 8 if charts else 4
    panels: list[dict[str, Any]] = [
        {"id": 1, "type": "table", "title": "Righe", "datasource": datasource, "targets": [query],
         "gridPos": {"x": 0, "y": top, "w": 24, "h": 12}},
    ]
    if usable:
        panels.append({
            "id": 3, "type": "stat", "title": "Righe totali", "datasource": shared,
            "targets": [{"refId": "A", "datasource": shared, "panelId": 1}],
            "options": {"reduceOptions": {"calcs": ["count"], "fields": f"/^{re.escape(usable[0].name)}$/",
                                          "values": False}},
            "gridPos": {"x": 0, "y": 0, "w": 4 if charts else 6, "h": top},
        })
    if charts:
        assert count_column is not None and other is not None
        group_by = [{"id": "groupBy", "options": {"fields": {
            count_column.name: {"operation": "groupby", "aggregations": []},
            other.name: {"operation": "aggregate", "aggregations": ["count"]},
        }}}]
        panels.append({
            "id": 2, "type": "barchart", "title": f"Conteggio per {count_column.name}", "datasource": shared,
            "targets": [{"refId": "A", "datasource": shared, "panelId": 1}],
            "transformations": group_by,
            "options": {"xField": count_column.name},
            # La legenda dice «Righe», non «<altra colonna> (count)».
            "fieldConfig": {"defaults": {"displayName": "Righe"}, "overrides": []},
            "gridPos": {"x": 4, "y": 0, "w": 10, "h": top},
        })
        panels.append({
            "id": 4, "type": "piechart", "title": f"Ripartizione per {count_column.name}", "datasource": shared,
            "targets": [{"refId": "A", "datasource": shared, "panelId": 1}],
            "transformations": [dict(item) for item in group_by],
            "options": {
                "reduceOptions": {"values": True, "calcs": ["lastNotNull"], "fields": ""},
                "pieType": "pie",
                "displayLabels": ["name", "percent"],
                "legend": {"displayMode": "list", "placement": "right", "showLegend": True},
            },
            "gridPos": {"x": 14, "y": 0, "w": 10, "h": top},
        })
    return {
        "uid": uid,
        "title": title,
        "tags": list(DASHBOARD_TAGS),
        "editable": True,
        "time": {"from": "now-1h", "to": "now"},
        # B1: nessun intervallo di tempo da scegliere (i dati sono di UNA esecuzione).
        "timepicker": {"hidden": True},
        "templating": {"list": [
            {"name": "pass", "type": "textbox", "hide": 2, "query": "", "current": {"value": ""}},
            {"name": "exec", "type": "textbox", "hide": 2, "query": "", "current": {"value": ""}},
        ]},
        "panels": panels,
        "schemaVersion": 41,
    }


_ANCHORED_NAME = re.compile(r"^/\^(.+)\$/$")


def _unescape_regex(text: str) -> str:
    return re.sub(r"\\(.)", r"\1", text)


def referenced_columns(dashboard: Optional[Mapping[str, Any]]) -> list[str]:
    """Nomi colonna che la dashboard usa: selettori Infinity, campi delle
    trasformazioni ``groupBy``/``organize``, ``xField``, campi del riduttore
    (``/^nome$/``) e override per nome. Ordine di prima apparizione."""
    found: list[str] = []

    def add(name: object) -> None:
        if isinstance(name, str) and name and name not in found:
            found.append(name)

    def visit_panel(panel: Mapping[str, Any]) -> None:
        for target in panel.get("targets") or []:
            if isinstance(target, Mapping):
                for column in target.get("columns") or []:
                    if isinstance(column, Mapping):
                        add(column.get("selector"))
        for transformation in panel.get("transformations") or []:
            options = transformation.get("options") if isinstance(transformation, Mapping) else None
            if isinstance(options, Mapping):
                fields = options.get("fields")
                if isinstance(fields, Mapping):
                    for name in fields:
                        add(name)
        options = panel.get("options")
        if isinstance(options, Mapping):
            add(options.get("xField"))
            reduce_options = options.get("reduceOptions")
            if isinstance(reduce_options, Mapping):
                fields = reduce_options.get("fields")
                match = _ANCHORED_NAME.match(fields) if isinstance(fields, str) else None
                if match:
                    add(_unescape_regex(match.group(1)))
        field_config = panel.get("fieldConfig")
        if isinstance(field_config, Mapping):
            for override in field_config.get("overrides") or []:
                matcher = override.get("matcher") if isinstance(override, Mapping) else None
                if isinstance(matcher, Mapping) and matcher.get("id") == "byName":
                    add(matcher.get("options"))
        for child in panel.get("panels") or []:  # righe
            if isinstance(child, Mapping):
                visit_panel(child)

    for panel in (dashboard or {}).get("panels") or []:
        if isinstance(panel, Mapping):
            visit_panel(panel)
    return found


def missing_columns(dashboard: Optional[Mapping[str, Any]], expected: Iterable[str]) -> list[str]:
    expected_set = set(expected)
    return [name for name in referenced_columns(dashboard) if name not in expected_set]


__all__ = [
    "CHART_COLUMN_RULES",
    "CHART_LOGICAL_TYPES",
    "CHART_PREFERRED_NAMES",
    "OutputColumnSpec",
    "chart_column",
    "chart_exclusion",
    "PASS_HEADER",
    "build_dashboard",
    "dashboard_uid",
    "folder_uid",
    "missing_columns",
    "referenced_columns",
]
