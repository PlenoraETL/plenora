"""
Mixin TEMPLATE_PARAMS — input transformation per `EnrichmentExecutor`:
template parsing + params building + cache keying.

Origine
=======
Estratto da `app.core.arricchisci.enrichment_service.EnrichmentExecutor`
come parte di Phase 5 Batch B3-A.2 (cluster split + mixin pattern,
vedi `orchestrate/PHASE_5_ENTRY_PLAN.md` §3 P5-B3-A; mixin pattern
ratificato in B2.1+...+B2.6 per `DatabaseWriter` carica/).

Razionale del raggruppamento: 6 metodi che condividono il dominio
"input transformation per chiamata API":

  - `_is_template`             — staticmethod, detection placeholder
                                 `{col}` in stringa.
  - `_template_columns`        — staticmethod, estrae nomi colonna
                                 dal template (`re.findall`).
  - `_interpolate_template`    — staticmethod, sostituisce `{col}`
                                 con valori riga; gestisce numpy→native
                                 + null/NaN safe; ritorna None se
                                 colonna assente o valore null.
  - `_build_params`            — costruisce dict params API per riga:
                                 static_parameters + param_mapping
                                 (template o nome colonna) + API key
                                 query string. Chiama intra-cluster
                                 `_is_template` + `_interpolate_template`.
  - `_has_null_params`         — verifica presenza di parametri
                                 obbligatori nulli (escluso API key);
                                 usa definizioni `_has_param_definitions`
                                 / `_required_param_names`.
  - `_make_cache_key`          — staticmethod, genera chiave cache MD5
                                 deterministica da dict params (sorted +
                                 json.dumps + md5).

Cluster coeso: tutti "input transformation" pre-call API. 4 sono
staticmethod (puri, nessuna dipendenza su `self`); 2 sono instance
method che usano attributi di `EnrichmentExecutor.__init__`
(`api_config`, `param_mapping`, `auth`, `_has_param_definitions`,
`_required_param_names`).

Pattern: Mixin class via MRO
============================
`EnrichmentTemplateParamsMixin` viene ereditato da `EnrichmentExecutor`:

    class EnrichmentExecutor(EnrichmentTemplateParamsMixin, ...):
        ...

I 6 metodi restano accessibili come `EnrichmentExecutor.<method>` via
MRO. `inspect.getsource(EnrichmentExecutor.<method>)` legge il source
DA QUESTO FILE tramite `__code__.co_filename` + line numbers
(verificato pattern in B2). Le call-site esterne dal cluster
`validate_input` / `_execute_*` / `_group_by_params` continuano a
risolvere via MRO senza modifiche.

Coupling esterno
================
- `self.api_config`, `self.param_mapping`, `self.auth` — instance
  attrs di `EnrichmentExecutor.__init__`.
- `self._has_param_definitions`, `self._required_param_names` —
  instance attrs di `EnrichmentExecutor.__init__` (precomputed da
  api_config["parameters"] schema).
- `re`, `pd`, `np` (lazy import in `_interpolate_template` /
  `_build_params`) — top-level modules.
- `json`, `hashlib` — top-level modules per `_make_cache_key`.

Cluster zero-transaction, zero DB I/O. Pure data transformation
in-memory pre-API-call.

Coupling test contract
======================
Verificato pre-extraction (preflight B3-A.2):
  - 0 `patch.object(executor, "<method>", ...)` su questi 6 metodi.
  - 0 `inspect.getsource(EnrichmentExecutor.<method>)` o
    `inspect.getsource(enrichment_service)`.
  - 0 AST scan su `enrichment_service.py` per pattern in questi metodi.
  - 0 `monkeypatch.setattr` su attributi del modulo.

Estrazione safe per costruzione. Nessuna pre-modernization come
B2.0/B2.4a/B2.5a richiesta.

Logger naming contract
======================
`_interpolate_template` emette 2 `logger.debug` (pd.isna probe +
numpy conversion probe failures). Quindi il mixin DEVE definire
`logger`. Esplicito canonico
`get_logger("app.core.arricchisci.enrichment_service")` per:
  - Coerenza con pattern B2 (canonico esplicito vs `__name__`).
  - Singleton stdlib per nome: `__name__` di `enrichment_service.py`
    valuta esattamente a `"app.core.arricchisci.enrichment_service"`
    → STESSO logger stdlib underlying → stessa observability per
    record dal mixin e dal modulo principale.

Quindi NESSUN cambio behavior dei log records: chi attacca handler /
filtra per `"app.core.arricchisci.enrichment_service"` continua a
catturare gli emit indistintamente.

Il body sotto e' una copia BIT-PER-BIT delle implementazioni in
`enrichment_service.py` al momento di B3-A.2. Nessuna modifica di
logica, comment, log payload, ordine di operazione, regex pattern,
fallback null/NaN, numpy→native conversion, static_parameters
priority, API key query injection, MD5 cache key sorting.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Dict, List, Optional

import pandas as pd

from app.utils.logger import get_logger

# Stesso logger name di `enrichment_service.py` (`__name__` valuta a
# `"app.core.arricchisci.enrichment_service"`): record indistinguibili
# dai consumer che attaccano handler / filtrano per nome canonico.
# Esplicito vs `__name__` per coerenza pattern B2.
logger = get_logger("app.core.arricchisci.enrichment_service")


class EnrichmentTemplateParamsMixin:
    """Mixin per `EnrichmentExecutor`: template parsing + params building
    + cache keying. Pure data transformation in-memory pre-API-call.
    Zero transaction, zero DB I/O, zero side-effects diversi da
    `logger.debug` su pd.isna / numpy probe failures.
    """

    @staticmethod
    def _is_template(value: Any) -> bool:
        """True se il valore è una stringa con almeno un placeholder {colonna}."""
        return isinstance(value, str) and bool(re.search(r"\{(\w+)\}", value))

    @staticmethod
    def _template_columns(template: str) -> List[str]:
        """Estrae i nomi di colonna referenziati nel template."""
        return re.findall(r"\{(\w+)\}", template)

    @staticmethod
    def _interpolate_template(template: str, row: "pd.Series") -> Optional[str]:
        """Sostituisce {col} con i valori della riga.

        Ritorna None se almeno una colonna referenziata è null/NaN
        (così il chiamante può gestirla come parametro nullo).
        """
        out_parts: List[str] = []
        last_end = 0
        for m in re.finditer(r"\{(\w+)\}", template):
            col_name = m.group(1)
            out_parts.append(template[last_end:m.start()])
            if col_name not in row.index:
                # Colonna non esiste → consideriamo il template "non risolvibile"
                return None
            val = row[col_name]
            if val is None:
                return None
            try:
                if pd.isna(val) if not isinstance(val, (list, dict)) else False:
                    return None
            except Exception as _isna_err:
                logger.debug(
                    f"pd.isna probe fallito per tipo {type(val).__name__}: {_isna_err}"
                )
            # Converti numpy → Python nativo per str()
            try:
                import numpy as _np
                if isinstance(val, (_np.integer,)):
                    val = int(val)
                elif isinstance(val, (_np.floating,)):
                    val = float(val)
                elif isinstance(val, _np.bool_):
                    val = bool(val)
            except Exception as _np_err:
                logger.debug(
                    f"numpy->native conversion probe fallita per tipo "
                    f"{type(val).__name__}: {_np_err}"
                )
            # Formattazione: int/float/bool senza decoration, str raw
            out_parts.append(str(val))
            last_end = m.end()
        out_parts.append(template[last_end:])
        return "".join(out_parts)

    def _build_params(self, row: pd.Series) -> Dict[str, Any]:
        """
        Costruisce parametri API dalla riga DataFrame.

        Ordine di priorità (i successivi sovrascrivono i precedenti):
        1. static_parameters dalla config API (fissi per tutte le righe)
        2. Parametri dinamici dal param_mapping (colonna -> parametro API,
           oppure template stringa con placeholder {col})
        3. API key in query string (se configurata come query param)

        Sintassi del param_mapping value:
          - "nome_colonna"      → legge la colonna direttamente (default)
          - "{X},{Y}"           → template: interpola colonne X e Y
          - "address: {via}"    → template misto con prefisso fisso
        """
        params = {}

        # 1. Prima aggiungi static_parameters dalla config API
        static_params = self.api_config.get("static_parameters", {})
        if static_params:
            params.update(static_params)

        # 2. Poi aggiungi parametri dinamici dal mapping
        for api_param, mapping_value in self.param_mapping.items():
            if not mapping_value:
                continue

            # ── 2a. Template con placeholder {col} ──
            if self._is_template(mapping_value):
                interpolated = self._interpolate_template(mapping_value, row)
                if interpolated is None:
                    # Una delle colonne referenziate è null o non esiste
                    if self._has_param_definitions and api_param not in self._required_param_names:
                        continue
                    params[api_param] = None
                else:
                    params[api_param] = interpolated
                continue

            # ── 2b. Mapping classico: nome di una colonna ──
            if mapping_value not in row.index:
                continue
            value = row[mapping_value]
            if pd.isna(value) if not isinstance(value, (list, dict)) else False:
                # Parametro null: se è opzionale (e abbiamo definizioni dei parametri),
                # NON lo aggiungiamo affatto — così l'API riceve solo i campi presenti
                # e _has_null_params non lo segnala come bloccante.
                if self._has_param_definitions and api_param not in self._required_param_names:
                    continue
                params[api_param] = None
            elif isinstance(value, (bool, int, float)):
                # Preserva tipo nativo Python: {"id": 10} non diventa {"id": "10"}
                params[api_param] = value
            else:
                # numpy int64/float64 -> converti in tipo Python nativo
                import numpy as np
                if isinstance(value, (np.integer,)):
                    params[api_param] = int(value)
                elif isinstance(value, (np.floating,)):
                    params[api_param] = float(value)
                elif isinstance(value, np.bool_):
                    params[api_param] = bool(value)
                else:
                    params[api_param] = str(value)

        # 3. API Key in query string
        if self.auth.get("type") == "api_key" and self.auth.get("location") == "query":
            params[self.auth.get("key_name", "apiKey")] = self.auth.get("key_value", "")

        return params

    def _has_null_params(self, params: Dict) -> bool:
        """Verifica se ci sono parametri OBBLIGATORI nulli (esclusa API key).

        Se le definizioni dei parametri sono disponibili (api_config["parameters"]),
        controlla solo quelli con required=True. Se non lo sono, fallback al
        comportamento legacy che blocca su qualsiasi null.
        """
        api_key_name = self.auth.get("key_name", "apiKey") if self.auth.get("type") == "api_key" else None

        for key, value in params.items():
            if key == api_key_name:
                continue
            # Se abbiamo le definizioni dei parametri, ignora gli opzionali
            if self._has_param_definitions and key not in self._required_param_names:
                continue
            if value is None or value == "" or value == "None":
                return True
        return False

    @staticmethod
    def _make_cache_key(params: Dict) -> str:
        """Genera chiave cache da parametri."""
        # Ordina per consistenza
        sorted_items = sorted(params.items())
        param_str = json.dumps(sorted_items, sort_keys=True)
        return hashlib.md5(param_str.encode()).hexdigest()
