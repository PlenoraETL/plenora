"""Valida di un passo Manipola = esecuzione a vuoto della stessa operazione.

Regola unica (A3, P1+P2): per ogni ingresso si costruisce un frame con ZERO
righe, le colonne e i tipi dello schema inferito (nell'ordine di esecuzione
degli ingressi) e si chiama lo stesso percorso dell'esecuzione reale
(``PluginExecutor.execute_node``: risoluzione dei riferimenti per ID,
normalizzazione dei parametri, preparazione e funzione dell'operazione;
``ManipolaExecutor._esegui_multi_join`` per il Multi-Join). Un'eccezione
sollevata è un errore di Valida con lo stesso messaggio pubblico.

Nessuna regola di configurazione duplicata: ciò che l'esecuzione rifiuta,
Valida lo rifiuta perché esegue lo stesso codice.

Esiti:

- ``verified``: l'esecuzione a vuoto è riuscita (la configurazione è accettata
  dal percorso reale per questo schema);
- ``rejected``: l'esecuzione a vuoto rifiuta la configurazione → errore di
  Valida con il messaggio del runtime. È un avviso se i nomi a monte sono
  incerti, oppure se il rifiuto sparisce rilassando ciò che lo schema non
  garantisce: tipi incerti (colonne ``object``) o CRS ignoto (EPSG:4326),
  ``data_dependent``;
- ``not_guaranteed``: l'operazione è dichiarata in ``VUOTO_NON_GARANTIBILE``
  o in ``CON_IO_ESTERNO`` e non viene eseguita a vuoto. Mai un errore spurio.

Errori che dipendono dai valori (cardinalità, contenuto, CRS reali, memoria)
non compaiono su zero righe: restano «non garantibili», non sono errori di
Valida.

I dtype vengono da una sola mappatura (``canvas_type_to_pandas_dtype``); le
colonne geometriche diventano ``GeoSeries`` vuote con il CRS dello schema
(``geometry:<sottotipo>:<srid>``) se noto: è la mappatura inversa di
``build_columns_with_types``, quella con cui le fasi A1/A2 hanno reso i tipi
inferiti uguali a quelli dell'esecuzione.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from app.core.dataframes.geodata import empty_frame_from_schema, schema_crs
from app.core.manipola.executor import ManipolaExecutor
from app.core.manipola.plugin_executor import get_executor
from app.core.manipola.types import JsonRows, StringMap
from app.core.resources.column_types import normalize_canvas_type


# Operazioni la cui esecuzione a vuoto non prova la configurazione: {op: motivo}.
# Esito "non garantibile", mai un errore. Le scorciatoie sul vuoto elencate
# qui sono la prova runtime su cui poggia l'incertezza dichiarata in A1
# (tests2/contracts/test_a1_uncertainty_witnesses.py): toglierle cambierebbe
# lo schema a vuoto e insieme l'inferenza, fuori dal perimetro di A3.
_EMPTY_SHORTCUT = "Su zero righe l'operazione restituisce l'ingresso invariato prima di leggere i parametri"
VUOTO_NON_GARANTIBILE: StringMap = {
    "transpose": _EMPTY_SHORTCUT,
    "reorder_columns": _EMPTY_SHORTCUT,
    "string_pad": _EMPTY_SHORTCUT,
    "string_length": _EMPTY_SHORTCUT,
    "window_function": _EMPTY_SHORTCUT,
    "uuid_generator": _EMPTY_SHORTCUT,
    # Scorciatoie di HEAD ripristinate (regola madre: l'esecuzione su zero
    # righe resta quella di HEAD; senza, ``sample`` stratificato falliva).
    "sample": _EMPTY_SHORTCUT,
    "dedup_advanced": _EMPTY_SHORTCUT,
    "geo_nearest": "Con un ingresso vuoto l'operazione restituisce il sinistro invariato prima di leggere i parametri",
    "geo_count_points_in_polygons": "Con un ingresso vuoto l'operazione restituisce i poligoni con il solo conteggio, senza leggere gli altri parametri",
}

# Operazioni con I/O esterno (rete, DB, file, API): mai eseguite a vuoto.
# Nessuna delle 71 operazioni del registry Manipola fa chiamate esterne.
CON_IO_ESTERNO: StringMap = {}


@dataclass(frozen=True)
class DryRunOutcome:
    """Esito dell'esecuzione a vuoto di un passo."""

    verdict: str  # "verified" | "rejected" | "not_guaranteed"
    message: str = ""
    result: Any = None
    # Rifiuto che sparisce quando si rilassa ciò che lo schema non sa (tipi
    # incerti → colonne ``object``; CRS ignoto → EPSG:4326): dipende dai dati.
    data_dependent: bool = False


def is_geometry_type(canvas_type: Any) -> bool:
    return normalize_canvas_type(canvas_type if isinstance(canvas_type, str) else None) == "geometry"


ASSUMED_CRS = "EPSG:4326"


def _input_columns(inputs: Sequence[Any]) -> JsonRows:
    """``inputColumns`` come li costruisce l'esecutore: concatenazione per ingresso."""
    return [dict(col) for schema in inputs for col in schema.columns if isinstance(col, Mapping)]


def _with_input_columns(node: Any, inputs: Sequence[Any]) -> Any:
    data = getattr(node, "data", None)
    data = dict(data) if isinstance(data, dict) else {}
    data["inputColumns"] = _input_columns(inputs)
    if hasattr(node, "model_copy"):
        return node.model_copy(update={"data": data})
    clone = type("DryRunNode", (), {})()
    clone.__dict__.update(vars(node))
    clone.data = data
    clone.config = getattr(node, "config", None)
    return clone


def _attempt(call: Any, inputs: Sequence[Any], *, relax_types: bool = False,
             assumed_crs: str | None = None) -> tuple[Any, str | None]:
    frames = [empty_frame_from_schema(schema.columns, relax_types=relax_types, assumed_crs=assumed_crs)
              for schema in inputs]
    try:
        return call(frames), None
    except Exception as error:  # broad intenzionale: qualunque rifiuto del percorso reale è l'esito di Valida
        return None, str(error)


def dry_run(operation: str, inputs: Sequence[Any], call: Any) -> DryRunOutcome:
    """Esegue ``call(frames)`` a vuoto e classifica l'esito secondo la regola."""
    if operation in CON_IO_ESTERNO:
        return DryRunOutcome("not_guaranteed", CON_IO_ESTERNO[operation])
    if operation in VUOTO_NON_GARANTIBILE:
        return DryRunOutcome("not_guaranteed", VUOTO_NON_GARANTIBILE[operation])
    result, message = _attempt(call, inputs)
    if message is None:
        return DryRunOutcome("verified", result=result)
    # Ciò che lo schema non garantisce e che può decidere un rifiuto.
    relax_types = any(getattr(schema, "types_status", None) == "indeterminate" for schema in inputs)
    unknown_crs = any(is_geometry_type(col.get("type")) and not schema_crs(col.get("type"))
                      for schema in inputs for col in schema.columns if isinstance(col, Mapping))
    data_dependent = (relax_types or unknown_crs) and _attempt(
        call, inputs, relax_types=relax_types, assumed_crs=ASSUMED_CRS if unknown_crs else None)[1] is None
    return DryRunOutcome("rejected", message, data_dependent=data_dependent)


def dry_run_transform(node: Any, inputs: Sequence[Any], operation: str) -> DryRunOutcome:
    """Esecuzione a vuoto di un nodo transform con il percorso reale del plugin."""
    prepared = _with_input_columns(node, inputs)
    executor = get_executor()
    return dry_run(operation, inputs, lambda frames: executor.execute_node(operation, frames, prepared))


def dry_run_multi_join(node: Any, inputs: Sequence[Any]) -> DryRunOutcome:
    """Esecuzione a vuoto del Multi-Join con lo stesso metodo dell'esecutore."""
    prepared = _with_input_columns(node, inputs)
    executor = ManipolaExecutor(project_service=None)
    return dry_run("multi_join", inputs, lambda frames: executor._esegui_multi_join(prepared, frames))


__all__ = [
    "CON_IO_ESTERNO",
    "DryRunOutcome",
    "VUOTO_NON_GARANTIBILE",
    "dry_run",
    "dry_run_multi_join",
    "dry_run_transform",
]
