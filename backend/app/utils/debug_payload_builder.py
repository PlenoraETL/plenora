"""
DebugPayloadBuilder — assembla il debug_payload per il Debug Agent.

Viene invocato alla chiusura di una run in failure.
"""

from typing import Optional

from app.core.projects import debug_context
from app.utils.logger import get_logger

logger = get_logger(__name__)


class DebugPayloadBuilder:
    """
    Assembla il debug_payload a partire da run_id, steps e failure info.

    Uso tipico (in pipeline_task.py, nel blocco except):

        from app.utils.debug_payload_builder import DebugPayloadBuilder

        payload = DebugPayloadBuilder.build(
            run_id=run_id,
            project_id=project_id,
            steps=run_steps,
            failure={
                "node_id": "node_7",
                "node_type": "api_connector",
                "node_label": "Arricchimento OMI",
                "module": "arricchisci",
                "error_type": type(e).__name__,
                "error_message": str(e),
                "traceback": traceback_str,
                "occurred_at_ms": elapsed_ms,
            },
            input_sample=input_sample, # opzionale
        )
    """

    def build(
        self,
        run_id: str,
        project_id: str,
        steps: list,
        failure: dict,
        input_sample: Optional[dict] = None,
    ) -> dict:
        """
        Costruisce il payload completo.

        Args:
            run_id: ID della run fallita
            project_id: ID del progetto
            steps: lista di step raccolti durante l'esecuzione
                           [{"step": 1, "module": "estrai", "status": "success",
                             "rows_out": 1847, "duration_ms": 340}, ...]
            failure: dizionario con info sul fallimento
                           (node_id, node_type, node_label, module,
                            error_type, error_message, traceback, occurred_at_ms)
            input_sample: opzionale — sample dei dati in ingresso al nodo fallito
                           {"rows": N, "columns": [...], "sample": [...],
                            "null_counts": {...}}

        Returns:
            dict con i 5 blocchi del debug_payload
        """
        pipeline_context = self._build_pipeline_context(project_id, steps)
        failing_node_config = self._extract_node_config(project_id, failure.get("node_id"))

        payload = {
            "pipeline_context":    pipeline_context,
            "steps":               steps,
            "failure":             failure,
            "failing_node_config": failing_node_config,
            "input_sample":        input_sample or {},
        }

        return payload

    # PIPELINE CONTEXT

    def _build_pipeline_context(self, project_id: str, steps: list) -> dict:
        """
        Legge metadata e canvas dal DB config (PG) e costruisce il contesto pipeline.
        """
        try:
            context = debug_context.load_project_debug_context(project_id)
            canvas_raw = context.canvas if context else {}

            project_name = context.project_name if context else project_id
            module = context.project_type if context else ""
            triggered_by = context.triggered_by if context else "manual"

            canvas_summary = self._summarize_canvas(canvas_raw)

            return {
                "project_id":     project_id,
                "project_name":   project_name,
                "module":         module,
                "triggered_by":   triggered_by,
                "canvas_summary": canvas_summary,
            }

        except Exception as e:
            logger.warning(f"DebugPayloadBuilder: impossibile leggere pipeline_context: {e}")
            return {"project_id": project_id, "canvas_summary": "non disponibile"}

    def _summarize_canvas(self, canvas_raw) -> str:
        """
        Ricostruisce la catena nodi come stringa leggibile.
        Es. "ESTRAI → MANIPOLA (3 trasformazioni) → ARRICCHISCI (OMI) → CARICA"
        """
        if not canvas_raw:
            return "non disponibile"

        try:
            # canvas_raw può essere un dict o un oggetto CanvasState
            if hasattr(canvas_raw, "nodes"):
                nodes = canvas_raw.nodes
                edges = canvas_raw.edges
            elif isinstance(canvas_raw, dict):
                nodes = canvas_raw.get("nodes", [])
                edges = canvas_raw.get("edges", [])
            else:
                return "non disponibile"

            # Mappa id → label leggibile
            node_labels = {}
            for n in nodes:
                nid = n.id if hasattr(n, "id") else n.get("id", "")
                ntype = n.type if hasattr(n, "type") else n.get("type", "")
                data = n.data if hasattr(n, "data") else n.get("data", {})
                label = (data or {}).get("label") or ntype or nid
                node_labels[nid] = label

            # Ordina i nodi seguendo gli edge (topological order approssimativo
            edge_map = {}
            for e in edges:
                src = e.source if hasattr(e, "source") else e.get("source", "")
                tgt = e.target if hasattr(e, "target") else e.get("target", "")
                edge_map[src] = tgt

            # Trova il nodo radice (nessuno punta a lui
            targets = set(edge_map.values())
            sources = set(edge_map.keys())
            roots = sources - targets
            if not roots:
                # Fallback: lista nomi dei nodi nell'ordine in cui compaiono
                return " → ".join(node_labels.values())

            # Traversa la catena
            chain = []
            current = next(iter(roots))
            visited = set()
            while current and current not in visited:
                visited.add(current)
                chain.append(node_labels.get(current, current))
                current = edge_map.get(current)

            return " → ".join(chain) if chain else "non disponibile"

        except Exception as e:
            logger.warning(f"DebugPayloadBuilder: errore canvas summary: {e}")
            return "non disponibile"

    # FAILING NODE CONFIG

    def _extract_node_config(self, project_id: str, node_id: Optional[str]) -> dict:
        """
        Estrae la configurazione (data{}) del nodo che ha fallito dal canvas.
        """
        if not node_id:
            return {}

        try:
            context = debug_context.load_project_debug_context(project_id)
            canvas_raw = context.canvas if context else {}
            if not canvas_raw:
                return {}

            nodes = canvas_raw.nodes if hasattr(canvas_raw, "nodes") else canvas_raw.get("nodes", [])

            for n in nodes:
                nid = n.id if hasattr(n, "id") else n.get("id", "")
                if nid == node_id:
                    data = n.data if hasattr(n, "data") else n.get("data", {})
                    # Rimuove campi interni non utili all'agente
                    config = dict(data or {})
                    for internal_key in ("__pydantic_fields_set__", "label", "color"):
                        config.pop(internal_key, None)
                    return config

            return {}

        except Exception as e:
            logger.warning(f"DebugPayloadBuilder: impossibile estrarre node config ({node_id}): {e}")
            return {}
