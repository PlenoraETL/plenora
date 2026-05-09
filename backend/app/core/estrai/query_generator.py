"""
Generatore di query SQL dal canvas grafico.

Keeps this module's responsibility explicit and isolated from adjacent code.
"""

import re
from app.utils.logger import get_logger
from typing import List, Dict, Optional, Any, Tuple

from app.shared.models import Node, Edge
from app.db.sql_functions import translate_calculated_column, get_translator
from app.db.sql_translator import sql_translator
from app.core.estrai.query_filters import WhereClauseBuilder
from app.core.estrai.query_joins import QueryJoinRenderer

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
    ) -> Dict[str, Any]:
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

        # Trova il nodo risultato
        result_node = next((n for n in nodes if n.id == result_node_id), None)
        if not result_node:
            raise ValueError(f"Nodo risultato '{result_node_id}' non trovato")

        # Risali il grafo dal risultato per trovare le tabelle connesse
        table_nodes = self._trova_tabelle_connesse(nodes, edges, result_node_id)
        table_nodes.reverse()
        
        # Riordina i nodi per rispettare le dipendenze dei JOIN
        # ogni tabella JOINata deve referenziare solo tabelle già presenti nel FROM
        table_nodes = self._riordina_per_dipendenze_join(table_nodes, edges)

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
            "where": self._genera_where(table_nodes, params),
            "group_by": self._genera_group_by(group_by_config, table_nodes),
            "having": self._genera_having(having_config, group_by_config, params),
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
            "columns_count": len(self._get_colonne_visibili(table_nodes))
        }

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
        """Restituisce il nome tabella SQL per un nodo, gestendo datasource_node."""
        if getattr(node, 'type', None) == 'datasource_node':
            raw = (getattr(node, 'datasource_name', None) or node.id)
            sanitized = raw.replace(' ', '_').replace('-', '_').replace('.', '_')
            return f"_urby_ds_{sanitized}"
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

    def _estrai_config_group_by(self, result_node: Node) -> Dict[str, Any]:
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

    def _estrai_config_order_by(self, result_node: Node) -> Dict[str, Any]:
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

    def _estrai_config_having(self, result_node: Node) -> Dict[str, Any]:
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

        return table_nodes

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
        group_by_config: Dict[str, Any] = None
    ) -> str:
        """
        Genera la clausola SELECT con colonne visibili, alias e colonne calcolate.

        Se GROUP BY è attivo, delega a _genera_select_con_groupby.
        Altrimenti genera un SELECT normale con:
        - Colonne visibili (con eventuale alias)
        - Colonne geometriche wrappate in ST_AsText
        - Colonne calcolate tradotte tramite sql_functions
        """
        # Se GROUP BY è attivo, usa il generatore specifico
        if group_by_config and group_by_config.get("enabled"):
            return self._genera_select_con_groupby(table_nodes, group_by_config)

        # SELECT normale (senza GROUP BY
        columns = []

        for node in table_nodes:
            table_alias = node.alias or self._get_node_table_name(node)

            if node.type == 'datasource_node':
                visible_cols = [c for c in (node.columns or []) if getattr(c, 'visible', True)]
                if not visible_cols:
                    columns.append(f"{table_alias}.*")
                    continue

            for col in node.columns:
                if not col.visible:
                    continue

                col_name = col.name
                col_alias = col.alias
                col_type = getattr(col, 'type', 'str') or 'str'

                col_name_sql = self._safe_identifier(col_name, "Nome colonna")

                # Colonne geometriche: wrappa con funzione WKT per il database
                if col_type.startswith('geometry'):
                    col_expr = self._wrap_geometry_as_wkt(f"{table_alias}.{col_name_sql}")
                    output_alias = col_alias if col_alias else col_name
                    output_alias_sql = self._safe_identifier(output_alias, "Alias colonna")
                    columns.append(f"{col_expr} AS {output_alias_sql}")
                else:
                    if col_alias:
                        col_alias_sql = self._safe_identifier(col_alias, "Alias colonna")
                        columns.append(f"{table_alias}.{col_name_sql} AS {col_alias_sql}")
                    else:
                        columns.append(f"{table_alias}.{col_name_sql}")

            calculated_columns = getattr(node, 'calculated_columns', None) or []
            for calc_col in calculated_columns:
                try:
                    if calc_col.get('is_manual') and calc_col.get('sql'):
                        # SQL manuale: usa l'espressione così com'è
                        sql_expr = calc_col['sql']
                    elif calc_col.get('function_name') and calc_col.get('params'):
                        # Formato preferito: function_name + params (database-agnostic
                        sql_expr = translate_calculated_column(
                            db_type=self.db_type,
                            column_name=calc_col['name'],
                            expression={
                                "function": calc_col['function_name'],
                                "params": calc_col['params']
                            },
                            table_alias=table_alias
                        )
                    elif calc_col.get('expression') and isinstance(calc_col['expression'], dict):
                        # Fallback: expression come dizionario (formato legacy
                        sql_expr = translate_calculated_column(
                            db_type=self.db_type,
                            column_name=calc_col['name'],
                            expression=calc_col['expression'],
                            table_alias=table_alias
                        )
                    else:
                        # Nessun formato riconosciuto: skip silenzioso
                        continue

                    columns.append(sql_expr)

                except Exception as e:
                    raise ValueError(
                        f"Errore colonna calcolata '{calc_col.get('name', '?')}': {e}"
                    )

        if not columns:
            raise ValueError("Nessuna colonna selezionata")

        return ", ".join(columns)

    def _genera_select_con_groupby(
        self,
        table_nodes: List[Node],
        group_by_config: Dict[str, Any]
    ) -> str:
        """
        Genera SELECT per query con GROUP BY.

        Output tipico: "comune, provincia, SUM(area) AS totale_area, COUNT(*) AS conteggio"

        Composizione:
        1. Colonne di raggruppamento (qualificate con alias tabella)
        2. Funzioni aggregate (COUNT, SUM, AVG, MIN, MAX)
        """
        columns = []
        group_columns = group_by_config.get("columns", [])
        aggregations = group_by_config.get("aggregations", [])

        # Costruisci mappa nome_colonna e alias → alias_tabella
        table_map = {}
        for node in table_nodes:
            table_alias = node.alias or self._get_node_table_name(node)
            for col in node.columns:
                if col.visible:
                    table_map[col.name] = table_alias
                    if hasattr(col, 'alias') and col.alias:
                        table_map[col.alias] = table_alias

        def _resolve_col(col_name, context):
            """Risolve alias → table.real_col oppure restituisce col_name qualificato."""
            resolved = table_map.get(col_name)
            if resolved:
                col_real = col_name.split("__", 1)[1] if "__" in col_name else col_name
                col_sql = self._safe_identifier(col_real, context)
                return f"{resolved}.{col_sql}", col_sql
            col_sql = self._safe_identifier(col_name, context)
            return col_sql, col_sql

        # 1. Colonne di raggruppamento
        for col_name in group_columns:
            col_ref, _ = _resolve_col(col_name, "Nome colonna GROUP BY")
            columns.append(col_ref)

        # 2. Funzioni aggregate
        valid_agg_functions = {'COUNT', 'SUM', 'AVG', 'MIN', 'MAX', 'FIRST', 'LAST'}

        for agg in aggregations:
            # Supporta sia dizionari che oggetti Pydantic
            if hasattr(agg, 'column'):
                col = agg.column or '*'
                func = (agg.function or 'COUNT').upper()
                alias = agg.alias or f"{func.lower()}_{col}"
            else:
                col = agg.get('column', '*')
                func = agg.get('function', 'COUNT').upper()
                alias = agg.get('alias', f"{func.lower()}_{col}")

            if func not in valid_agg_functions:
                raise ValueError(f"Funzione aggregazione non valida: {func}")
            alias_sql = self._safe_identifier(alias, "Alias aggregazione")

            # COUNT(*) è l'unica aggregazione che non richiede una colonna specifica
            if col == '*':
                if func != 'COUNT':
                    raise ValueError(f"Solo COUNT può usare *, non {func}")
                columns.append(f"COUNT(*) AS {alias_sql}")
            else:
                col_ref, _ = _resolve_col(col, "Nome colonna aggregazione")
                columns.append(f"{func}({col_ref}) AS {alias_sql}")

        if not columns:
            raise ValueError("Nessuna colonna per GROUP BY")

        return ", ".join(columns)

    def _genera_group_by(self, group_by_config: Dict[str, Any], table_nodes: List[Node] = None) -> str:
        """
        Genera clausola GROUP BY con colonne qualificate (tabella.colonna).

        Qualificare è necessario quando più tabelle hanno colonne con lo stesso nome
        (es. bl_id in bl e prt_su_ls_link) — senza qualifica SQL Server dà errore
        "Ambiguous column name".

        Returns:
            Stringa "t1.col1, t2.col2, ..." oppure stringa vuota se GROUP BY non attivo
        """
        if not group_by_config or not group_by_config.get("enabled"):
            return ""

        columns = group_by_config.get("columns", [])
        if not columns:
            return ""

        # Costruisci mappa colonna → alias tabella per qualificare
        # Filtra per visible=True, stesso criterio di _genera_select_con_groupby
        # così SELECT e GROUP BY usano sempre la stessa tabella per ogni colonna
        table_map = {}
        if table_nodes:
            for node in table_nodes:
                table_alias = node.alias or self._get_node_table_name(node)
                for col in (node.columns or []):
                    col_name    = col.name  if hasattr(col, 'name')  else col.get('name', '')
                    col_alias   = col.alias if hasattr(col, 'alias') else col.get('alias', '')
                    col_visible = col.visible if hasattr(col, 'visible') else col.get('visible', True)
                    if col_name and col_visible:
                        table_map[col_name] = table_alias
                        if col_alias:
                            table_map[col_alias] = table_alias

        qualified = []
        for col in columns:
            resolved = table_map.get(col)
            if resolved:
                col_real = col.split('__', 1)[1] if '__' in col else col
                col_sql  = self._safe_identifier(col_real, "Nome colonna GROUP BY")
                qualified.append(f"{resolved}.{col_sql}")
            else:
                col_sql = self._safe_identifier(col, "Nome colonna GROUP BY")
                qualified.append(col_sql)

        return ", ".join(qualified)

    # ORDER BY

    def _genera_order_by(
        self,
        order_by_config: Dict[str, Any],
        table_nodes: List[Node],
        group_by_config: Dict[str, Any]
    ) -> str:
        """
        Genera la clausola ORDER BY.

        Se GROUP BY è attivo, permette di ordinare solo per colonne nel GROUP BY
        o per espressioni aggregate (es. SUM(area), COUNT(*)).

        Args:
            order_by_config: configurazione ORDER BY dal nodo risultato
            table_nodes: nodi tabella per risolvere gli alias
            group_by_config: configurazione GROUP BY (per validazione)

        Returns:
            Stringa ORDER BY (senza la keyword), o stringa vuota.
        """
        if not order_by_config.get("enabled") or not order_by_config.get("columns"):
            return ""

        columns = order_by_config["columns"]

        # Mappa colonne → alias tabella
        table_map = {}
        if table_nodes:
            for node in table_nodes:
                table_alias = node.alias or self._get_node_table_name(node)
                for col in (node.columns or []):
                    col_name = col.name if hasattr(col, 'name') else col.get('name', '')
                    col_alias = col.alias if hasattr(col, 'alias') else col.get('alias', '')
                    col_visible = col.visible if hasattr(col, 'visible') else col.get('visible', True)
                    if col_name and col_visible:
                        table_map[col_name] = table_alias
                        if col_alias:
                            table_map[col_alias] = table_alias

        # Set delle funzioni aggregate valide (per ORDER BY con GROUP BY
        aggregate_functions = {"COUNT", "SUM", "AVG", "MIN", "MAX"}

        parti = []
        for item in columns:
            col = item.get("column", "") if isinstance(item, dict) else str(item)
            direction = item.get("direction", "ASC") if isinstance(item, dict) else "ASC"

            # Normalizza direzione
            direction = direction.upper()
            if direction not in ("ASC", "DESC"):
                direction = "ASC"

            # Controlla se è un'espressione aggregata (es. "COUNT(*)", "SUM(area)"
            col_upper = col.upper().strip()
            is_aggregate = any(col_upper.startswith(f"{fn}(") for fn in aggregate_functions)

            if is_aggregate:
                # Espressione aggregata: valida la struttura base prima dell'interpolazione
                # Pattern atteso: FUNC(identifier) — es. COUNT(*), SUM(area), AVG(col1
                # Rifiuta input che contengono SQL injection keywords
                import re as _re
                if not _re.match(r'^[A-Z_]+\([a-zA-Z0-9_.*\s,]+\)$', col.strip()):
                    raise ValueError(f"Espressione aggregata ORDER BY non valida: {col}")
                parti.append(f"{col} {direction}")
            else:
                # Colonna normale: qualifica con alias tabella
                col_real = col.split('__', 1)[1] if '__' in col else col
                resolved = table_map.get(col)
                if resolved:
                    col_sql = self._safe_identifier(col_real, "Nome colonna ORDER BY")
                    parti.append(f"{resolved}.{col_sql} {direction}")
                else:
                    col_sql = self._safe_identifier(col_real, "Nome colonna ORDER BY")
                    parti.append(f"{col_sql} {direction}")

        return ", ".join(parti)

    # HAVING

    def _genera_having(
        self,
        having_config: Dict[str, Any],
        group_by_config: Dict[str, Any],
        params: Dict[str, Any]
    ) -> str:
        """
        Genera la clausola HAVING.

        HAVING è valido solo quando GROUP BY è attivo.
        Permette di filtrare i risultati aggregati (es. HAVING COUNT(*) > 10).

        Args:
            having_config: configurazione HAVING dal nodo risultato
            group_by_config: configurazione GROUP BY (deve essere attivo)
            params: dizionario parametri da riempire (per query parametrizzate)

        Returns:
            Stringa HAVING (senza la keyword), o stringa vuota.
        """
        if not having_config.get("enabled") or not having_config.get("conditions"):
            return ""

        # HAVING ha senso solo con GROUP BY
        if not group_by_config.get("enabled"):
            return ""

        conditions = having_config["conditions"]

        # Operatori validi
        operatori_validi = {"=", "!=", ">", "<", ">=", "<="}

        parti = []
        for cond in conditions:
            funzione = cond.get("function", "COUNT").upper()
            colonna = cond.get("column", "*")
            operatore = cond.get("operator", ">")
            valore = cond.get("value", "0")

            # Validazione
            if funzione not in ("COUNT", "SUM", "AVG", "MIN", "MAX"):
                continue
            if operatore not in operatori_validi:
                continue

            # Costruisci l'espressione aggregata
            if colonna == "*":
                espressione = f"{funzione}(*)"
            else:
                col_sql = self._safe_identifier(colonna, "Nome colonna HAVING")
                espressione = f"{funzione}({col_sql})"

            # Parametrizza il valore
            param_name = self._prossimo_nome_param()
            try:
                valore_tipizzato = float(valore) if '.' in str(valore) else int(valore)
            except (ValueError, TypeError):
                valore_tipizzato = valore
            params[param_name] = valore_tipizzato

            parti.append(f"{espressione} {operatore} :{param_name}")

        return " AND ".join(parti)

    # GEOMETRIE — CONVERSIONE WKT

    def _wrap_geometry_as_wkt(self, col_ref: str) -> str:
        """
        Wrappa una colonna geometrica con la funzione ST_AsText appropriata.

        Usa sql_translator per tradurre nel dialetto corretto del database.
        Fallback su sintassi PostGIS standard se il database non è nel registry.
        """
        db = self.db_type.lower() if self.db_type else 'postgres'

        try:
            return sql_translator.translate('ST_AsText', db, geom=col_ref)
        except (ValueError, KeyError):
            return f"ST_AsText({col_ref})"

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

    def _genera_where(self, table_nodes: List[Node], params: Dict[str, Any]) -> str:
        return self._where_builder().generate_where(table_nodes, params)

    def _crea_condizione(
        self,
        table_alias: str,
        column: str,
        operator: str,
        value: Any,
        params: Dict[str, Any],
        column_type: str = 'str'
    ) -> str:
        return self._where_builder().create_condition(
            table_alias, column, operator, value, params, column_type
        )

    _SQL_FUNCTION_RE = None  # mantenuto per compatibilita' con call-site privati

    @classmethod
    def _is_sql_function(cls, value: str) -> bool:
        if cls._SQL_FUNCTION_RE is not None:
            WhereClauseBuilder._SQL_FUNCTION_RE = cls._SQL_FUNCTION_RE
        result = WhereClauseBuilder.is_sql_function(value)
        cls._SQL_FUNCTION_RE = WhereClauseBuilder._SQL_FUNCTION_RE
        return result

    def _translate_date_function(self, value: str) -> str:
        return self._where_builder().translate_date_function(value)

    def _crea_condizione_in(self, col_ref: str, value: Any, params: Dict[str, Any]) -> str:
        return self._where_builder().create_condition_in(col_ref, value, params)

    def _where_builder(self) -> WhereClauseBuilder:
        return WhereClauseBuilder(
            self.db_type,
            self._prossimo_nome_param,
            self._is_valid_identifier,
            self._get_node_table_name,
        )

    # ASSEMBLAGGIO QUERY

    def _assembla_query(self, sql_parts: Dict) -> str:
        """
        Assembla le parti SQL in una query completa.

        Ordine: SELECT [DISTINCT] colonne FROM tabelle [JOIN...]
                [WHERE...] [GROUP BY...] [HAVING...] [ORDER BY...] [LIMIT]

        La sintassi LIMIT varia per database:
        - PostgreSQL, MySQL, SQLite: LIMIT N (in fondo)
        - Oracle 12c+: FETCH FIRST N ROWS ONLY (in fondo)
        - SQL Server: SELECT TOP N (dopo SELECT, inserito tramite replace)
        """
        query = "SELECT "

        # DISTINCT (non applicato se c'è GROUP BY
        if sql_parts.get('distinct') and not sql_parts.get('group_by'):
            query += "DISTINCT "

        query += sql_parts['select']
        query += f"\nFROM {sql_parts['from']}"

        if sql_parts['where']:
            query += f"\nWHERE {sql_parts['where']}"

        if sql_parts.get('group_by'):
            query += f"\nGROUP BY {sql_parts['group_by']}"

        if sql_parts.get('having'):
            query += f"\nHAVING {sql_parts['having']}"

        if sql_parts.get('order_by'):
            query += f"\nORDER BY {sql_parts['order_by']}"

        # LIMIT con sintassi database-specifica
        limit = sql_parts.get('limit')
        if limit is not None:
            if not isinstance(limit, int) or limit < 0:
                raise ValueError(f"LIMIT deve essere un intero positivo, ricevuto: {limit}")

            db = self.db_type.lower() if self.db_type else 'postgres'

            if db == 'oracle':
                query += f"\nFETCH FIRST {limit} ROWS ONLY"
            elif db in ('mssql', 'sqlserver'):
                query = query.replace("SELECT ", f"SELECT TOP {limit} ", 1)
            else:
                query += f"\nLIMIT {limit}"

        return query

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
