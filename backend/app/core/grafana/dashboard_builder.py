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
  statistica «Righe totali» e, se c'e' una colonna testuale e un'altra
  colonna, grafico a barre del conteggio per quella colonna.
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
) -> JsonObject:
    """Dashboard di partenza dalle colonne dell'output (geometrie escluse)."""
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
    panels: list[dict[str, Any]] = [
        {"id": 1, "type": "table", "title": "Righe", "datasource": datasource, "targets": [query],
         "gridPos": {"x": 0, "y": 4, "w": 24, "h": 12}},
    ]
    if usable:
        panels.append({
            "id": 3, "type": "stat", "title": "Righe totali", "datasource": shared,
            "targets": [{"refId": "A", "datasource": shared, "panelId": 1}],
            "options": {"reduceOptions": {"calcs": ["count"], "fields": f"/^{re.escape(usable[0].name)}$/",
                                          "values": False}},
            "gridPos": {"x": 0, "y": 0, "w": 6, "h": 4},
        })
    count_column = next((col for col in usable if col.logical == "string"), None)
    other = next((col for col in usable if count_column is not None and col.name != count_column.name), None)
    if count_column is not None and other is not None:
        panels.append({
            "id": 2, "type": "barchart", "title": f"Conteggio per {count_column.name}", "datasource": shared,
            "targets": [{"refId": "A", "datasource": shared, "panelId": 1}],
            "transformations": [{"id": "groupBy", "options": {"fields": {
                count_column.name: {"operation": "groupby", "aggregations": []},
                other.name: {"operation": "aggregate", "aggregations": ["count"]},
            }}}],
            "options": {"xField": count_column.name},
            "gridPos": {"x": 6, "y": 0, "w": 18, "h": 4},
        })
    return {
        "uid": uid,
        "title": title,
        "tags": list(DASHBOARD_TAGS),
        "editable": True,
        "time": {"from": "now-1h", "to": "now"},
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
    "OutputColumnSpec",
    "PASS_HEADER",
    "build_dashboard",
    "dashboard_uid",
    "folder_uid",
    "missing_columns",
    "referenced_columns",
]
