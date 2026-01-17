"""
Service per generare query SQL dal canvas
Analizza nodi, edges, filtri e join per produrre SELECT statement

SICUREZZA: Usa parametri preparati per prevenire SQL injection
"""
from typing import List, Dict, Optional, Any, Tuple
from models import Node, Edge
from services.sql_functions import translate_calculated_column, get_translator


class QueryGenerator:
    """Genera query SQL dal canvas state con protezione SQL injection"""
    
    def __init__(self, db_type: str = 'postgres'):
        # Contatore per generare nomi parametri unici
        self._param_counter = 0
        # Tipo database per traduzione funzioni
        self.db_type = db_type
    
    def generate_sql(
        self, 
        nodes: List[Node], 
        edges: List[Edge],
        result_node_id: str,
        db_type: str = None
    ) -> Dict[str, Any]:
        """
        Genera query SQL dal canvas
        
        Args:
            nodes: Lista nodi del canvas
            edges: Lista edges (join) del canvas
            result_node_id: ID del nodo risultato (punto finale)
            db_type: Tipo database (postgres, mssql, etc.) per traduzione funzioni
            
        Returns:
            Dict con {
                "sql": "SELECT ... WHERE col = :p_0",
                "params": {"p_0": "valore"},
                "tables_used": ["table1", "table2"],
                "columns_count": 10
            }
        """
        # Reset contatore parametri per ogni nuova query
        self._param_counter = 0
        
        # Usa db_type passato o quello di default
        if db_type:
            self.db_type = db_type
        
        # Dizionario per raccogliere tutti i parametri
        params = {}
        
        # Trova nodo risultato
        result_node = next((n for n in nodes if n.id == result_node_id), None)
        if not result_node:
            raise ValueError(f"Nodo risultato '{result_node_id}' non trovato")
        
        # Trova tabelle connesse al risultato (percorso inverso)
        table_nodes = self._find_connected_tables(nodes, edges, result_node_id)
        table_nodes.reverse()
        
        if not table_nodes:
            raise ValueError("Nessuna tabella connessa al nodo risultato")
        
        # Genera SQL con parametri
        sql_parts = {
            "select": self._generate_select(table_nodes, result_node),
            "from": self._generate_from(table_nodes, edges),
            "where": self._generate_where(table_nodes, params),  # Passa params
            "distinct": result_node.distinct if hasattr(result_node, 'distinct') else False,
            "limit": result_node.limit if hasattr(result_node, 'limit') else None
        }
        
        # Assembla query
        sql = self._assemble_query(sql_parts)
        
        return {
            "sql": sql,
            "params": params,
            "tables_used": [n.table_name for n in table_nodes],
            "columns_count": len(self._get_visible_columns(table_nodes))
        }
    
    def _get_next_param_name(self) -> str:
        """Genera nome parametro univoco incrementale"""
        name = f"p_{self._param_counter}"
        self._param_counter += 1
        return name
    
    def _find_connected_tables(
        self, 
        nodes: List[Node], 
        edges: List[Edge], 
        result_node_id: str
    ) -> List[Node]:
        """
        Trova tutte le tabelle connesse al nodo risultato
        Percorre il grafo all'indietro partendo dal risultato
        """
        table_nodes = []
        visited = set()
        queue = [result_node_id]
        
        while queue:
            current_id = queue.pop(0)
            
            if current_id in visited:
                continue
            visited.add(current_id)
            
            # Trova edges che puntano a questo nodo (target = current)
            incoming_edges = [e for e in edges if e.target == current_id]
            
            for edge in incoming_edges:
                source_node = next((n for n in nodes if n.id == edge.source), None)
                
                if source_node and source_node.type != 'result':
                    # È un nodo tabella
                    if source_node not in table_nodes:
                        table_nodes.append(source_node)
                    
                    # Continua a risalire
                    queue.append(edge.source)
        
        return table_nodes
    
    def _generate_select(self, table_nodes: List[Node], result_node: Node) -> str:
        """Genera clausola SELECT con colonne visibili, alias e colonne calcolate.
        Le colonne geometriche vengono convertite in WKT per uniformità."""
        columns = []
        
        for node in table_nodes:
            table_alias = node.alias or node.table_name
            
            # Colonne normali
            for col in node.columns:
                if not col.visible:
                    continue  # Salta colonne nascoste
                
                col_name = col.name
                col_alias = col.alias
                col_type = getattr(col, 'type', 'str') or 'str'
                
                # Validazione nome colonna (previene injection nei nomi)
                if not self._is_valid_identifier(col_name):
                    raise ValueError(f"Nome colonna non valido: {col_name}")
                
                # Se è una colonna geometrica, wrappa con funzione WKT
                if col_type.startswith('geometry'):
                    col_expr = self._wrap_geometry_as_wkt(f"{table_alias}.{col_name}")
                    # Usa alias originale o nome colonna
                    output_alias = col_alias if col_alias else col_name
                    if not self._is_valid_identifier(output_alias):
                        raise ValueError(f"Alias colonna non valido: {output_alias}")
                    columns.append(f"{col_expr} AS {output_alias}")
                else:
                    # Colonna normale
                    if col_alias:
                        if not self._is_valid_identifier(col_alias):
                            raise ValueError(f"Alias colonna non valido: {col_alias}")
                        columns.append(f"{table_alias}.{col_name} AS {col_alias}")
                    else:
                        columns.append(f"{table_alias}.{col_name}")
            
            # Colonne calcolate
            calculated_columns = getattr(node, 'calculated_columns', None) or []
            for calc_col in calculated_columns:
                try:
                    # calc_col ha struttura: {name, expression, sql, output_type, is_manual}
                    if calc_col.get('is_manual') and calc_col.get('sql'):
                        # Usa SQL manuale (già validato dall'utente)
                        sql_expr = calc_col['sql']
                    elif calc_col.get('expression'):
                        # Traduci espressione in SQL per il db_type corrente
                        sql_expr = translate_calculated_column(
                            db_type=self.db_type,
                            column_name=calc_col['name'],
                            expression=calc_col['expression'],
                            table_alias=table_alias
                        )
                    else:
                        continue
                    
                    columns.append(sql_expr)
                    
                except Exception as e:
                    raise ValueError(f"Errore colonna calcolata '{calc_col.get('name', '?')}': {str(e)}")
        
        if not columns:
            raise ValueError("Nessuna colonna selezionata")
        
        return ", ".join(columns)
    
    def _wrap_geometry_as_wkt(self, col_ref: str) -> str:
        """
        Wrappa una colonna geometrica con la funzione appropriata per convertirla in WKT.
        
        Args:
            col_ref: Riferimento colonna (es. "table.geom")
            
        Returns:
            Espressione SQL che converte la geometria in WKT
        """
        # Funzioni per convertire geometrie in WKT per ogni DB
        wkt_functions = {
            'postgres': f"ST_AsText({col_ref})",      # PostGIS
            'mssql': f"{col_ref}.STAsText()",          # SQL Server
            'mysql': f"ST_AsText({col_ref})",          # MySQL
            'oracle': f"SDO_UTIL.TO_WKTGEOMETRY({col_ref})",  # Oracle Spatial
            'sqlite': f"AsText({col_ref})",            # SpatiaLite
        }
        
        return wkt_functions.get(self.db_type, f"ST_AsText({col_ref})")
    
    def _generate_from(self, table_nodes: List[Node], edges: List[Edge]) -> str:
        """
        Genera clausola FROM con JOIN
        Strategia: prima tabella come FROM, le altre come JOIN
        """
        if not table_nodes:
            return ""
        
        # Prima tabella è il FROM principale
        first_table = table_nodes[0]
        
        # Validazione nome tabella
        if not self._is_valid_identifier(first_table.table_name):
            raise ValueError(f"Nome tabella non valido: {first_table.table_name}")
        
        from_clause = first_table.table_name
        if first_table.alias:
            if not self._is_valid_identifier(first_table.alias):
                raise ValueError(f"Alias tabella non valido: {first_table.alias}")
            from_clause += f" AS {first_table.alias}"
        
        # Se c'è solo una tabella, finito
        if len(table_nodes) == 1:
            return from_clause
        
        # Altre tabelle come JOIN
        joins = []
        processed_tables = {first_table.id}
        
        for node in table_nodes[1:]:
            # Trova edge che connette questa tabella
            edge = self._find_edge_for_table(node.id, edges, processed_tables)
            
            if edge:
                join_clause = self._create_join_clause(node, edge, table_nodes)
                joins.append(join_clause)
                processed_tables.add(node.id)
        
        # Assembla FROM + JOINs
        result = from_clause
        if joins:
            result += "\n" + "\n".join(joins)
        
        return result
    
    def _find_edge_for_table(
        self, 
        table_id: str, 
        edges: List[Edge], 
        processed_tables: set
    ) -> Optional[Edge]:
        """Trova edge che connette questa tabella a quelle già processate"""
        for edge in edges:
            if edge.target == table_id and edge.source in processed_tables:
                return edge
            if edge.source == table_id and edge.target in processed_tables:
                return edge
        return None
    
    def _create_join_clause(
        self, 
        node: Node, 
        edge: Edge, 
        all_nodes: List[Node]
    ) -> str:
        """Crea clausola JOIN per una tabella (standard o spaziale)"""
        # Validazione tipo join
        valid_join_types = {'LEFT', 'RIGHT', 'INNER', 'OUTER'}
        join_type = edge.join_type.upper() if edge.join_type else 'LEFT'
        
        if join_type not in valid_join_types:
            raise ValueError(f"Tipo join non valido: {join_type}")
        
        # Nome tabella e alias
        table_name = node.table_name
        table_alias = node.alias or table_name
        
        # Validazione
        if not self._is_valid_identifier(table_name):
            raise ValueError(f"Nome tabella non valido: {table_name}")
        if node.alias and not self._is_valid_identifier(node.alias):
            raise ValueError(f"Alias tabella non valido: {node.alias}")
        
        # Trova nodo sorgente (l'altra tabella del join)
        source_node_id = edge.source if edge.target == node.id else edge.target
        source_node = next((n for n in all_nodes if n.id == source_node_id), None)
        
        if not source_node:
            raise ValueError(f"Nodo sorgente {source_node_id} non trovato per join")
        
        source_alias = source_node.alias or source_node.table_name
        
        # Verifica se è un join spaziale
        is_spatial = getattr(edge, 'is_spatial', False)
        
        if is_spatial:
            # === JOIN SPAZIALE ===
            spatial_predicate = getattr(edge, 'spatial_predicate', 'ST_Intersects')
            source_geom_col = getattr(edge, 'source_geom_column', 'geom')
            target_geom_col = getattr(edge, 'target_geom_column', 'geom')
            spatial_distance = getattr(edge, 'spatial_distance', None)
            
            # Validazione predicati spaziali
            valid_predicates = {'ST_Intersects', 'ST_Within', 'ST_Contains', 'ST_DWithin', 'ST_Touches', 'ST_Overlaps'}
            if spatial_predicate not in valid_predicates:
                raise ValueError(f"Predicato spaziale non valido: {spatial_predicate}")
            
            # Validazione colonne geometriche
            if not self._is_valid_identifier(source_geom_col):
                raise ValueError(f"Colonna geometrica non valida: {source_geom_col}")
            if not self._is_valid_identifier(target_geom_col):
                raise ValueError(f"Colonna geometrica non valida: {target_geom_col}")
            
            # Costruisci riferimenti geometria
            if edge.target == node.id:
                geom_a = f"{source_alias}.{source_geom_col}"
                geom_b = f"{table_alias}.{target_geom_col}"
            else:
                geom_a = f"{table_alias}.{target_geom_col}"
                geom_b = f"{source_alias}.{source_geom_col}"
            
            # Costruisci predicato
            if spatial_predicate == 'ST_DWithin':
                if spatial_distance is None:
                    spatial_distance = 100  # Default
                on_clause = f"{spatial_predicate}({geom_a}, {geom_b}, {spatial_distance})"
            else:
                on_clause = f"{spatial_predicate}({geom_a}, {geom_b})"
        else:
            # === JOIN STANDARD ===
            source_col = edge.source_column or 'id'
            target_col = edge.target_column or 'id'
            
            if not self._is_valid_identifier(source_col):
                raise ValueError(f"Colonna join non valida: {source_col}")
            if not self._is_valid_identifier(target_col):
                raise ValueError(f"Colonna join non valida: {target_col}")
            
            # Determina quale colonna appartiene a quale tabella
            if edge.target == node.id:
                on_clause = f"{source_alias}.{source_col} = {table_alias}.{target_col}"
            else:
                on_clause = f"{table_alias}.{target_col} = {source_alias}.{source_col}"
        
        # Assembla JOIN
        join_str = f"{join_type} JOIN {table_name}"
        if node.alias:
            join_str += f" AS {node.alias}"
        join_str += f" ON {on_clause}"
        
        return join_str
    
    def _generate_where(self, table_nodes: List[Node], params: Dict[str, Any]) -> str:
        """
        Genera clausola WHERE con filtri di tutti i nodi.
        USA PARAMETRI PREPARATI per prevenire SQL injection.
        
        Args:
            table_nodes: Lista nodi tabella con filtri
            params: Dizionario dove inserire i parametri (modificato in-place)
            
        Returns:
            Stringa WHERE con placeholder (es: "col = :p_0 AND col2 > :p_1")
        """
        conditions = []
        
        for node in table_nodes:
            table_alias = node.alias or node.table_name
            filters = node.filters or []
            
            for filter_item in filters:
                column = filter_item.column
                operator = filter_item.operator
                value = filter_item.value
                
                if not column or not operator:
                    continue
                
                # Validazione colonna
                if not self._is_valid_identifier(column):
                    raise ValueError(f"Nome colonna filtro non valido: {column}")
                
                # Genera condizione SQL con parametri sicuri
                condition = self._create_condition(table_alias, column, operator, value, params)
                if condition:
                    conditions.append(condition)
        
        if not conditions:
            return ""
        
        return " AND ".join(conditions)
    
    def _create_condition(
        self, 
        table_alias: str, 
        column: str, 
        operator: str, 
        value: Any,
        params: Dict[str, Any]
    ) -> str:
        """
        Crea singola condizione WHERE con parametri sicuri.
        
        IMPORTANTE: I valori vengono passati come parametri, MAI interpolati nella stringa SQL.
        
        Args:
            table_alias: Alias o nome tabella
            column: Nome colonna
            operator: Operatore SQL (=, !=, >, <, LIKE, IN, etc.)
            value: Valore da confrontare
            params: Dizionario parametri (modificato in-place)
            
        Returns:
            Stringa condizione con placeholder (es: "table.col = :p_0")
        """
        col_ref = f"{table_alias}.{column}"
        
        # Validazione operatore (whitelist)
        valid_operators = {'=', '!=', '>', '<', '>=', '<=', 'LIKE', 'IN', 'IS NULL', 'IS NOT NULL'}
        if operator not in valid_operators:
            raise ValueError(f"Operatore non valido: {operator}")
        
        # Operatori senza valore
        if operator == 'IS NULL':
            return f"{col_ref} IS NULL"
        
        if operator == 'IS NOT NULL':
            return f"{col_ref} IS NOT NULL"
        
        # Operatore IN (lista di valori)
        if operator == 'IN':
            # Se è una stringa con virgole, splittala in lista
            if isinstance(value, str) and ',' in value:
                value = [v.strip() for v in value.split(',') if v.strip()]
            
            if isinstance(value, list):
                placeholders = []
                for v in value:
                    param_name = self._get_next_param_name()
                    # Prova a convertire in intero se possibile
                    try:
                        params[param_name] = int(v) if isinstance(v, str) and v.isdigit() else v
                    except (ValueError, TypeError):
                        params[param_name] = v
                    placeholders.append(f":{param_name}")
                return f"{col_ref} IN ({', '.join(placeholders)})"
            else:
                # Singolo valore
                param_name = self._get_next_param_name()
                # Prova a convertire in intero se possibile
                try:
                    params[param_name] = int(value) if isinstance(value, str) and value.isdigit() else value
                except (ValueError, TypeError):
                    params[param_name] = value
                return f"{col_ref} IN (:{param_name})"
        
        # Operatore LIKE
        if operator == 'LIKE':
            param_name = self._get_next_param_name()
            # Aggiungi wildcard al valore, non alla query
            params[param_name] = f"%{value}%"
            return f"{col_ref} LIKE :{param_name}"
        
        # Operatori di confronto standard: =, !=, >, <, >=, <=
        param_name = self._get_next_param_name()
        params[param_name] = value
        return f"{col_ref} {operator} :{param_name}"
    
    def _is_valid_identifier(self, name: str) -> bool:
        """
        Valida che un identificatore SQL (tabella, colonna, alias) sia sicuro.
        
        Regole:
        - Solo lettere, numeri, underscore
        - Non può iniziare con un numero
        - Lunghezza massima 128 caratteri
        - Non può essere una keyword SQL pericolosa
        """
        if not name or len(name) > 128:
            return False
        
        # Pattern: inizia con lettera o underscore, seguito da lettere/numeri/underscore
        import re
        if not re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', name):
            return False
        
        # Blocca keyword SQL pericolose (case insensitive)
        dangerous_keywords = {
            'drop', 'delete', 'truncate', 'insert', 'update', 'alter',
            'create', 'exec', 'execute', 'union', 'grant', 'revoke'
        }
        if name.lower() in dangerous_keywords:
            return False
        
        return True
    
    def _get_visible_columns(self, table_nodes: List[Node]) -> List[str]:
        """Ottieni lista colonne visibili (incluse le calcolate)"""
        columns = []
        for node in table_nodes:
            # Colonne normali
            for col in node.columns:
                if col.visible:
                    columns.append(col.name)
            
            # Colonne calcolate
            calculated_columns = getattr(node, 'calculated_columns', None) or []
            for calc_col in calculated_columns:
                columns.append(calc_col.get('name', 'calculated'))
        
        return columns
    
    def _assemble_query(self, sql_parts: Dict) -> str:
        """Assembla le parti SQL in una query completa"""
        query = "SELECT "
        
        # DISTINCT
        if sql_parts.get('distinct'):
            query += "DISTINCT "
        
        # Colonne
        query += sql_parts['select']
        
        # FROM + JOINs
        query += f"\nFROM {sql_parts['from']}"
        
        # WHERE
        if sql_parts['where']:
            query += f"\nWHERE {sql_parts['where']}"
        
        # LIMIT (validazione: deve essere intero positivo)
        limit = sql_parts.get('limit')
        if limit is not None:
            if not isinstance(limit, int) or limit < 0:
                raise ValueError(f"LIMIT deve essere un intero positivo, ricevuto: {limit}")
            query += f"\nLIMIT {limit}"
        
        return query