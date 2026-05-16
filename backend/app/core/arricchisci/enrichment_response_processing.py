"""
Mixin RESPONSE_PROCESSING — elaborazione response API per

`EnrichmentExecutor`: extract output JSON + explode multi-row +
"""

from __future__ import annotations

import json
from typing import Any, List

from app.core.dataframes.tabular import set_cell_value
from app.shared.resources.json_path import (
    extract_json_value as _shared_extract_json_value,
    geojson_to_wkt as _shared_geojson_to_wkt,
)
from app.core.arricchisci.response_type_detector import ResponseTypeDetector
from app.core.arricchisci.types import JsonObject, JsonRows
from app.utils.logger import get_logger

# Keep the canonical enrichment logger name for existing log consumers.
logger = get_logger("app.core.arricchisci.enrichment_service")


def _geojson_to_wkt(geom: JsonObject) -> str:
    """Converte un dict geometry GeoJSON in WKT."""
    return _shared_geojson_to_wkt(geom)


class EnrichmentResponseProcessingMixin:
    """Mixin per `EnrichmentExecutor`: response → DataFrame rows
    transformation. Pure data transformation in-memory. Zero
    transaction, zero DB I/O.
    """

    @property
    def _response_type_detector(self) -> ResponseTypeDetector:
        detector = getattr(self, "_response_type_detector_collaborator", None)
        detected_types = self.detected_column_types
        if detector is None or detector.detected_column_types is not detected_types:
            detector = ResponseTypeDetector(detected_types)
            self._response_type_detector_collaborator = detector
        return detector

    def _extract_output(self, data: Any) -> JsonRows:
        """Estrae campi output dalla response. Restituisce SEMPRE una lista di dict.

        - Con output_mapping vuoto (auto-flatten, B-BE-27):
            - GeoJSON FeatureCollection -> lista con 1 dict per feature (esplosione)
            - Dict generico -> lista con 1 dict (chiavi di primo livello come colonne)
            - List -> lista di dict (1 dict per elemento se sono dict, altrimenti 1 dict con _response)
        - Con output_mapping configurato:
            - Path con [] o [*] (es. "features[].properties.x") -> multi-row, 1 dict per elemento
            - Path con livelli annidati diversi (es. "features[*].nome" + "features[*].valori[*].anno")
              -> prodotto cartesiano: 1 riga per ogni combinazione (feature, valore)
            - Altrimenti -> lista con 1 dict (comportamento standard)
        """
        if not self.output_mapping:
            return self._auto_flatten(data)

        def _norm(path: str) -> str:
            return path.replace("[*]", "[]") if path else path

        max_depth = 0
        normalized_mappings = []
        for mapping in self.output_mapping:
            path = _norm(mapping.get("path", ""))
            depth = path.count("[]")
            max_depth = max(max_depth, depth)
            normalized_mappings.append({
                "path": path,
                "column": mapping.get("column") or mapping.get("path", ""),
                "depth": depth,
            })

        if max_depth == 0:
            result = {}
            for m in normalized_mappings:
                result[m["column"]] = self._extract_json_value(data, m["path"])

            if result and all(v is None for v in result.values()):
                # Tutti None è legittimo quando il punto non ha dati per questo
                # servizio (es. Tiranti idrici NoData, Frana PAI fuori zona
                # NON fare fallback ad auto_flatten: scaricherebbe le chiavi
                # della struttura response (results, fields, fieldAliases, ecc
                # come colonne fantasma nel DataFrame. Ritorna la riga con None
                # DEBUG level: e' un evento normale (punti fuori area di copertura
                # e si ripete per ogni riga senza dati → rumore inutile a INFO
                logger.debug(
                    f"output_mapping ha prodotto tutti None per path={[m['path'] for m in normalized_mappings]}. "
                    f"Risposta top-level keys: {list(data.keys()) if isinstance(data, dict) else type(data).__name__}"
                )
            return [result]

        rows = []
        self._explode_rows(data, normalized_mappings, contexts=[data], rows=rows, current_depth=0, max_depth=max_depth)
        return rows if rows else [{}]

    def _explode_rows(
        self,
        current_data: Any,
        mappings: JsonRows,
        contexts: List[Any],
        rows: JsonRows,
        current_depth: int,
        max_depth: int,
    ) -> None:
        """Esplora ricorsivamente i livelli di [] producendo una riga per ogni foglia.

        contexts[k] = elemento corrente al livello k (0 = root, 1 = primo []-iterator, ...)
        Una volta raggiunto max_depth, costruisce una riga leggendo ogni mapping dal
        contesto corrispondente alla sua profondità.
        """
        if current_depth == max_depth:
            row = {}
            for m in mappings:
                path = m["path"]
                depth = m["depth"]
                if depth == 0:
                    suffix = path
                    ctx = contexts[0]
                else:
                    parts = path.split("[]", depth)
                    suffix = parts[-1].lstrip(".") if len(parts) > depth else ""
                    ctx = contexts[depth]
                row[m["column"]] = self._extract_json_value(ctx, suffix) if suffix else ctx
            rows.append(row)
            return

        next_base = None
        for m in mappings:
            if m["depth"] > current_depth:
                parts = m["path"].split("[]", current_depth + 1)
                next_base = parts[current_depth].rstrip(".")
                break

        if next_base is None:
            return

        items = self._extract_json_value(current_data, next_base) if next_base else current_data
        if not isinstance(items, list):
            return

        for item in items:
            new_contexts = contexts + [item]
            self._explode_rows(item, mappings, new_contexts, rows, current_depth + 1, max_depth)

    def _auto_flatten(self, data: Any) -> JsonRows:
        """Auto-flatten senza output_mapping: restituisce lista di righe.
        Popola self.detected_column_types con i tipi rilevati (es. geometry:point).
        """
        # GeoJSON FeatureCollection -> 1 riga per feature
        if isinstance(data, dict) and data.get("type") == "FeatureCollection":
            rows = []
            features = data.get("features", [])
            for feature in features:
                row = {}
                props = feature.get("properties") or {}
                for k, v in props.items():
                    # Fix: interi rimangono interi (pandas li upcastava a float con NaN
                    row[k] = int(v) if isinstance(v, float) and v == int(v) else v
                geom = feature.get("geometry")
                if geom:
                    row["geometry"] = _geojson_to_wkt(geom)
                    # Rileva tipo geometria
                    geom_type = geom.get("type", "").lower()
                    self._response_type_detector.detect_geometry_type("geometry", geom_type)
                if "id" in feature:
                    row.setdefault("id", feature["id"])
                rows.append(row)
            # Rileva tipi delle properties dalla prima feature
            if features:
                first_props = features[0].get("properties") or {}
                for k, v in first_props.items():
                    if k not in self.detected_column_types:
                        self.detected_column_types[k] = self._response_type_detector.infer_scalar_type(v)
                if "id" in features[0]:
                    self.detected_column_types.setdefault("id", "int")
            return rows if rows else [{}]

        # Lista di dict -> 1 riga per elemento
        if isinstance(data, list):
            if data and isinstance(data[0], dict):
                rows = [self._response_type_detector.flatten_dict(item) for item in data]
                # Rileva tipi dal primo elemento
                for k, v in data[0].items():
                    self.detected_column_types[k] = self._response_type_detector.infer_scalar_type(v)
                return rows
            return [{"_response": json.dumps(data, ensure_ascii=False)}]

        # Dict generico -> 1 riga, primo livello come colonne
        if isinstance(data, dict):
            for k, v in data.items():
                self.detected_column_types[k] = self._response_type_detector.infer_scalar_type(v)
            return [self._response_type_detector.flatten_dict(data)]

        return [{"_response": str(data)}]

    def _detect_geometry_type(self, col_name: str, geom_type: str) -> None:
        """Mappa tipo GeoJSON -> tipo sistema (geometry:point, geometry:polygon, ecc.)"""
        self._response_type_detector.detect_geometry_type(col_name, geom_type)

    def _infer_scalar_type(self, value: Any) -> str:
        """Inferisce tipo colonna da un valore scalare."""
        return self._response_type_detector.infer_scalar_type(value)

    def _flatten_dict(self, data: JsonObject) -> JsonObject:
        """Appiattisce un dict: valori scalari diretti, dict/list -> JSON string.
        Fix: interi floats (es 114367654.0) vengono convertiti a int.
        """
        return self._response_type_detector.flatten_dict(data)

    def _apply_result(self, df: Any, idx: int, result: JsonObject) -> None:
        """Applica risultato API a una riga del DataFrame."""
        for col_name, value in result.items():
            set_cell_value(df, idx, col_name, value)

    def _resolve_nested_list(self, data: Any, path: str) -> List[Any]:
        """Risolve un path con [] annidati, restituendo la lista "flat" di tutti
        gli elementi al livello più profondo.

        Es: path = 'features[].properties.valori'
            data = {features: [{properties: {valori: [1,2]}}, {properties: {valori: [3]}}]}
            → [1, 2, 3]

        Accetta sia [] che [*] come marker di iterazione.
        Se non ci sono marker, applica extract_json_value normale.
        """
        # Normalizza [*] →
        path = path.replace("[*]", "[]") if path else path
        if "[]" not in path:
            return self._extract_json_value(data, path)

        # Split sul primo [] per processare a strati
        prefix, _, rest = path.partition("[]")
        prefix = prefix.rstrip(".")
        rest = rest.lstrip(".")

        # Estrai la lista al livello corrente
        items = self._extract_json_value(data, prefix) if prefix else data
        if not isinstance(items, list):
            return []

        # Se non c'è altro dopo [], restituisci la lista
        if not rest:
            return items

        # Altrimenti, per ogni elemento, ricorsa e appiattisci
        result = []
        for item in items:
            sub = self._resolve_nested_list(item, rest)
            if isinstance(sub, list):
                result.extend(sub)
            else:
                result.append(sub)
        return result

    def _extract_json_value(self, data: Any, path: str) -> Any:
        """Estrae valore da JSON usando dot notation. Delegato a shared/api_utils."""
        return _shared_extract_json_value(data, path)
