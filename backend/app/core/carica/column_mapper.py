"""
Auto-mapping colonne sorgente → destinazione per il modulo CARICA.

Keeps database loading behavior isolated from orchestration callers.
"""

import re
from collections.abc import Mapping
from typing import Any, List, Optional, Tuple

from app.core.carica.models import ColumnMapping
from app.core.dataframes.schema import series_to_canvas_type
from app.core.dataframes.tabular import non_null_head_values
from app.core.dataframes.values import is_geometry_series
from app.core.resources.column_types import normalize_canvas_type
from app.shared.contracts.payloads import StringMap, StringRows


# Nomi colonna che indicano una geometria (case-insensitive
GEOMETRY_COLUMN_NAMES = {
    "geometry", "geom", "the_geom", "shape", "wkb_geometry",
    "geo", "spatial", "location", "coordinates",
}

# Tipi canvas PLENORA che indicano una geometria
GEOMETRY_URBY_TYPES = {
    "geometry", "geometry:point", "geometry:line", "geometry:polygon",
    "geometry:multipoint", "geometry:multiline", "geometry:multipolygon",
}


class ColumnMapper:
    """Gestisce il mapping colonne sorgente → destinazione.

    Attributes:
        db_type: tipo database destinazione
    """

    def __init__(self, db_type: str):
        self.db_type = db_type.lower()

    def auto_map(
        self,
        df: Any,
        existing_columns: Optional[StringRows] = None,
        canvas_types: Optional[StringMap] = None,
        srid: int = 4326,
    ) -> List[ColumnMapping]:
        """Genera automaticamente il mapping colonne.

        Strategia:
        1. Per ogni colonna del DataFrame sorgente
        2. Se esiste una colonna con lo stesso nome nella tabella destinazione → match
        3. Se no → proponi colonna nuova con nome sanitizzato
        4. Rileva automaticamente la colonna geometria

        Args:
            df: DataFrame sorgente (output del nodo Result)
            existing_columns: colonne già presenti nella tabella destinazione
                              [{"name": "col", "type": "INTEGER"}, ...]
            canvas_types: tipi dal canvas PLENORA {"col_name": "geometry:point:4326"}
            srid: SRID di default per le geometrie

        Returns:
            Lista di ColumnMapping con mapping proposto.
        """
        existing_columns = existing_columns or []
        canvas_types = canvas_types or {}

        # Costruisci lookup tabella esistente
        existing_map = {
            col["name"].lower(): col for col in existing_columns
        }

        mappings = []
        geometry_found = False

        for col_name in df.columns:
            # Determina se è una geometria
            is_geom = self._is_geometry_column(
                col_name, df[col_name], canvas_types.get(col_name, "")
            )

            # Estrai SRID dal tipo canvas (es. "geometry:point:4326" → 4326
            col_srid = self._extract_srid(canvas_types.get(col_name, ""), srid)

            # Nome destinazione: match per nome o sanitizzato
            dest_name = self._find_matching_column(col_name, existing_map)
            if dest_name is None:
                dest_name = self._sanitize_column_name(col_name)

            # Tipo destinazione
            if is_geom and not geometry_found:
                dest_type = "geometry"
                geometry_found = True
            elif col_name.lower() in existing_map:
                dest_type = existing_map[col_name.lower()].get("type", "")
            else:
                dest_type = self._infer_type_from_canvas(
                    canvas_types.get(col_name, "")
                ) or self._infer_type_from_pandas(df[col_name])

            mappings.append(ColumnMapping(
                source_column=col_name,
                dest_column=dest_name,
                dest_type=dest_type,
                is_geometry=is_geom and not (geometry_found and is_geom is False),
                srid=col_srid if is_geom else None,
                is_primary_key=False,
                skip=False,
            ))

            if is_geom:
                geometry_found = True

        return mappings

    def validate_mapping(
        self,
        mappings: List[ColumnMapping],
        df: Any,
    ) -> Tuple[bool, List[str]]:
        """Valida un mapping colonne.

        Verifica:
        - Tutte le colonne sorgente esistono nel DataFrame
        - Nessun nome destinazione duplicato
        - Almeno una colonna non-skip
        - Match columns esistono (per UPDATE/UPSERT)
        - Massimo una colonna geometria

        Returns:
            Tupla (is_valid, lista_errori).
        """
        errors = []
        df_columns = set(df.columns)
        dest_names = []
        geom_count = 0

        active_mappings = [m for m in mappings if not m.skip]

        if not active_mappings:
            errors.append("Nessuna colonna attiva nel mapping (tutte skip).")

        for m in mappings:
            if m.skip:
                continue

            # Colonna sorgente esiste?
            if m.source_column not in df_columns:
                errors.append(
                    f"Colonna sorgente '{m.source_column}' non trovata nel DataFrame."
                )

            # Duplicati destinazione
            if m.dest_column in dest_names:
                errors.append(
                    f"Nome destinazione '{m.dest_column}' duplicato."
                )
            dest_names.append(m.dest_column)

            # Conteggio geometrie
            if m.is_geometry:
                geom_count += 1

        if geom_count > 1:
            errors.append(
                f"Trovate {geom_count} colonne geometria. "
                f"Massimo una colonna geometria supportata."
            )

        return len(errors) == 0, errors

    def suggest_match_columns(
        self,
        mappings: List[ColumnMapping],
        existing_columns: Optional[StringRows] = None,
    ) -> List[str]:
        """Suggerisce le colonne per il match (UPDATE/UPSERT).

        Euristica:
        1. Se la tabella ha PK → usa quelle
        2. Se ci sono colonne con "id" nel nome → suggerisci quelle
        3. Altrimenti → lista vuota (l'utente deve scegliere)
        """
        candidates = []

        for m in mappings:
            if m.skip or m.is_geometry:
                continue
            name_lower = m.dest_column.lower()
            # Euristica: colonne che sembrano ID/chiave
            if name_lower in ("id", "pk", "key"):
                candidates.insert(0, m.dest_column)
            elif name_lower.endswith("_id") or name_lower.startswith("id_"):
                candidates.append(m.dest_column)
            elif "codice" in name_lower or "code" in name_lower:
                candidates.append(m.dest_column)

        return candidates[:3]  # max 3 suggerimenti

    # RILEVAMENTO GEOMETRIA

    def _is_geometry_column(
        self,
        col_name: str,
        series: Any,
        canvas_type: str = "",
    ) -> bool:
        """Determina se una colonna contiene dati geometrici.

        Controlla in ordine:
        1. Tipo canvas PLENORA (geometry:*)
        2. Nome colonna noto (geom, geometry, shape, ...)
        3. Tipo dati Pandas (GeoSeries)
        4. Contenuto valori (WKT/WKB pattern)
        """
        # 1. Tipo canvas
        if canvas_type:
            base_type = canvas_type.split(":")[0].lower()
            if base_type == "geometry":
                return True

        # 2. Nome colonna
        if col_name.lower() in GEOMETRY_COLUMN_NAMES:
            return True

        # 3. GeoSeries-like values.
        if is_geometry_series(series):
            return True

        # 4. Contenuto WKT (campione) — usa shapely per validare geometrie reali
        # Più robusto della regex: "POINT" fallisce, "POINT(9 45)" passa
        try:
            from shapely import wkt as _wkt
            sample = non_null_head_values(series, 5)
            wkt_count = 0
            for val in sample:
                try:
                    geom = _wkt.loads(str(val).strip())
                    if geom and not geom.is_empty:
                        wkt_count += 1
                except Exception:
                    pass  # non è WKT valido
            # Richiedi che la maggioranza dei campioni sia geometria
            if wkt_count > 0 and wkt_count >= len(sample) * 0.5:
                return True
        except (TypeError, ValueError):
            pass

        return False

    def _extract_srid(self, canvas_type: str, default: int = 4326) -> int:
        """Estrae SRID dal tipo canvas (es. "geometry:point:4326" → 4326)."""
        if not canvas_type:
            return default
        parts = canvas_type.split(":")
        if len(parts) >= 3:
            try:
                return int(parts[2])
            except (ValueError, IndexError):
                pass
        return default

    def _infer_type_from_canvas(self, canvas_type: str) -> str:
        """Converte tipo canvas PLENORA in tipo interno per il mapping.

        Delega a `app.core.resources.column_types.normalize_canvas_type`, fonte
        unica del vocabolario canonico.
        """
        return normalize_canvas_type(canvas_type)

    def _infer_type_from_pandas(self, series: Any) -> str:
        """Converte dtype Pandas in tipo interno PLENORA.

        Delega al boundary dataframe, che mantiene la dispatch engine->canonical.
        """
        return series_to_canvas_type(series)

    def _find_matching_column(
        self,
        source_name: str,
        existing_map: Mapping[str, StringMap],
    ) -> Optional[str]:
        """Trova una colonna esistente che matcha per nome.

        Match case-insensitive. Se trovata, restituisce il nome originale
        (preservando il case della tabella esistente).
        """
        key = source_name.lower()
        if key in existing_map:
            return existing_map[key]["name"]
        return None

    def _sanitize_column_name(self, name: str) -> str:
        """Sanitizza un nome colonna per il database destinazione.

        - Rimuove caratteri speciali
        - Sostituisce spazi con underscore
        - Converte in lowercase (tranne Oracle che usa uppercase)
        - Tronca a 30 caratteri (limite Oracle)
        """
        # Rimuovi caratteri non alfanumerici (tranne underscore
        sanitized = re.sub(r'[^\w]', '_', name)
        # Rimuovi underscore multipli
        sanitized = re.sub(r'_+', '_', sanitized)
        # Rimuovi underscore iniziali/finali
        sanitized = sanitized.strip('_')

        # Case
        if self.db_type == "oracle":
            sanitized = sanitized.upper()
        else:
            sanitized = sanitized.lower()

        # Tronca (Oracle ha limite 30, altri 128+
        max_len = 30 if self.db_type == "oracle" else 128
        if len(sanitized) > max_len:
            sanitized = sanitized[:max_len]

        # Se vuoto dopo sanitizzazione
        if not sanitized:
            sanitized = "column_0"

        return sanitized
