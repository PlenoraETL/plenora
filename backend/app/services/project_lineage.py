"""
Rilevamento cicli nel DAG delle dipendenze cross-project.

C3 fix 5: due progetti Manipola possono referenziare l'uno l'altro
"""

from typing import Callable, List, Optional


class ProjectLineageCycleError(Exception):
    """Sollevato quando un ciclo cross-project viene rilevato al save canvas."""

    def __init__(self, chain: List[str]):
        self.chain = chain
        super().__init__(self._format(chain))

    @staticmethod
    def _format(chain: List[str]) -> str:
        arrow = " → "
        return f"Ciclo di dipendenza tra progetti: {arrow.join(chain)}"


def detect_project_lineage_cycle(
    root_id: str,
    get_sources_fn: Callable[[str], Optional[List[str]]],
) -> Optional[List[str]]:
    """Cerca un ciclo nel DAG delle dipendenze cross-project a partire da root.

    Args:
        root_id: project_id da cui partire (tipicamente il progetto
            che si sta salvando).
        get_sources_fn: callback che dato un project_id ritorna la
            lista degli ID dei progetti referenziati nei suoi
            `project_source`. Ritorna None o [] se nessun source.

    Returns:
        La catena del ciclo (es. ["A", "B", "C", "A"]) se rilevato,
        None altrimenti. La catena inizia e finisce sullo stesso ID
        per rendere il ciclo ovvio nel messaggio di errore.

    Implementazione: DFS iterativo con path corrente tracciato come
    lista + on-path set (O(1) check appartenenza). Visited globale
    evita di riesplorare sottografi gia' analizzati — il diamond
    pattern (A → B → D, A → C → D) non genera falsi positivi.
    """
    # Stack di tuple (node_id, iterator_figli). L'iterator consuma i
    # figli uno alla volta: quando finisce, pop del nodo dal path.
    path: List[str] = [root_id]
    on_path: set = {root_id}
    visited: set = set()

    sources = get_sources_fn(root_id) or []
    stack = [(root_id, iter(sources))]

    while stack:
        _current, children_iter = stack[-1]
        try:
            child = next(children_iter)
        except StopIteration:
            # Finito di esplorare i figli del nodo corrente: pop.
            finished = stack.pop()[0]
            path.pop()
            on_path.discard(finished)
            visited.add(finished)
            continue

        if child in on_path:
            # Ciclo! Ricostruisci la catena dal primo incontro di `child`
            # fino al nodo corrente, poi chiudi tornando su `child`.
            cycle_start_idx = path.index(child)
            return path[cycle_start_idx:] + [child]

        if child in visited:
            # Sottografo gia' esplorato senza cicli — skip.
            continue

        # Espandi child
        path.append(child)
        on_path.add(child)
        child_sources = get_sources_fn(child) or []
        stack.append((child, iter(child_sources)))

    return None
