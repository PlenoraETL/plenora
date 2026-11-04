"""Piani di schema ottenuti dalle proiezioni effettive di Estrai e Arricchisci."""

from types import SimpleNamespace

from app.core.arricchisci import db as api_db
from app.core.connections import db as connection_db
from app.core.estrai.query_generator import QueryGenerator
from app.core.estrai.query_projection import QueryProjectionRenderer
from app.core.query_execution.query_session import normalize_result_columns
from app.core.projects.canvas_models import Node, Edge
from app.shared.contracts.payloads import JsonObject, JsonRows


def sql_projection(nodes: list[Node], edges: list[Edge], result_node: Node,
                   provider: str = "postgres") -> JsonRows:
    """La visita del grafo e il renderer sono gli stessi del generatore SQL."""
    generator = QueryGenerator(provider)
    tables = generator._trova_tabelle_connesse(nodes, edges, result_node.id)
    tables.reverse()
    tables = generator._riordina_per_dipendenze_join(tables, edges)
    metadata = []
    QueryProjectionRenderer(generator).generate_select(
        tables, result_node, generator._estrai_config_group_by(result_node),
        column_metadata=metadata,
    )
    names = normalize_result_columns([col["name"] for col in metadata if col["name"]], SimpleNamespace(provider=provider))
    for col, name in zip((col for col in metadata if col["name"]), names):
        col["name"] = name
    return metadata


def load_api_schema_config(api_id: str) -> JsonObject | None:
    """Legge la configurazione corrente senza decifrare credenziali o invocare l'API."""
    row = api_db.get_api_connection_row(api_id)
    return dict(row.config or {}) if row else None


def load_sql_provider(connection_id: str) -> str:
    """Il provider serve alla stessa normalizzazione usata dal cursore SQL."""
    connection = connection_db.get_connection_config(connection_id)
    if connection is None:
        raise ValueError("Connessione SQL non disponibile per l'inferenza")
    return connection.db_type
