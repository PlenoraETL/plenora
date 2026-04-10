"""
Generatore di query SQL dal canvas grafico.

Architettura
============
Questo modulo analizza il grafo visuale del canvas (nodi tabella, edges di join,
nodo risultato) e genera la corrispondente query SELECT SQL con protezione
contro SQL injection tramite parametri preparati.

Pipeline di generazione:
1. Riceve nodi, edges e ID del nodo risultato
2. Percorre il grafo all'indietro dal risultato per trovare le tabelle connesse
3. Estrae configurazione GROUP BY dal nodo risultato (se presente)
4. Genera separatamente: SELECT, FROM+JOIN, WHERE, GROUP BY
5. Assembla il tutto in una query completa con sintassi LIMIT specifica per database

Protezione SQL injection:
- Tutti i valori dei filtri usano parametri preparati (:p_0, :p_1, ...)
- Nomi di colonne e tabelle vengono validati con _is_valid_identifier()
- Operatori filtro sono limitati a un set di operatori sicuri

Supporto JOIN:
- Join su attributi: LEFT, RIGHT, INNER, FULL OUTER, CROSS
- Join spaziali: usa sql_translator per generare predicati multi-database
  (ST_Intersects, ST_Contains, ST_Within, ST_DWithin con buffer, ecc.)

Supporto GROUP BY:
- Colonne di raggruppamento
- Funzioni aggregate: COUNT, SUM, AVG, MIN, MAX, FIRST, LAST
- COUNT(*) per conteggio totale righe per gruppo

Colonne calcolate:
- Tradotte tramite sql_functions.translate_calculated_column()
- Supporto SQL manuale (is_manual=true) e funzioni database-agnostic

Sintassi LIMIT per database:
- PostgreSQL, MySQL, SQLite: LIMIT N
- SQL Server: SELECT TOP N
- Oracle 12c+: FETCH FIRST N ROWS ONLY
"""

import re
from app.utils.logger import get_logger
from typing import List, Dict, Optional, Any, Tuple

from app.models import Node, Edge
from app.db.sql_functions import translate_calculated_column, get_translator
from app.db.sql_translator import sql_translator

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
        # Contatore incrementale per generare nomi parametri unici (:p_0, :p_1, ...)
        self._param_counter = 0
        # Tipo database per la traduzione di funzioni SQL
        self.db_type = db_type
        # Schema database (es. dbo per SQL Server, public per PostgreSQL)
        self.db_schema = None

    # =========================================================================
    # METODO PRINCIPALE
    # =========================================================================

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
        
        # Imposta lo schema (con default per database type)
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
        
        # Riordina i nodi per rispettare le dipendenze dei JOIN:
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
        # elif db == 'postgres':
        #     return 'public'
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

    # =========================================================================
    # CONFIGURAZIONE GROUP BY
    # =========================================================================

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

    # =========================================================================
    # NAVIGAZIONE GRAFO
    # =========================================================================

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

            # Trova edges entranti (target = nodo corrente)
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
        # (escludi edges verso resultNode, carica, ecc.)
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

    # =========================================================================
    # GENERAZIONE CLAUSOLA SELECT
    # =========================================================================

    def _genera_select(
        self,
        table_nodes: List[Node],
        result_node: Node,
        group_by_config: Dict[str, Any] = None
    ) -> str:
        """
        Genera la clausola SELECT con colonne visibili, alias e colonne calcolate.

        Se GROUP BY è attivo, delega a _genera_select_con_groupby().
        Altrimenti genera un SELECT normale con:
        - Colonne visibili (con eventuale alias)
        - Colonne geometriche wrappate in ST_AsText()
        - Colonne calcolate tradotte tramite sql_functions
        """
        # Se GROUP BY è attivo, usa il generatore specifico
        if group_by_config and group_by_config.get("enabled"):
            return self._genera_select_con_groupby(table_nodes, group_by_config)

        # SELECT normale (senza GROUP BY)
        columns = []

        for node in table_nodes:
            table_alias = node.alias or self._get_node_table_name(node)

            # --- datasource_node senza colonne configurate → SELECT table.* ---
            if node.type == 'datasource_node':
                visible_cols = [c for c in (node.columns or []) if getattr(c, 'visible', True)]
                if not visible_cols:
                    columns.append(f"{table_alias}.*")
                    continue

            # --- Colonne normali ---
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

            # --- Colonne calcolate ---
            calculated_columns = getattr(node, 'calculated_columns', None) or []
            for calc_col in calculated_columns:
                try:
                    if calc_col.get('is_manual') and calc_col.get('sql'):
                        # SQL manuale: usa l'espressione così com'è
                        sql_expr = calc_col['sql']
                    elif calc_col.get('function_name') and calc_col.get('params'):
                        # Formato preferito: function_name + params (database-agnostic)
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
                        # Fallback: expression come dizionario (formato legacy)
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

        # Costruisci mappa colonna → alias tabella per qualificare.
        # Filtra per visible=True, stesso criterio di _genera_select_con_groupby,
        # così SELECT e GROUP BY usano sempre la stessa tabella per ogni colonna.
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

    # =========================================================================
    # ORDER BY
    # =========================================================================

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

        # Set delle funzioni aggregate valide (per ORDER BY con GROUP BY)
        aggregate_functions = {"COUNT", "SUM", "AVG", "MIN", "MAX"}

        parti = []
        for item in columns:
            col = item.get("column", "") if isinstance(item, dict) else str(item)
            direction = item.get("direction", "ASC") if isinstance(item, dict) else "ASC"

            # Normalizza direzione
            direction = direction.upper()
            if direction not in ("ASC", "DESC"):
                direction = "ASC"

            # Controlla se è un'espressione aggregata (es. "COUNT(*)", "SUM(area)")
            col_upper = col.upper().strip()
            is_aggregate = any(col_upper.startswith(f"{fn}(") for fn in aggregate_functions)

            if is_aggregate:
                # Espressione aggregata: usa così com'è
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

    # =========================================================================
    # HAVING
    # =========================================================================

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

    # =========================================================================
    # GEOMETRIE — CONVERSIONE WKT
    # =========================================================================

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

    # =========================================================================
    # GENERAZIONE CLAUSOLA FROM + JOIN
    # =========================================================================

    def _genera_from(self, table_nodes: List[Node], edges: List[Edge]) -> str:
        """
        Genera clausola FROM con eventuali JOIN.

        La prima tabella diventa il FROM principale, le successive vengono
        aggiunte come JOIN in base alla configurazione degli edges.
        
        IMPORTANTE: I nomi tabella vengono qualificati con lo schema se presente.
        Es: 'dbo.ac' per SQL Server
        
        Traccia i nodi già processati per permettere a _genera_join di scegliere
        l'edge corretto quando un nodo ha più connessioni.
        """
        if not table_nodes:
            raise ValueError("Nessuna tabella selezionata")

        # Prima tabella → FROM (qualificata con schema)
        first_node = table_nodes[0]
        raw_name = self._get_node_table_name(first_node)
        qualified_table_name = self._qualify_table_name(raw_name) if first_node.type != 'datasource_node' else raw_name
        from_clause = qualified_table_name
        if first_node.alias:
            from_clause += f" AS {first_node.alias}"

        # Traccia nodi già nel FROM per scegliere l'edge corretto
        processed_ids = {first_node.id}

        # Tabelle successive → JOIN
        for node in table_nodes[1:]:
            join_str = self._genera_join(node, edges, table_nodes, processed_ids)
            from_clause += f"\n{join_str}"
            processed_ids.add(node.id)

        return from_clause

    def _genera_join(
        self,
        node: Node,
        edges: List[Edge],
        all_nodes: List[Node],
        processed_ids: set = None
    ) -> str:
        """
        Genera una singola clausola JOIN per un nodo tabella.

        Supporta due tipi di join:
        - Join su attributi: ON source.col = target.col
        - Join spaziali: ON ST_Intersects(source.geom, target.geom)
          con supporto per buffer distance via sql_translator

        Se non trova un edge per questo nodo, genera CROSS JOIN.
        
        IMPORTANTE: I nomi tabella vengono qualificati con lo schema se presente.
        Gestisce correttamente edge invertiti (quando il nodo corrente è edge.source
        anziché edge.target).
        
        Quando processed_ids è fornito, preferisce edges che collegano a nodi
        già presenti nel FROM/JOIN, evitando riferimenti a tabelle non ancora joinata.
        """
        # Cerca l'edge che collega questo nodo — preferisci edge verso nodi GIÀ processati
        edge = None
        
        if processed_ids:
            # PRIORITÀ 1: edge dove questo nodo è target E source è già processato
            edge = next(
                (e for e in edges 
                 if e.target == node.id and e.source in processed_ids),
                None
            )
            # PRIORITÀ 2: edge dove questo nodo è source E target è già processato
            if not edge:
                edge = next(
                    (e for e in edges 
                     if e.source == node.id and e.target in processed_ids),
                    None
                )
        
        if not edge:
            # Fallback originale: qualsiasi edge collegato a questo nodo
            edge = next(
                (e for e in edges if e.target == node.id and e.source != node.id),
                next(
                    (e for e in edges if e.source == node.id and e.target != node.id),
                    None
                )
            )

        # Qualifica il nome tabella con lo schema
        raw_name = self._get_node_table_name(node)
        qualified_table_name = self._qualify_table_name(raw_name) if node.type != 'datasource_node' else raw_name

        if not edge:
            # Nessun edge trovato → CROSS JOIN
            if node.alias:
                return f"CROSS JOIN {qualified_table_name} AS {node.alias}"
            return f"CROSS JOIN {qualified_table_name}"

        # Determina tipo JOIN
        join_type = (edge.join_type or 'LEFT').upper()

        if join_type not in ('LEFT', 'RIGHT', 'INNER', 'OUTER', 'FULL', 'FULL OUTER', 'CROSS'):
            join_type = 'LEFT'
        # Normalizza FULL e OUTER → FULL OUTER (sintassi SQL standard)
        if join_type in ('FULL', 'OUTER'):
            join_type = 'FULL OUTER'

        table_alias = node.alias or self._get_node_table_name(node)

        # Determina il nodo "altro" (quello già nel FROM / JOIN precedenti)
        # e se l'edge è invertito rispetto alla direzione del JOIN
        if edge.target == node.id:
            # Caso normale: edge punta VERSO questo nodo
            other_node_id = edge.source
            is_reversed = False
        else:
            # Caso invertito: edge parte DA questo nodo
            other_node_id = edge.target
            is_reversed = True

        other_node = next((n for n in all_nodes if n.id == other_node_id), None)
        source_alias = other_node.alias or other_node.table_name if other_node else 'unknown'

        # Determina se è un join spaziale o attributivo
        is_spatial = getattr(edge, 'is_spatial', False)

        if is_spatial:
            if is_reversed:
                on_clause = self._genera_on_spaziale(edge, table_alias, source_alias)
            else:
                on_clause = self._genera_on_spaziale(edge, source_alias, table_alias)
        else:
            on_clause = self._genera_on_attributivo(
                edge, source_alias, table_alias, node, join_type, is_reversed
            )

        # Assembla la clausola JOIN
        join_str = f"{join_type} JOIN {qualified_table_name}"
        if node.alias:
            join_str += f" AS {node.alias}"
        join_str += f" ON {on_clause}"

        return join_str

    def _genera_on_spaziale(self, edge: Edge, source_alias: str, table_alias: str) -> str:
        """
        Genera la condizione ON per un join spaziale.

        Usa sql_translator per produrre la sintassi corretta per ogni database:
        - Con distanza: build_buffer_join() → ST_Intersects(a.geom, ST_Buffer(b.geom, dist))
        - Senza distanza: get_spatial_predicate() → ST_Intersects(a.geom, b.geom)
        """
        spatial_predicate = getattr(edge, 'spatial_predicate', 'ST_Intersects')
        source_geom = getattr(edge, 'source_geom_column', 'geom')
        target_geom = getattr(edge, 'target_geom_column', 'geom')
        spatial_distance = getattr(edge, 'spatial_distance', None)

        if not self._is_valid_identifier(source_geom):
            raise ValueError(f"Colonna geometria non valida: {source_geom}")
        if not self._is_valid_identifier(target_geom):
            raise ValueError(f"Colonna geometria non valida: {target_geom}")

        db = self.db_type.lower() if self.db_type else 'postgres'
        geom1 = f"{source_alias}.{source_geom}"
        geom2 = f"{table_alias}.{target_geom}"

        try:
            predicate_key = spatial_predicate.replace('ST_', '').lower()

            if predicate_key == 'dwithin':
                # ST_DWithin: la distanza è il terzo parametro, non un buffer
                # ST_DWithin(geom1, geom2, distance) ≠ ST_Intersects(geom1, ST_Buffer(geom2, dist))
                dist = spatial_distance or 0
                return sql_translator.get_spatial_predicate(
                    predicate='dwithin',
                    db_type=db,
                    geom1=geom1, geom2=geom2,
                    distance=dist
                )
            else:
                # Tutti gli altri predicati (intersects, contains, within, touches, overlaps)
                # non usano distanza — spatial_distance viene ignorato se presente
                return sql_translator.get_spatial_predicate(
                    predicate=predicate_key,
                    db_type=db,
                    geom1=geom1, geom2=geom2
                )
        except (ValueError, KeyError, AttributeError):
            # Fallback: sintassi PostGIS standard
            if spatial_predicate == 'ST_DWithin' and spatial_distance:
                return f"ST_DWithin({geom1}, {geom2}, {spatial_distance})"
            return f"{spatial_predicate}({geom1}, {geom2})"

    def _genera_on_attributivo(
        self,
        edge: Edge,
        source_alias: str,
        table_alias: str,
        node: Node,
        join_type: str,
        is_reversed: bool = False
    ) -> str:
        """
        Genera la condizione ON per un join su attributi.

        Supporta condizioni multiple (AND):
        - Nuovo formato: edge.join_conditions = [{source_column, target_column}, ...]
        - Backward compatibility: edge.source_column + edge.target_column (singola coppia)
        - Filtri ON aggiuntivi: edge.on_filters = [{column, operator, value, func}, ...]

        Se le colonne non sono specificate, ritorna "1=1" (sempre vero).
        
        Args:
            is_reversed: Se True, l'edge è invertito (il nodo corrente è edge.source
                anziché edge.target), quindi source_column e target_column vanno swappati.
                I filtri ON (on_filters) si riferiscono sempre alla tabella target dell'edge,
                quindi quando invertito usano source_alias anziché table_alias.
        """
        parts = []
        
        # Nuovo formato: condizioni multiple
        join_conditions = getattr(edge, 'join_conditions', None)
        
        if join_conditions and len(join_conditions) > 0:
            # Filtra condizioni valide
            valid_conditions = [
                c for c in join_conditions 
                if c.get('source_column') and c.get('target_column')
            ]
            
            for cond in valid_conditions:
                if is_reversed:
                    # Edge invertito: source_column dell'edge si riferisce al nodo corrente (table_alias)
                    # e target_column si riferisce al nodo già processato (source_alias)
                    src_col = cond['target_column']
                    tgt_col = cond['source_column']
                    src_func = cond.get('target_func', '')
                    tgt_func = cond.get('source_func', '')
                else:
                    src_col = cond['source_column']
                    tgt_col = cond['target_column']
                    src_func = cond.get('source_func', '')
                    tgt_func = cond.get('target_func', '')
                
                if not self._is_valid_identifier(src_col):
                    raise ValueError(f"Colonna JOIN non valida: {src_col}")
                if not self._is_valid_identifier(tgt_col):
                    raise ValueError(f"Colonna JOIN non valida: {tgt_col}")
                
                src_expr = self._wrap_col_with_func(f"{source_alias}.{src_col}", src_func)
                tgt_expr = self._wrap_col_with_func(f"{table_alias}.{tgt_col}", tgt_func)
                parts.append(f"{src_expr} = {tgt_expr}")
        else:
            # Backward compatibility: singola coppia
            source_col = edge.source_column
            target_col = edge.target_column

            if source_col and target_col:
                if is_reversed:
                    source_col, target_col = target_col, source_col
                    
                if not self._is_valid_identifier(source_col):
                    raise ValueError(f"Colonna JOIN non valida: {source_col}")
                if not self._is_valid_identifier(target_col):
                    raise ValueError(f"Colonna JOIN non valida: {target_col}")
                parts.append(f"{source_alias}.{source_col} = {table_alias}.{target_col}")

        # Filtri ON aggiuntivi
        # I filtri sono configurati sulle colonne della tabella TARGET dell'edge.
        # Se l'edge è invertito, la tabella target dell'edge è il nodo "altro" (source_alias),
        # non il nodo corrente (table_alias).
        on_filters = getattr(edge, 'on_filters', None)
        if on_filters and len(on_filters) > 0:
            filter_table_alias = source_alias if is_reversed else table_alias
            
            for f in on_filters:
                col = f.get('column', '')
                operator = f.get('operator', '')
                value = f.get('value', '')
                func = f.get('func', '')
                
                if not col:
                    continue
                if not self._is_valid_identifier(col):
                    raise ValueError(f"Colonna filtro ON non valida: {col}")
                
                # Costruisci espressione colonna con funzione opzionale
                col_expr = self._wrap_col_with_func(f"{filter_table_alias}.{col}", func)
                
                # Validazione operatore: whitelist strict.
                # Senza whitelist, un utente potrebbe iniettare un operatore
                # arbitrario (es. "= '1'; DROP TABLE --") nel canvas.
                _VALID_ON_OPERATORS = {
                    '=', '!=', '<>', '>', '<', '>=', '<=',
                    'LIKE', 'IN', 'IS NULL', 'IS NOT NULL',
                }
                if operator not in _VALID_ON_OPERATORS:
                    raise ValueError(f"Operatore ON filter non valido: {operator}")

                # Costruisci condizione con escaping SQL strict.
                # NOTA: idealmente i filtri ON dovrebbero usare parametri
                # preparati come i filtri WHERE (_crea_condizione). Ma i JOIN ON
                # sono assemblati come stringa pura (pre-refactor). Il replace
                # ("'","''") è il metodo standard SQL per escaping di apici
                # singoli e previene injection base.
                if operator in ('IS NULL', 'IS NOT NULL'):
                    parts.append(f"{col_expr} {operator}")
                elif operator == 'IN':
                    # Sanitizza OGNI valore della lista IN con escape apici
                    in_values = [v.strip() for v in value.split(',') if v.strip()]
                    if in_values:
                        in_list = ", ".join(
                            f"'{v.replace(chr(39), chr(39)+chr(39))}'"
                            for v in in_values
                        )
                        parts.append(f"{col_expr} IN ({in_list})")
                else:
                    safe_value = value.replace("'", "''")
                    parts.append(f"{col_expr} {operator} '{safe_value}'")

        if not parts:
            return "1=1"
        
        return " AND ".join(parts)

    # =========================================================================
    # GENERAZIONE CLAUSOLA WHERE
    # =========================================================================

    def _genera_where(self, table_nodes: List[Node], params: Dict[str, Any]) -> str:
        """
        Genera clausola WHERE da tutti i filtri dei nodi tabella.

        Ogni filtro diventa una condizione parametrizzata.
        Le condizioni sono combinate con AND.
        """
        conditions = []

        for node in table_nodes:
            table_alias = node.alias or self._get_node_table_name(node)
            filters = getattr(node, 'filters', []) or []

            # Costruisci mappa colonna → tipo per gestire CLOB Oracle
            column_type_map = {}
            for col in node.columns:
                col_name = col.name if hasattr(col, 'name') else col.get('name')
                col_type = (
                    (col.type if hasattr(col, 'type') else None)
                    or (col.get('type') if isinstance(col, dict) else None)
                    or 'str'
                )
                if col_name:
                    column_type_map[col_name] = col_type


            for filter_item in filters:
                column = filter_item.column
                operator = filter_item.operator
                value = filter_item.value

                if not column or not operator:
                    continue
                if not self._is_valid_identifier(column):
                    raise ValueError(f"Nome colonna filtro non valido: {column}")

                column_type = column_type_map.get(column, 'str')

                condition = self._crea_condizione(
                    table_alias, column, operator, value, params, column_type
                )
                if condition:
                    conditions.append(condition)

        if not conditions:
            return ""

        return " AND ".join(conditions)

    def _crea_condizione(
        self,
        table_alias: str,
        column: str,
        operator: str,
        value: Any,
        params: Dict[str, Any],
        column_type: str = 'str'
    ) -> str:
        """
        Crea una singola condizione WHERE con parametri preparati.

        Operatori supportati: =, !=, >, <, >=, <=, LIKE, IN, IS NULL, IS NOT NULL

        Gestione speciale per Oracle:
        - Colonne CLOB/NCLOB vengono wrappate con TO_CHAR() perché Oracle
          non permette confronti diretti su tipi LOB.
        """
        col_ref = f"{table_alias}.{column}"

        # Oracle: wrappa colonne LOB con TO_CHAR() per permettere confronti
        db = self.db_type.lower() if self.db_type else 'postgres'
        col_type_upper = (column_type or '').upper()
        is_oracle_lob = (
            db == 'oracle'
            and col_type_upper in ('CLOB', 'NCLOB', 'LONG', 'TEXT', 'TESTO', 'STRING', 'STR')
        )
        if is_oracle_lob:
            col_ref = f"TO_CHAR({table_alias}.{column})"

        # Validazione operatore
        valid_operators = {'=', '!=', '>', '<', '>=', '<=', 'LIKE', 'IN', 'IS NULL', 'IS NOT NULL'}
        if operator not in valid_operators:
            raise ValueError(f"Operatore non valido: {operator}")

        # --- Operatori senza valore ---
        if operator == 'IS NULL':
            return f"{col_ref} IS NULL"
        if operator == 'IS NOT NULL':
            return f"{col_ref} IS NOT NULL"

        # --- Operatore IN (gestisce lista e stringa con virgole) ---
        if operator == 'IN':
            return self._crea_condizione_in(col_ref, value, params)

        # --- Operatore LIKE (aggiunge automaticamente % intorno al valore) ---
        if operator == 'LIKE':
            param_name = self._prossimo_nome_param()
            params[param_name] = f"%{value}%"
            return f"{col_ref} LIKE :{param_name}"

        # --- Operatori standard (=, !=, >, <, >=, <=) ---

        # Se il valore è una funzione SQL nativa (es. GETDATE(), DATEADD(...), NOW())
        # lo inietta inline invece di parametrizzarlo — i parametri bind non supportano
        # espressioni SQL, solo valori scalari.
        if isinstance(value, str) and self._is_sql_function(value):
            translated = self._translate_date_function(value)
            return f"{col_ref} {operator} {translated}"

        param_name = self._prossimo_nome_param()
        # Converti il valore al tipo Python corretto in base al tipo colonna.
        # I valori arrivano sempre come stringa dal frontend — senza conversione
        # PostgreSQL riceve '50' come stringa e fa confronto lessicografico invece
        # che numerico (es. id < '50' esclude '6','7',... che sono > '50' come str).
        col_type_norm = (column_type or 'str').lower().split(':')[0]
        typed_value = value
        if col_type_norm in ('int', 'integer', 'bigint', 'smallint'):
            try:
                typed_value = int(value)
            except (ValueError, TypeError):
                typed_value = value
        elif col_type_norm in ('real', 'float', 'double', 'numeric', 'decimal'):
            try:
                typed_value = float(value)
            except (ValueError, TypeError):
                typed_value = value
        params[param_name] = typed_value
        return f"{col_ref} {operator} :{param_name}"

    _SQL_FUNCTION_RE = None  # inizializzato al primo uso

    @classmethod
    def _is_sql_function(cls, value: str) -> bool:
        """
        Ritorna True se il valore è una funzione SQL nativa da iniettare inline.
        Queste espressioni non possono essere parametrizzate — vanno inserite raw nell'SQL.
        Supporta: SQL Server, PostgreSQL, MySQL, Oracle.
        """
        import re
        if cls._SQL_FUNCTION_RE is None:
            cls._SQL_FUNCTION_RE = re.compile(
                r'^('
                r'GETDATE\s*\(\s*\)'
                r'|GETUTCDATE\s*\(\s*\)'
                r'|SYSDATETIME\s*\(\s*\)'
                r'|DATEADD\s*\('
                r'|DATEDIFF\s*\('
                r'|DATEPART\s*\('
                r'|DATEFROMPARTS\s*\('
                r'|EOMONTH\s*\('
                r'|NOW\s*\(\s*\)'
                r'|CURDATE\s*\(\s*\)'
                r'|CURRENT_DATE\b'
                r'|CURRENT_TIMESTAMP\b'
                r'|CURRENT_TIME\b'
                r'|SYSDATE\b'
                r'|SYSTIMESTAMP\b'
                r'|ADD_MONTHS\s*\('
                r'|TRUNC\s*\('
                r'|date_trunc\s*\('
                r'|INTERVAL\b'
                r'|EXTRACT\s*\('
                r'|DATE_ADD\s*\('
                r'|DATE_SUB\s*\('
                r'|DATE_FORMAT\s*\('
                r')',
                re.IGNORECASE
            )
        return bool(cls._SQL_FUNCTION_RE.match(value.strip()))

    def _translate_date_function(self, value: str) -> str:
        """
        Traduce una funzione data SQL nel dialetto corretto per self.db_type.

        Normalizza funzioni di qualsiasi dialetto (GETDATE, NOW, SYSDATE, ecc.)
        in quella corretta per il DB attivo — così il canvas funziona su tutti i DB
        indipendentemente da quale funzione ha generato Claude.

        Mapping astratto → per-DB:
            NOW()                 → GETDATE() / NOW() / SYSDATE / NOW()
            NOW + N months        → DATEADD / NOW()+INTERVAL / ADD_MONTHS / DATE_ADD
            TODAY (date only)     → CAST(GETDATE() AS DATE) / CURRENT_DATE / TRUNC(SYSDATE)
        """
        import re

        v = value.strip()
        db = (self.db_type or 'postgres').lower()

        # Mappa db_type → (now_fn, today_fn, add_months_fn_template)
        _DIALECTS = {
            'mssql':    {
                'now':       'GETDATE()',
                'today':     'CAST(GETDATE() AS DATE)',
                'add_months': lambda n: f'DATEADD(month, {n}, GETDATE())',
                'add_days':   lambda n: f'DATEADD(day, {n}, GETDATE())',
            },
            'postgres': {
                'now':       'NOW()',
                'today':     'CURRENT_DATE',
                'add_months': lambda n: f"NOW() + INTERVAL '{n} months'",
                'add_days':   lambda n: f"NOW() + INTERVAL '{n} days'",
            },
            'mysql': {
                'now':       'NOW()',
                'today':     'CURDATE()',
                'add_months': lambda n: f'DATE_ADD(NOW(), INTERVAL {n} MONTH)',
                'add_days':   lambda n: f'DATE_ADD(NOW(), INTERVAL {n} DAY)',
            },
            'oracle': {
                'now':       'SYSDATE',
                'today':     'TRUNC(SYSDATE)',
                'add_months': lambda n: f'ADD_MONTHS(SYSDATE, {n})',
                'add_days':   lambda n: f'SYSDATE + {n}',
            },
            'sqlite': {
                'now':       "datetime('now')",
                'today':     "date('now')",
                'add_months': lambda n: f"datetime('now', '+{n} months')",
                'add_days':   lambda n: f"datetime('now', '+{n} days')",
            },
        }
        dialect = _DIALECTS.get(db, _DIALECTS['postgres'])

        # DATEADD(month, N, ...) → add_months
        m = re.match(r'DATEADD\s*\(\s*month\s*,\s*(\d+)\s*,', v, re.IGNORECASE)
        if m:
            return dialect['add_months'](m.group(1))

        # DATEADD(day, N, ...) → add_days
        m = re.match(r'DATEADD\s*\(\s*day\s*,\s*(\d+)\s*,', v, re.IGNORECASE)
        if m:
            return dialect['add_days'](m.group(1))

        # ADD_MONTHS(SYSDATE, N) → add_months
        m = re.match(r'ADD_MONTHS\s*\(\s*\w+\s*,\s*(\d+)\s*\)', v, re.IGNORECASE)
        if m:
            return dialect['add_months'](m.group(1))

        # DATE_ADD(NOW(), INTERVAL N MONTH) → add_months
        m = re.match(r'DATE_ADD\s*\(.*?INTERVAL\s+(\d+)\s+MONTH', v, re.IGNORECASE)
        if m:
            return dialect['add_months'](m.group(1))

        # NOW() + INTERVAL 'N months' (PostgreSQL) → add_months
        m = re.match(r"NOW\s*\(\s*\)\s*\+\s*INTERVAL\s+'(\d+)\s+months?'", v, re.IGNORECASE)
        if m:
            return dialect['add_months'](m.group(1))

        # CURRENT_DATE / CURDATE() / TRUNC(SYSDATE) → today
        if re.match(r'CURRENT_DATE|CURDATE\s*\(\s*\)|TRUNC\s*\(\s*SYSDATE', v, re.IGNORECASE):
            return dialect['today']

        # Qualsiasi funzione "now" (GETDATE, NOW, SYSDATE, GETUTCDATE, SYSDATETIME) → now
        if re.match(r'GETDATE\s*\(\s*\)|GETUTCDATE\s*\(\s*\)|SYSDATETIME\s*\(\s*\)|NOW\s*\(\s*\)|SYSDATE|SYSTIMESTAMP', v, re.IGNORECASE):
            return dialect['now']

        # Fallback: inietta il valore così com'è (meglio che un errore)
        return v

    def _crea_condizione_in(self, col_ref: str, value: Any, params: Dict[str, Any]) -> str:
        """
        Genera condizione IN con parametri preparati.

        Accetta sia una lista Python sia una stringa con valori separati da virgola.
        I valori numerici vengono automaticamente convertiti in int.
        """
        # Converte stringa CSV in lista
        if isinstance(value, str) and ',' in value:
            value = [v.strip() for v in value.split(',') if v.strip()]

        if isinstance(value, list):
            placeholders = []
            for v in value:
                param_name = self._prossimo_nome_param()
                try:
                    params[param_name] = int(v) if isinstance(v, str) and v.isdigit() else v
                except (ValueError, TypeError):
                    params[param_name] = v
                placeholders.append(f":{param_name}")
            return f"{col_ref} IN ({', '.join(placeholders)})"
        else:
            param_name = self._prossimo_nome_param()
            try:
                params[param_name] = int(value) if isinstance(value, str) and value.isdigit() else value
            except (ValueError, TypeError):
                params[param_name] = value
            return f"{col_ref} IN (:{param_name})"

    # =========================================================================
    # ASSEMBLAGGIO QUERY
    # =========================================================================

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

        # DISTINCT (non applicato se c'è GROUP BY)
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

    # =========================================================================
    # UTILITÀ
    # =========================================================================

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