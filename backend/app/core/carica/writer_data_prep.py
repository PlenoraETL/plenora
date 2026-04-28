"""
Mixin DATA_PREP — trasformazioni DataFrame/values per DatabaseWriter.

Origine
=======
Estratto da `app.core.carica.database_writer.DatabaseWriter` come parte
di Phase 5 Batch B2.1 (cluster split + mixin pattern, vedi
`orchestrate/PHASE_5_ENTRY_PLAN.md` §3 P5-B2 + §6.1 facade pattern
ratificato in B1).

Razionale del raggruppamento: 4 metodi che condividono il dominio
"trasformazione DataFrame/values per la scrittura DB" — pure data
transformation, **zero transaction semantics**, zero DB I/O:

  - `_prepare_dataframe`        — copia DF + fix tipi misti +
                                   pre-conversione GeoSeries → WKT
                                   (chiama `_fix_mixed_type_columns`
                                   intra-cluster).
  - `_fix_mixed_type_columns`   — detection colonne object con mix
                                   numeri+stringhe + cast a str
                                   per evitare DDL mismatch (mass
                                   coerce con preservazione NaN).
  - `_geometry_to_wkt`          — conversione valore geometria a WKT:
                                   stringa WKT pre-formato, GeoJSON via
                                   shapely.shape, WKB hex via shapely.wkb,
                                   shapely geometry via .wkt; fallback
                                   raw value se tutti i parser falliscono.
  - `_build_params`             — costruzione dict params per SQLAlchemy
                                   da una riga DataFrame: NaN→None,
                                   geometry→WKT, numpy scalar→native,
                                   datetime→ISO; UPDATE aggiunge
                                   `match_<col>` keys (chiama
                                   `_geometry_to_wkt` intra-cluster +
                                   legge `self.config.match_columns`).

Pattern: Mixin class
====================
`DataPrepMixin` viene ereditato da `DatabaseWriter`:

    class DatabaseWriter(DataPrepMixin, DestinationWriter):
        ...

I 4 metodi restano accessibili come `DatabaseWriter._<method>` via MRO.
`inspect.getsource(DatabaseWriter._prepare_dataframe)` legge il source
DA QUESTO FILE (verificato: `inspect` usa `__code__.co_filename` +
line numbers per risolvere). Le call-site `self._<method>(...)` nei
metodi che restano in `database_writer.py` continuano a risolvere via
MRO senza modifiche.

Coupling esterno
================
Solo `self.config.match_columns` (in `_build_params`, ramo UPDATE).
Nessuna dipendenza su `self.engine`, `self.db_type`, `self.ddl`,
`self.progress_callback`. Cluster zero-transaction, zero DB I/O —
sicuro per estrazione anche con vincoli transazionali del modulo
parent.

Logger naming contract
======================
Logger creato esplicitamente come `get_logger("app.core.carica.writer")`
(NON `__name__`) per preservare il contratto osservabile pre-extraction
e i test contract modernizzati B2.0
(`test_database_writer_log_consistency_contract.py`):

  - `test_geometry_to_wkt_logs_on_wkb_fallback_exhaustion`
  - `test_geometry_to_wkt_geojson_uses_module_logger`
  - `test_prepare_dataframe_logs_when_geopandas_missing`

Questi test catturano via handler agganciato al logger stdlib named
`"app.core.carica.writer"`. Il `StructuredLogger` wrapper di
`app/utils/logger.py` delega a `_logger.log(...)` sul logger stdlib
omonimo, quindi gli emit dal mixin (qui) e dal modulo parent
(`database_writer.py`) finiscono sullo stesso logger underlying →
handler cattura entrambi indistintamente. Nome canonico unico:
contract preservato per costruzione.

Il body sotto e' una copia BIT-PER-BIT delle implementazioni in
`database_writer.py` al momento di B2.1. Nessuna modifica di logica,
comment, log payload, ordine di operazione, fallback chain di
`_geometry_to_wkt`, mass-coerce di `_fix_mixed_type_columns`,
costruzione params di `_build_params`.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import pandas as pd

from app.core.carica.models import ColumnMapping
from app.utils.logger import get_logger

# Stesso logger name di `database_writer.py`: i record sono
# indistinguibili dai consumer che attaccano handler / filtrano per
# "app.core.carica.writer". Il `StructuredLogger` wrapper delega a
# `_logger.log(...)` sul logger stdlib omonimo (singleton per nome),
# quindi handler agganciati nei test B2.0 catturano gli emit dal mixin.
logger = get_logger("app.core.carica.writer")


class DataPrepMixin:
    """Mixin per `DatabaseWriter`: trasformazioni DataFrame/values
    pre-scrittura DB. Pure data transformation, zero transaction semantics.
    """

    # ========================================================================
    # PREPARAZIONE DATAFRAME
    # ========================================================================

    def _prepare_dataframe(self, df: pd.DataFrame) -> pd.DataFrame:
        """Prepara il DataFrame per la scrittura.

        - Rileva colonne con tipi misti (es. numeri + stringhe) e le converte a TEXT
        - Converte GeoSeries in colonne WKT string
        - Rinomina colonne secondo il mapping
        - Rimuove colonne skip
        """
        prepared = df.copy()

        # Rileva e correggi colonne con tipi misti
        prepared = self._fix_mixed_type_columns(prepared)

        # Converti GeoSeries in WKT
        try:
            import geopandas as gpd
            for col in prepared.columns:
                if isinstance(prepared[col], gpd.GeoSeries):
                    prepared[col] = prepared[col].apply(
                        lambda g: g.wkt if g is not None and not pd.isna(g) else None
                    )
        except ImportError:
            # geopandas e' dependency opzionale: se manca, le eventuali
            # GeoSeries nel DataFrame non vengono pre-convertite a WKT,
            # ma _geometry_to_wkt a valle gestisce comunque il caso.
            # Pre-fix: `pass` silenzioso — impossibile sapere in prod se
            # la conversione GeoSeries e' stata saltata.
            logger.debug(
                "[_prepare_dataframe] geopandas non disponibile, "
                "skip pre-conversione GeoSeries → WKT"
            )

        return prepared

    def _fix_mixed_type_columns(self, df: pd.DataFrame) -> pd.DataFrame:
        """Rileva colonne con tipi misti e le forza a str per evitare errori SQL.

        Caso tipico: colonna 'numero' con valori 95, 100, "26A" — pandas la tiene
        come object ma il DDLGenerator potrebbe mapparla a numeric guardando i primi valori.
        Forziamo tutte le colonne object con valori misti a str esplicito.
        """
        for col in df.columns:
            if df[col].dtype != 'object':
                continue

            # Colonna object: verifica se contiene mix di tipi
            non_null = df[col].dropna()
            if len(non_null) == 0:
                continue

            # Campiona per performance (max 1000 righe)
            sample = non_null.head(1000)
            has_numeric = False
            has_string = False

            for val in sample:
                if isinstance(val, (int, float)):
                    has_numeric = True
                elif isinstance(val, str):
                    has_string = True
                if has_numeric and has_string:
                    break

            if has_numeric and has_string:
                # Colonna mista: converti tutto a str per sicurezza (vectorized)
                logger.info(
                    f"Colonna '{col}' contiene tipi misti (numeri + stringhe), "
                    f"conversione a TEXT"
                )
                mask_null = df[col].isna()
                df[col] = df[col].astype(str)
                df.loc[mask_null, col] = None

        return df

    def _geometry_to_wkt(self, value) -> Optional[str]:
        """Converte un valore geometria in WKT."""
        if value is None:
            return None

        # Già stringa WKT
        if isinstance(value, str):
            val_upper = value.strip().upper()
            if any(val_upper.startswith(prefix) for prefix in (
                "POINT", "LINESTRING", "POLYGON", "MULTI", "GEOMETRY"
            )):
                return value.strip()
            # Stringa GeoJSON -> WKT tramite shapely
            if value.strip().startswith("{"):
                try:
                    import json as _json
                    from shapely.geometry import shape
                    return shape(_json.loads(value)).wkt
                except Exception as e:
                    # Usa il logger di modulo (prima: getLogger(__name__)
                    # ad hoc — inconsistente col resto del file).
                    # Il `pass` morto dopo il warning e' stato rimosso:
                    # era dead statement, cade naturalmente al ramo WKB.
                    logger.warning(f"GeoJSON->WKT fallito, provo WKB: {e}")
            # Potrebbe essere WKB hex
            try:
                from shapely import wkb
                geom = wkb.loads(bytes.fromhex(value))
                return geom.wkt
            except Exception as wkb_err:
                # Fallback esaurito: ritorna il valore raw (contratto
                # pubblico invariato — il DB sollevera' su geometria
                # malformata, ma almeno la causa originale e' tracciata
                # nel log. Pre-fix: nessun log.
                logger.debug(
                    "[_geometry_to_wkt] parse WKB fallito dopo GeoJSON, "
                    "restituisco valore raw: %s: %s",
                    type(wkb_err).__name__,
                    wkb_err,
                )
                return value

        # Shapely geometry
        if hasattr(value, 'wkt'):
            return value.wkt

        # Fallback
        return str(value)

    # ========================================================================
    # COSTRUZIONE PARAMETRI SQL
    # ========================================================================

    def _build_params(
        self,
        row: pd.Series,
        columns: List[ColumnMapping],
        operation: str,
    ) -> Dict[str, Any]:
        """Costruisce i parametri per una singola riga.

        Converte i valori dal DataFrame nel formato atteso da SQLAlchemy:
        - None/NaN -> None (SQL NULL)
        - Geometria GeoSeries -> WKT string
        - numpy scalar -> Python native
        - datetime -> string ISO
        """
        params = {}

        for col in columns:
            value = row.get(col.source_column)
            param_name = col.dest_column

            # Converti NaN/NaT in None
            if pd.isna(value) if not isinstance(value, (str, bytes)) else False:
                params[param_name] = None
                continue

            # Geometria -> WKT
            if col.is_geometry:
                params[param_name] = self._geometry_to_wkt(value)
                continue

            # numpy scalar -> Python native
            if hasattr(value, 'item'):
                value = value.item()

            # Timestamp/datetime -> ISO string (per compatibilità cross-DB)
            if hasattr(value, 'isoformat'):
                value = value.isoformat()

            params[param_name] = value

        # Per UPDATE: aggiungi parametri match con prefisso "match_"
        if operation == "update":
            for match_col in self.config.match_columns:
                value = row.get(match_col)
                if pd.isna(value) if not isinstance(value, (str, bytes)) else False:
                    params[f"match_{match_col}"] = None
                elif hasattr(value, 'item'):
                    params[f"match_{match_col}"] = value.item()
                else:
                    params[f"match_{match_col}"] = value

        return params
