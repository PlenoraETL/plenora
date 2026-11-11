"""
Generatore di query SQL dal canvas grafico.

Keeps this module's responsibility explicit and isolated from adjacent code.
"""

import re
from typing import Any, List, Optional

from app.core.projects.canvas_models import Node, Edge
from app.core.projects.canvas_preview_scope import is_join_edge
from app.core.estrai.types import JsonObject
from app.core.estrai.query_aliases import with_unique_aliases
from app.core.estrai.query_filters import WhereClauseBuilder
from app.core.estrai.query_joins import QueryJoinRenderer
from app.core.estrai.query_projection import QueryProjectionRenderer
from app.utils.logger import get_logger

logger = get_logger(__name__)


class QueryGenerator:
    """
    Genera query SQL a partire dallo stato del canvas grafico.

    Il canvas è un grafo diretto dove:
    - I nodi sorgente (type=source) rappresentano tabelle con colonne, filtri e alias
    - Gli edges rappresentano JOIN (attributivi o spaziali)
    - Il nodo risultato (type=result) definisce la query finale con DISTINCT, LIMIT, GROUP BY

    La generazione è parametrica rispetto al database target (db_type) per gestire
    le differenze di sintassi SQL tra i vari DBMS.
    """

    def __init__(self, db_type: str = 'postgres'):
        # Contatore incrementale per generare nomi parametri unici (:p_0, :p_1
        self._param_counter = 0
        # Tipo database per la traduzione di funzioni SQL
        self.db_type = db_type
        # Schema database (es. dbo per SQL Server, public per PostgreSQL
        self.db_schema = None

    # METODO PRINCIPALE

    def generate_sql(
        self,
        nodes: List[Node],
        edges: List[Edge],
        result_node_id: str,
        db_type: str = None,
        db_schema: str = None
    ) -> JsonObject:
        """
        Genera la query SQL completa a partire dal canvas.

        Args:
            nodes: tutti i nodi del canvas
            edges: tutti gli edges (join) del canvas
            result_node_id: ID del nodo risultato (punto finale del flusso)
            db_type: tipo database target (sovrascrive quello del costruttore)
            db_schema: schema database (es. 'dbo' per SQL Server, 'public' per PostgreSQL)

        Returns:
            Dizionario con:
            - sql: la query SQL con placeholder (:p_0, :p_1, ...)
            - params: dizionario {p_0: valore, p_1: valore, ...}
            - tables_used: lista nomi tabelle coinvolte
            - columns_count: numero colonne nella SELECT
        """
        # Reset contatore parametri per ogni nuova query
        self._param_counter = 0

        if db_type:
            self.db_type = db_type
        
        # Imposta lo schema (con default per database type
        self.db_schema = db_schema
        if not self.db_schema:
            self.db_schema = self._get_default_schema()

        params = {}
        # Registro della costruzione: i filtri entrati nella WHERE con i
        # loro parametri (verifica «Utente che esegue» dopo la costruzione).
        bound_filters: list = []

        # Trova il nodo risultato
        result_node = next((n for n in nodes if n.id == result_node_id), None)
        if not result_node:
            raise ValueError(f"Nodo risultato '{result_node_id}' non trovato")

        # ESTRAI is SQL-source only.
        unsupported_sources = [
            n for n in nodes
            if n.type in {"staticValues", "datasourceNode", "datasource_node"}
            and not self._is_datasource_carica_target(n)
        ]
        if unsupported_sources:
            ids = ", ".join(n.id for n in unsupported_sources)
            raise ValueError(
                f"Il modulo ESTRAI non supporta sorgenti file o statiche "
                f"({ids}). Usa tabelle database, layer ArcGIS, oppure MANIPOLA."
            )

        table_nodes = self.query_tables(nodes, edges, result_node_id)

        if not table_nodes:
            raise ValueError("Nessuna tabella connessa al nodo risultato")

        # Estrai configurazione GROUP BY, ORDER BY, HAVING dal nodo risultato
        group_by_config = self._estrai_config_group_by(result_node)
        order_by_config = self._estrai_config_order_by(result_node)
        having_config = self._estrai_config_having(result_node)

        # Genera le singole parti della query
        sql_parts = {
            "select": self._genera_select(table_nodes, result_node, group_by_config),
            "from": self._genera_from(table_nodes, edges),
            "where": self._genera_where(table_nodes, params, bound_filters),
            "group_by": self._genera_group_by(group_by_config, table_nodes),
            "having": self._genera_having(having_config, group_by_config, params, table_nodes),
            "order_by": self._genera_order_by(order_by_config, table_nodes, group_by_config),
            "distinct": result_node.distinct if hasattr(result_node, 'distinct') else False,
            "limit": result_node.limit if hasattr(result_node, 'limit') else None
        }

        # Assembla la query finale
        sql = self._assembla_query(sql_parts)

        return {
            "sql": sql,
            "params": params,
            "tables_used": [self._get_node_table_name(n) for n in table_nodes],
            "columns_count": len(self._get_colonne_visibili(table_nodes)),
            "bound_filters": bound_filters,
        }

    def query_tables(self, nodes: List[Node], edges: List[Edge], result_node_id: str) -> List[Node]:
        """Tabelle della query nell'ordine del FROM, con alias univoci.

        Unica sequenza per il SQL (``generate_sql``) e per le colonne di
        /schemas (``projects.schema_execution_sources.sql_projection``):
        risale il grafo dal risultato, riordina per le dipendenze dei JOIN
        (ogni tabella JOINata referenzia solo tabelle gia' nel FROM) e rende
        univoci gli alias (``query_aliases.with_unique_aliases``: la stessa
        tabella piu' volte, self-join) su copie dei nodi.
        """
        table_nodes = self._trova_tabelle_connesse(nodes, edges, result_node_id)
        table_nodes.reverse()
        table_nodes = self._riordina_per_dipendenze_join(table_nodes, edges)
        return with_unique_aliases(table_nodes)

    def _get_default_schema(self) -> Optional[str]:
        """
        Ritorna lo schema di default per il tipo di database.
        
        - SQL Server: dbo
        - PostgreSQL: public (ma non obbligatorio)
        - MySQL/MariaDB: None (usa il database)
        - Oracle: None (usa lo schema dell'utente)
        - SQLite: None (non ha schema)
        """
        db = (self.db_type or 'postgres').lower()
        
        if db in ('mssql', 'sqlserver'):
            return 'dbo'
        # PostgreSQL non richiede obbligatoriamente lo schema public
        else:
            return None

    def _get_node_table_name(self, node) -> str:
        """Restituisce il nome tabella SQL per un nodo."""
        return getattr(node, 'table_name', None) or ''

    def _qualify_table_name(self, table_name: str) -> str:
        """
        Qualifica il nome tabella con lo schema se necessario.

        Solo PostgreSQL e SQL Server hanno il concetto di "schema" da
        prefissare alle tabelle. MySQL usa "database" (non si prefissa
        nelle query intra-DB), SQLite non ha schema affatto, Oracle usa
        lo schema dell'utente di default.

        Es: 'ac' -> 'dbo.ac' per SQL Server
            'ac' -> 'public.ac' per PostgreSQL (se db_schema='public')
            'ac' -> 'ac' per MySQL/SQLite/Oracle (db_schema ignorato)
        """
        if not table_name:
            return table_name

        # Se la tabella ha già uno schema (contiene '.'), non modificare
        if '.' in table_name:
            return table_name

        # Solo per i DB che davvero usano schema-prefix nelle query
        db = (self.db_type or '').lower()
        schema_aware_dbs = ('postgres', 'postgresql', 'mssql', 'sqlserver')
        if db not in schema_aware_dbs:
            return table_name

        # Se abbiamo uno schema configurato, qualifica il nome
        if self.db_schema:
            return f"{self.db_schema}.{table_name}"

        return table_name

    # CONFIGURAZIONE GROUP BY

    def _estrai_config_group_by(self, result_node: Node) -> JsonObject:
        """
        Estrae la configurazione GROUP BY dal nodo risultato.

        Il GROUP BY può essere specificato in due modi:
        1. Come attributi diretti del nodo (group_by_enabled, group_by_columns, aggregations)
        2. Come chiavi nel dizionario result_node.data

        Formato configurazione:
        {
            "enabled": True,
            "columns": ["comune", "provincia"],
            "aggregations": [
                {"column": "area", "function": "SUM", "alias": "totale_area"},
                {"column": "*", "function": "COUNT", "alias": "conteggio"}
            ]
        }
        """
        config = {
            "enabled": False,
            "columns": [],
            "aggregations": []
        }

        # Tentativo 1: attributi diretti del nodo
        if hasattr(result_node, 'group_by_enabled') and result_node.group_by_enabled:
            config["enabled"] = True
            config["columns"] = getattr(result_node, 'group_by_columns', []) or []
            config["aggregations"] = getattr(result_node, 'aggregations', []) or []
        # Tentativo 2: chiavi nel dizionario .data
        elif hasattr(result_node, 'data') and result_node.data:
            data = result_node.data if isinstance(result_node.data, dict) else {}
            if data.get('group_by_enabled'):
                config["enabled"] = True
                config["columns"] = data.get('group_by_columns', []) or []
                config["aggregations"] = data.get('aggregations', []) or []

        return config

    def _estrai_config_order_by(self, result_node: Node) -> JsonObject:
        """
        Estrae la configurazione ORDER BY dal nodo risultato.

        Formato configurazione:
        {
            "enabled": True,
            "columns": [
                {"column": "nome", "direction": "ASC"},
                {"column": "data", "direction": "DESC"}
            ]
        }
        """
        config = {"enabled": False, "columns": []}

        # Tentativo 1: attributi diretti del nodo
        if hasattr(result_node, 'order_by_enabled') and result_node.order_by_enabled:
            config["enabled"] = True
            cols = getattr(result_node, 'order_by_columns', []) or []
            config["columns"] = [
                {"column": c.column, "direction": c.direction}
                if hasattr(c, 'column') else c
                for c in cols
            ]
        # Tentativo 2: chiavi nel dizionario .data
        elif hasattr(result_node, 'data') and result_node.data:
            data = result_node.data if isinstance(result_node.data, dict) else {}
            if data.get('order_by_enabled'):
                config["enabled"] = True
                config["columns"] = data.get('order_by_columns', []) or []

        return config

    def _estrai_config_having(self, result_node: Node) -> JsonObject:
        """
        Estrae la configurazione HAVING dal nodo risultato.

        Formato configurazione:
        {
            "enabled": True,
            "conditions": [
                {"function": "COUNT", "column": "*", "operator": ">", "value": "10"},
                {"function": "SUM", "column": "area", "operator": ">=", "value": "1000"}
            ]
        }
        """
        config = {"enabled": False, "conditions": []}

        # Tentativo 1: attributi diretti del nodo
        if hasattr(result_node, 'having_enabled') and result_node.having_enabled:
            config["enabled"] = True
            conds = getattr(result_node, 'having_conditions', []) or []
            config["conditions"] = [
                {"function": c.function, "column": c.column, "operator": c.operator, "value": c.value}
                if hasattr(c, 'function') else c
                for c in conds
            ]
        # Tentativo 2: chiavi nel dizionario .data
        elif hasattr(result_node, 'data') and result_node.data:
            data = result_node.data if isinstance(result_node.data, dict) else {}
            if data.get('having_enabled'):
                config["enabled"] = True
                config["conditions"] = data.get('having_conditions', []) or []

        return config

    # NAVIGAZIONE GRAFO

    def _trova_tabelle_connesse(
        self,
        nodes: List[Node],
        edges: List[Edge],
        result_node_id: str
    ) -> List[Node]:
        """
        Risale il grafo all'indietro dal nodo risultato per trovare tutte le
        tabelle sorgente connesse. Usa BFS (Breadth-First Search) per
        visitare tutti i nodi raggiungibili seguendo gli edges al contrario.

        REGOLA DELLE JOIN (filtro «Utente che esegue» ignorato, 11/11/2026):
        un arco fra due tabelle SQL e' una JOIN e vale nei due versi
        (``canvas_preview_scope.is_join_edge``, la stessa regola dell'ambito
        dell'esecuzione). Una tabella collegata alla radice «in avanti»
        (radice -> collegata) entra nella query con le sue colonne, i suoi
        filtri e la JOIN, anche senza colonne visibili. Prima restava fuori
        in silenzio, e con lei i suoi filtri.

        Returns:
            Lista ordinata dei nodi tabella connessi (dal più lontano al più vicino)
        """
        table_nodes = []
        visited = set()
        queue = [result_node_id]

        while queue:
            current_id = queue.pop(0)

            if current_id in visited:
                continue
            visited.add(current_id)

            # Trova edges entranti (target = nodo corrente
            incoming_edges = [e for e in edges if e.target == current_id]

            for edge in incoming_edges:
                source_node = next((n for n in nodes if n.id == edge.source), None)

                if source_node and source_node.type != 'result':
                    if source_node not in table_nodes:
                        table_nodes.append(source_node)
                    # Continua a risalire il grafo
                    queue.append(edge.source)

        # JOIN nei due versi: le tabelle collegate «in avanti» alle tabelle
        # gia' trovate (in coda: l'ordine delle tabelle di prima non cambia).
        types_by_id = {n.id: n.type for n in nodes}
        by_id = {n.id: n for n in nodes}
        pending = [n.id for n in table_nodes]
        while pending:
            current_id = pending.pop(0)
            for edge in edges:
                if not is_join_edge(edge, types_by_id) or current_id not in (edge.source, edge.target):
                    continue
                other = by_id.get(edge.target if edge.source == current_id else edge.source)
                if other is not None and other not in table_nodes:
                    table_nodes.append(other)
                    pending.append(other.id)

        return table_nodes

    @staticmethod
    def _is_datasource_carica_target(node: Node) -> bool:
        """True for datasource nodes used as CARICA destinations, not sources."""
        data = node.data if isinstance(node.data, dict) else {}
        mode = getattr(node, "mode", None) or data.get("mode")
        dest_filename = getattr(node, "dest_filename", None) or data.get("dest_filename")
        dest_format = getattr(node, "dest_format", None) or data.get("dest_format")
        return str(mode or "").lower() == "carica" or bool(dest_filename and dest_format)

    def _riordina_per_dipendenze_join(
        self,
        table_nodes: List[Node],
        edges: List[Edge]
    ) -> List[Node]:
        """
        Riordina i nodi tabella in modo che ogni JOIN referenzi solo tabelle
        già presenti nella clausola FROM.

        Algoritmo:
        1. Trova la root: il nodo che NON è target di nessun edge inter-tabella
           (cioè nessun edge punta verso di lui da un altro nodo tabella)
        2. Parti dalla root come FROM principale
        3. Ad ogni iterazione, cerca tra i nodi rimanenti uno il cui edge
           referenzia un nodo già ordinato
        4. Se nessun nodo soddisfa il vincolo, aggiungilo comunque (CROSS JOIN)
        """
        if len(table_nodes) <= 1:
            return table_nodes

        node_ids = {n.id for n in table_nodes}
        
        # Trova la root: nodo che non è target di nessun edge tra nodi tabella
        # escludi edges verso resultNode, carica, ecc
        target_ids_of_table_edges = set()
        for e in edges:
            if e.source in node_ids and e.target in node_ids:
                target_ids_of_table_edges.add(e.target)
        
        root = None
        for n in table_nodes:
            if n.id not in target_ids_of_table_edges:
                root = n
                break
        
        # Fallback: se tutti sono target (ciclo), usa il primo
        if not root:
            root = table_nodes[0]

        # Ordina partendo dalla root
        ordered = [root]
        ordered_ids = {root.id}
        remaining = [n for n in table_nodes if n.id != root.id]

        max_iterations = len(remaining) * len(remaining) + 1  # safety net
        iterations = 0

        while remaining and iterations < max_iterations:
            iterations += 1
            found = False

            for i, node in enumerate(remaining):
                # Cerca l'edge che collega questo nodo a un nodo già ordinato
                edge = next(
                    (e for e in edges 
                     if (e.target == node.id and e.source in ordered_ids)
                     or (e.source == node.id and e.target in ordered_ids)),
                    None
                )

                if edge:
                    ordered.append(node)
                    ordered_ids.add(node.id)
                    remaining.pop(i)
                    found = True
                    break

            if not found:
                # Nessun nodo ha dipendenze soddisfatte — aggiungi il primo
                node = remaining.pop(0)
                ordered.append(node)
                ordered_ids.add(node.id)

        return ordered

    # GENERAZIONE CLAUSOLA SELECT

    def _genera_select(
        self,
        table_nodes: List[Node],
        result_node: Node,
        group_by_config: JsonObject = None
    ) -> str:
        return self._projection_renderer().generate_select(
            table_nodes, result_node, group_by_config
        )

    def _genera_group_by(
        self,
        group_by_config: JsonObject,
        table_nodes: List[Node] = None
    ) -> str:
        return self._projection_renderer().generate_group_by(group_by_config, table_nodes)

    # ORDER BY

    def _genera_order_by(
        self,
        order_by_config: JsonObject,
        table_nodes: List[Node],
        group_by_config: JsonObject
    ) -> str:
        return self._projection_renderer().generate_order_by(
            order_by_config, table_nodes, group_by_config
        )

    # HAVING

    def _genera_having(
        self,
        having_config: JsonObject,
        group_by_config: JsonObject,
        params: JsonObject,
        table_nodes: Optional[List[Node]] = None,
    ) -> str:
        return self._projection_renderer().generate_having(
            having_config, group_by_config, params, table_nodes=table_nodes,
        )

    # GEOMETRIE — CONVERSIONE WKT

    # GENERAZIONE CLAUSOLA FROM + JOIN

    def _genera_from(self, table_nodes: List[Node], edges: List[Edge]) -> str:
        return QueryJoinRenderer(self).generate_from(table_nodes, edges)

    def _genera_join(
        self,
        node: Node,
        edges: List[Edge],
        all_nodes: List[Node],
        processed_ids: set = None
    ) -> str:
        return QueryJoinRenderer(self).generate_join(node, edges, all_nodes, processed_ids)

    def _genera_on_spaziale(self, edge: Edge, source_alias: str, table_alias: str) -> str:
        return QueryJoinRenderer(self).generate_on_spatial(edge, source_alias, table_alias)

    def _genera_on_attributivo(
        self,
        edge: Edge,
        source_alias: str,
        table_alias: str,
        node: Node,
        join_type: str,
        is_reversed: bool = False
    ) -> str:
        return QueryJoinRenderer(self).generate_on_attribute(
            edge, source_alias, table_alias, node, join_type, is_reversed
        )

    # GENERAZIONE CLAUSOLA WHERE

    def _genera_where(self, table_nodes: List[Node], params: JsonObject, rendered: Optional[list] = None) -> str:
        return self._where_builder().generate_where(table_nodes, params, rendered)

    def _crea_condizione(
        self,
        table_alias: str,
        column: str,
        operator: str,
        value: Any,
        params: JsonObject,
        column_type: str = 'str'
    ) -> str:
        return self._where_builder().create_condition(
            table_alias, column, operator, value, params, column_type
        )

    def _where_builder(self) -> WhereClauseBuilder:
        return WhereClauseBuilder(
            self.db_type,
            self._prossimo_nome_param,
            self._is_valid_identifier,
            self._get_node_table_name,
        )

    def _projection_renderer(self) -> QueryProjectionRenderer:
        return QueryProjectionRenderer(self)

    # ASSEMBLAGGIO QUERY

    def _assembla_query(self, sql_parts: JsonObject) -> str:
        return self._projection_renderer().assemble_query(sql_parts)

    # UTILITÀ

    def _prossimo_nome_param(self) -> str:
        """Genera il prossimo nome parametro univoco incrementale (:p_0, :p_1, ...)."""
        name = f"p_{self._param_counter}"
        self._param_counter += 1
        return name

    def _is_valid_identifier(self, name: str) -> bool:
        """
        Valida che un identificatore SQL sia sicuro contro injection.

        Controlli:
        - Non vuoto e max 128 caratteri
        - Solo lettere, numeri e underscore (inizia con lettera o underscore)
        - Non è una keyword SQL pericolosa (DROP, DELETE, UNION, ecc.)
        """
        if not name or len(name) > 128:
            return False

        if not re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', name):
            return False

        dangerous_keywords = {
            'drop', 'delete', 'truncate', 'insert', 'update', 'alter',
            'create', 'exec', 'execute', 'union', 'grant', 'revoke'
        }
        if name.lower() in dangerous_keywords:
            return False

        return True

    def _is_valid_quoted_identifier(self, name: str) -> bool:
        """
        Valida un identificatore che verrà emesso tra doppi apici.

        Permette trattini (comuni in nomi da GeoJSON/API) oltre ai caratteri
        standard. L'identificatore sarà racchiuso tra "" nel SQL generato.

        Controlli:
        - Non vuoto e max 128 caratteri
        - Inizia con lettera o underscore, poi lettere/numeri/underscore/trattini
        - Non è una keyword SQL pericolosa
        """
        if not name or len(name) > 128:
            return False
        if not re.match(r'^[a-zA-Z_][a-zA-Z0-9_\-]*$', name):
            return False
        dangerous_keywords = {
            'drop', 'delete', 'truncate', 'insert', 'update', 'alter',
            'create', 'exec', 'execute', 'union', 'grant', 'revoke'
        }
        if name.lower() in dangerous_keywords:
            return False
        return True

    def _safe_identifier(self, name: str, context: str = "identificatore") -> str:
        """
        Ritorna l'identificatore pronto per l'SQL, scegliendo la forma corretta:
        - Identificatore standard (solo lettere/numeri/underscore) → emesso as-is
        - Identificatore con trattini → emesso tra doppi apici: "poi-name"
        - Identificatore non valido → ValueError

        Da usare in tutti i punti dove si emette un nome colonna o alias nel SQL,
        in sostituzione del pattern: if not _is_valid_identifier: raise ValueError.
        """
        if self._is_valid_identifier(name):
            return name
        if self._is_valid_quoted_identifier(name):
            return f'"{name}"'
        raise ValueError(f"{context} non valido: {name}")

    def _wrap_col_with_func(self, col_expr: str, func: str) -> str:
        """
        Wrappa un'espressione colonna con una funzione SQL opzionale.
        
        Funzioni supportate: TRIM, LTRIM_RTRIM, UPPER, LOWER, COALESCE_EMPTY
        Se func è vuoto o None, ritorna l'espressione invariata.
        """
        if not func:
            return col_expr
        if func == 'TRIM':
            return f"TRIM({col_expr})"
        if func == 'LTRIM_RTRIM':
            return f"LTRIM(RTRIM({col_expr}))"
        if func == 'UPPER':
            return f"UPPER({col_expr})"
        if func == 'LOWER':
            return f"LOWER({col_expr})"
        if func == 'COALESCE_EMPTY':
            return f"COALESCE({col_expr}, '')"
        return col_expr

    def _get_colonne_visibili(self, table_nodes: List[Node]) -> List[str]:
        """Restituisce la lista di nomi delle colonne visibili (incluse le calcolate)."""
        columns = []
        for node in table_nodes:
            for col in node.columns:
                if col.visible:
                    columns.append(col.name)

            calculated_columns = getattr(node, 'calculated_columns', None) or []
            for calc_col in calculated_columns:
                columns.append(calc_col.get('name', 'calculated'))

        return columns
