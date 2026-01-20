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
        
        # ✅ Estrai configurazione GROUP BY dal result_node
        group_by_config = self._get_group_by_config(result_node)
        
        # Genera SQL con parametri
        sql_parts = {
            "select": self._generate_select(table_nodes, result_node, group_by_config),
            "from": self._generate_from(table_nodes, edges),
            "where": self._generate_where(table_nodes, params),
            "group_by": self._generate_group_by(group_by_config),
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
    
    def _get_group_by_config(self, result_node: Node) -> Dict[str, Any]:
        """
        Estrae configurazione GROUP BY dal nodo risultato.
        
        Formato atteso nel result_node:
        {
            "group_by_enabled": true,
            "group_by_columns": ["comune", "provincia"],
            "aggregations": [
                {"column": "area", "function": "SUM", "alias": "totale_area"},
                {"column": "id", "function": "COUNT", "alias": "num_records"},
                {"column": "*", "function": "COUNT", "alias": "conteggio"}
            ]
        }
        """
        config = {
            "enabled": False,
            "columns": [],
            "aggregations": []
        }
        
        # Prova a leggere da diversi attributi possibili
        if hasattr(result_node, 'group_by_enabled') and result_node.group_by_enabled:
            config["enabled"] = True
            config["columns"] = getattr(result_node, 'group_by_columns', []) or []
            config["aggregations"] = getattr(result_node, 'aggregations', []) or []
        elif hasattr(result_node, 'data') and result_node.data:
            data = result_node.data if isinstance(result_node.data, dict) else {}
            if data.get('group_by_enabled'):
                config["enabled"] = True
                config["columns"] = data.get('group_by_columns', []) or []
                config["aggregations"] = data.get('aggregations', []) or []
        
        return config
    
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
    
    def _generate_select(
        self, 
        table_nodes: List[Node], 
        result_node: Node,
        group_by_config: Dict[str, Any] = None
    ) -> str:
        """
        Genera clausola SELECT con colonne visibili, alias e colonne calcolate.
        Se GROUP BY è attivo, genera SELECT con funzioni aggregate.
        """
        columns = []
        
        # ✅ Se GROUP BY è attivo, genera SELECT aggregato
        if group_by_config and group_by_config.get("enabled"):
            return self._generate_select_with_groupby(table_nodes, group_by_config)
        
        # SELECT normale (senza GROUP BY)
        for node in table_nodes:
            table_alias = node.alias or node.table_name
            
            # Colonne normali
            for col in node.columns:
                if not col.visible:
                    continue
                
                col_name = col.name
                col_alias = col.alias
                col_type = getattr(col, 'type', 'str') or 'str'
                
                if not self._is_valid_identifier(col_name):
                    raise ValueError(f"Nome colonna non valido: {col_name}")
                
                # Se è una colonna geometrica, wrappa con funzione WKT
                if col_type.startswith('geometry'):
                    col_expr = self._wrap_geometry_as_wkt(f"{table_alias}.{col_name}")
                    output_alias = col_alias if col_alias else col_name
                    if not self._is_valid_identifier(output_alias):
                        raise ValueError(f"Alias colonna non valido: {output_alias}")
                    columns.append(f"{col_expr} AS {output_alias}")
                else:
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
                    if calc_col.get('is_manual') and calc_col.get('sql'):
                        sql_expr = calc_col['sql']
                    elif calc_col.get('expression'):
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
    
    def _generate_select_with_groupby(
        self, 
        table_nodes: List[Node], 
        group_by_config: Dict[str, Any]
    ) -> str:
        """
        Genera SELECT con GROUP BY: colonne raggruppamento + funzioni aggregate.
        
        Esempio output:
            comune, provincia, SUM(area) AS totale_area, COUNT(*) AS conteggio
        """
        columns = []
        group_columns = group_by_config.get("columns", [])
        aggregations = group_by_config.get("aggregations", [])
        
        # Mappa nomi tabella per trovare l'alias corretto
        table_map = {}
        for node in table_nodes:
            table_alias = node.alias or node.table_name
            for col in node.columns:
                if col.visible:
                    table_map[col.name] = table_alias
        
        # 1. Aggiungi colonne di raggruppamento
        for col_name in group_columns:
            if not self._is_valid_identifier(col_name):
                raise ValueError(f"Nome colonna GROUP BY non valido: {col_name}")
            
            # Trova la tabella corretta per questa colonna
            table_alias = table_map.get(col_name)
            if table_alias:
                columns.append(f"{table_alias}.{col_name}")
            else:
                columns.append(col_name)
        
        # 2. Aggiungi aggregazioni
        valid_agg_functions = {'COUNT', 'SUM', 'AVG', 'MIN', 'MAX', 'FIRST', 'LAST'}
        
        for agg in aggregations:
            # Supporta sia dict che oggetti Pydantic
            if hasattr(agg, 'column'):
                # Oggetto Pydantic
                col = agg.column or '*'
                func = (agg.function or 'COUNT').upper()
                alias = agg.alias or f"{func.lower()}_{col}"
            else:
                # Dizionario
                col = agg.get('column', '*')
                func = agg.get('function', 'COUNT').upper()
                alias = agg.get('alias', f"{func.lower()}_{col}")
            
            # Validazione funzione
            if func not in valid_agg_functions:
                raise ValueError(f"Funzione aggregazione non valida: {func}")
            
            # Validazione alias
            if not self._is_valid_identifier(alias):
                raise ValueError(f"Alias aggregazione non valido: {alias}")
            
            # Costruisci espressione
            if col == '*':
                # COUNT(*) è speciale
                if func != 'COUNT':
                    raise ValueError(f"Solo COUNT può usare *, non {func}")
                columns.append(f"COUNT(*) AS {alias}")
            else:
                if not self._is_valid_identifier(col):
                    raise ValueError(f"Nome colonna aggregazione non valido: {col}")
                
                # Trova la tabella corretta
                table_alias = table_map.get(col)
                col_ref = f"{table_alias}.{col}" if table_alias else col
                columns.append(f"{func}({col_ref}) AS {alias}")
        
        if not columns:
            raise ValueError("Nessuna colonna per GROUP BY")
        
        return ", ".join(columns)
    
    def _generate_group_by(self, group_by_config: Dict[str, Any]) -> str:
        """
        Genera clausola GROUP BY.
        
        Returns:
            Stringa con colonne (es: "comune, provincia") o stringa vuota se non attivo
        """
        if not group_by_config or not group_by_config.get("enabled"):
            return ""
        
        columns = group_by_config.get("columns", [])
        if not columns:
            return ""
        
        # Validazione
        for col in columns:
            if not self._is_valid_identifier(col):
                raise ValueError(f"Nome colonna GROUP BY non valido: {col}")
        
        return ", ".join(columns)
    
    def _wrap_geometry_as_wkt(self, col_ref: str) -> str:
        """
        Wrappa una colonna geometrica con la funzione appropriata per convertirla in WKT.
        """
        wkt_functions = {
            'postgres': 'ST_AsText',
            'postgresql': 'ST_AsText',
            'postgis': 'ST_AsText',
            'mysql': 'ST_AsText',
            'mariadb': 'ST_AsText',
            'sqlite': 'AsText',
            'spatialite': 'AsText',
            'mssql': '{col}.STAsText()',
            'sqlserver': '{col}.STAsText()',
            'oracle': 'SDO_UTIL.TO_WKTGEOMETRY',
        }
        
        db = self.db_type.lower() if self.db_type else 'postgres'
        
        if db in ('mssql', 'sqlserver'):
            return wkt_functions[db].replace('{col}', col_ref)
        
        func = wkt_functions.get(db, 'ST_AsText')
        return f"{func}({col_ref})"
    
    def _generate_from(self, table_nodes: List[Node], edges: List[Edge]) -> str:
        """Genera clausola FROM con eventuali JOIN"""
        if not table_nodes:
            raise ValueError("Nessuna tabella selezionata")
        
        # Prima tabella (FROM)
        first_node = table_nodes[0]
        from_clause = first_node.table_name
        if first_node.alias:
            from_clause += f" AS {first_node.alias}"
        
        # Altre tabelle (JOIN)
        for node in table_nodes[1:]:
            join_str = self._generate_join(node, edges, table_nodes)
            from_clause += f"\n{join_str}"
        
        return from_clause
    
    def _generate_join(
        self, 
        node: Node, 
        edges: List[Edge], 
        all_nodes: List[Node]
    ) -> str:
        """Genera singola clausola JOIN"""
        edge = next(
            (e for e in edges if e.target == node.id or e.source == node.id),
            None
        )
        
        if not edge:
            table_name = node.table_name
            if node.alias:
                return f"CROSS JOIN {table_name} AS {node.alias}"
            return f"CROSS JOIN {table_name}"
        
        join_type = (edge.join_type or 'LEFT').upper()
        if join_type not in ('LEFT', 'RIGHT', 'INNER', 'OUTER', 'FULL', 'FULL OUTER', 'CROSS'):
            join_type = 'LEFT'
        
        # Normalizza FULL o OUTER -> FULL OUTER (sintassi SQL standard)
        if join_type in ('FULL', 'OUTER'):
            join_type = 'FULL OUTER'
        
        table_name = node.table_name
        table_alias = node.alias or node.table_name
        
        source_node = next((n for n in all_nodes if n.id == edge.source), None)
        source_alias = source_node.alias or source_node.table_name if source_node else 'unknown'
        
        # ✅ Controlla se è un join spaziale
        is_spatial = getattr(edge, 'is_spatial', False)
        
        if is_spatial:
            spatial_predicate = getattr(edge, 'spatial_predicate', 'ST_Intersects')
            source_geom = getattr(edge, 'source_geom_column', 'geom')
            target_geom = getattr(edge, 'target_geom_column', 'geom')
            spatial_distance = getattr(edge, 'spatial_distance', None)
            
            if not self._is_valid_identifier(source_geom):
                raise ValueError(f"Colonna geometria non valida: {source_geom}")
            if not self._is_valid_identifier(target_geom):
                raise ValueError(f"Colonna geometria non valida: {target_geom}")
            
            # Costruisci ON clause spaziale in base al tipo database
            db = self.db_type.lower() if self.db_type else 'postgres'
            
            if db in ('mssql', 'sqlserver'):
                # SQL Server usa sintassi metodo: geom.STIntersects(geom) = 1
                predicate_map = {
                    'ST_Intersects': 'STIntersects',
                    'ST_Within': 'STWithin',
                    'ST_Contains': 'STContains',
                    'ST_Touches': 'STTouches',
                    'ST_Overlaps': 'STOverlaps',
                    'ST_DWithin': 'STDistance'  # SQL Server usa STDistance per DWithin
                }
                sql_method = predicate_map.get(spatial_predicate, 'STIntersects')
                
                if spatial_predicate == 'ST_DWithin' and spatial_distance:
                    # SQL Server: geom.STDistance(geom) <= distance
                    on_clause = f"{source_alias}.{source_geom}.{sql_method}({table_alias}.{target_geom}) <= {spatial_distance}"
                else:
                    # SQL Server: geom.STIntersects(geom) = 1
                    on_clause = f"{source_alias}.{source_geom}.{sql_method}({table_alias}.{target_geom}) = 1"
            
            elif db == 'oracle':
                # Oracle usa SDO_RELATE o SDO_WITHIN_DISTANCE
                if spatial_predicate == 'ST_DWithin' and spatial_distance:
                    on_clause = f"SDO_WITHIN_DISTANCE({source_alias}.{source_geom}, {table_alias}.{target_geom}, 'distance={spatial_distance}') = 'TRUE'"
                elif spatial_predicate == 'ST_Intersects':
                    on_clause = f"SDO_RELATE({source_alias}.{source_geom}, {table_alias}.{target_geom}, 'mask=ANYINTERACT') = 'TRUE'"
                elif spatial_predicate == 'ST_Within':
                    on_clause = f"SDO_RELATE({source_alias}.{source_geom}, {table_alias}.{target_geom}, 'mask=INSIDE+COVEREDBY') = 'TRUE'"
                elif spatial_predicate == 'ST_Contains':
                    on_clause = f"SDO_RELATE({source_alias}.{source_geom}, {table_alias}.{target_geom}, 'mask=CONTAINS+COVERS') = 'TRUE'"
                elif spatial_predicate == 'ST_Touches':
                    on_clause = f"SDO_RELATE({source_alias}.{source_geom}, {table_alias}.{target_geom}, 'mask=TOUCH') = 'TRUE'"
                elif spatial_predicate == 'ST_Overlaps':
                    on_clause = f"SDO_RELATE({source_alias}.{source_geom}, {table_alias}.{target_geom}, 'mask=OVERLAPBDYINTERSECT') = 'TRUE'"
                else:
                    on_clause = f"SDO_RELATE({source_alias}.{source_geom}, {table_alias}.{target_geom}, 'mask=ANYINTERACT') = 'TRUE'"
            
            else:
                # PostgreSQL/PostGIS, MySQL, MariaDB usano sintassi funzione standard
                if spatial_predicate == 'ST_DWithin' and spatial_distance:
                    on_clause = f"{spatial_predicate}({source_alias}.{source_geom}, {table_alias}.{target_geom}, {spatial_distance})"
                else:
                    on_clause = f"{spatial_predicate}({source_alias}.{source_geom}, {table_alias}.{target_geom})"
        else:
            # Join normale su attributi
            source_col = edge.source_column
            target_col = edge.target_column
            
            if not source_col or not target_col:
                table_name = node.table_name
                if node.alias:
                    return f"{join_type} JOIN {table_name} AS {node.alias} ON 1=1 -- JOIN NON CONFIGURATO"
                return f"{join_type} JOIN {table_name} ON 1=1 -- JOIN NON CONFIGURATO"
            
            if not self._is_valid_identifier(source_col):
                raise ValueError(f"Colonna join non valida: {source_col}")
            if not self._is_valid_identifier(target_col):
                raise ValueError(f"Colonna join non valida: {target_col}")
            
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
        """
        conditions = []
        
        for node in table_nodes:
            table_alias = node.alias or node.table_name
            filters = node.filters or []
            
            # Costruisci mappa colonna -> tipo per questo nodo
            column_type_map = {}
            for col in node.columns:
                col_name = col.name if hasattr(col, 'name') else col.get('name')
                col_type = getattr(col, 'type', None) or (col.get('type') if isinstance(col, dict) else None) or 'str'
                if col_name:
                    column_type_map[col_name] = col_type
            
            # DEBUG: log della mappa tipi (rimuovere in produzione)
            import logging
            logging.getLogger(__name__).debug(f"Column type map for {table_alias}: {column_type_map}")
            
            for filter_item in filters:
                column = filter_item.column
                operator = filter_item.operator
                value = filter_item.value
                
                if not column or not operator:
                    continue
                
                if not self._is_valid_identifier(column):
                    raise ValueError(f"Nome colonna filtro non valido: {column}")
                
                # Ottieni il tipo colonna dalla mappa
                column_type = column_type_map.get(column, 'str')
                
                # DEBUG: log del tipo colonna usato
                logging.getLogger(__name__).debug(f"Filter on {column}: type={column_type}, db={self.db_type}")
                
                condition = self._create_condition(table_alias, column, operator, value, params, column_type)
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
        params: Dict[str, Any],
        column_type: str = 'str'
    ) -> str:
        """
        Crea singola condizione WHERE con parametri sicuri.
        
        Gestisce specialmente:
        - Oracle CLOB/NCLOB: wrappa con TO_CHAR() per permettere confronti
        """
        col_ref = f"{table_alias}.{column}"
        
        # Per Oracle, wrappa colonne CLOB/NCLOB con TO_CHAR()
        # Questo permette di usare operatori di confronto su tipi LOB
        db = self.db_type.lower() if self.db_type else 'postgres'
        
        # Normalizza il tipo colonna per il confronto
        col_type_upper = (column_type or '').upper()
        
        # Oracle CLOB detection: tipo esplicito CLOB/NCLOB/LONG
        # oppure tipi testo generici che Oracle potrebbe aver mappato come CLOB
        is_oracle_lob = (
            db == 'oracle' and 
            col_type_upper in ('CLOB', 'NCLOB', 'LONG', 'TEXT', 'TESTO', 'STRING', 'STR')
        )
        
        if is_oracle_lob:
            col_ref = f"TO_CHAR({table_alias}.{column})"
        
        valid_operators = {'=', '!=', '>', '<', '>=', '<=', 'LIKE', 'IN', 'IS NULL', 'IS NOT NULL'}
        if operator not in valid_operators:
            raise ValueError(f"Operatore non valido: {operator}")
        
        if operator == 'IS NULL':
            return f"{col_ref} IS NULL"
        
        if operator == 'IS NOT NULL':
            return f"{col_ref} IS NOT NULL"
        
        if operator == 'IN':
            if isinstance(value, str) and ',' in value:
                value = [v.strip() for v in value.split(',') if v.strip()]
            
            if isinstance(value, list):
                placeholders = []
                for v in value:
                    param_name = self._get_next_param_name()
                    try:
                        params[param_name] = int(v) if isinstance(v, str) and v.isdigit() else v
                    except (ValueError, TypeError):
                        params[param_name] = v
                    placeholders.append(f":{param_name}")
                return f"{col_ref} IN ({', '.join(placeholders)})"
            else:
                param_name = self._get_next_param_name()
                try:
                    params[param_name] = int(value) if isinstance(value, str) and value.isdigit() else value
                except (ValueError, TypeError):
                    params[param_name] = value
                return f"{col_ref} IN (:{param_name})"
        
        if operator == 'LIKE':
            param_name = self._get_next_param_name()
            params[param_name] = f"%{value}%"
            return f"{col_ref} LIKE :{param_name}"
        
        param_name = self._get_next_param_name()
        params[param_name] = value
        return f"{col_ref} {operator} :{param_name}"
    
    def _is_valid_identifier(self, name: str) -> bool:
        """Valida che un identificatore SQL sia sicuro."""
        if not name or len(name) > 128:
            return False
        
        import re
        if not re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', name):
            return False
        
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
            for col in node.columns:
                if col.visible:
                    columns.append(col.name)
            
            calculated_columns = getattr(node, 'calculated_columns', None) or []
            for calc_col in calculated_columns:
                columns.append(calc_col.get('name', 'calculated'))
        
        return columns
    
    def _assemble_query(self, sql_parts: Dict) -> str:
        """Assembla le parti SQL in una query completa"""
        query = "SELECT "
        
        # DISTINCT (non ha senso con GROUP BY, ma lo lasciamo per sicurezza)
        if sql_parts.get('distinct') and not sql_parts.get('group_by'):
            query += "DISTINCT "
        
        # Colonne
        query += sql_parts['select']
        
        # FROM + JOINs
        query += f"\nFROM {sql_parts['from']}"
        
        # WHERE
        if sql_parts['where']:
            query += f"\nWHERE {sql_parts['where']}"
        
        # ✅ GROUP BY
        if sql_parts.get('group_by'):
            query += f"\nGROUP BY {sql_parts['group_by']}"
        
        # LIMIT - sintassi diversa per database
        limit = sql_parts.get('limit')
        if limit is not None:
            if not isinstance(limit, int) or limit < 0:
                raise ValueError(f"LIMIT deve essere un intero positivo, ricevuto: {limit}")
            
            db = self.db_type.lower() if self.db_type else 'postgres'
            
            if db == 'oracle':
                # Oracle 12c+ usa FETCH FIRST
                query += f"\nFETCH FIRST {limit} ROWS ONLY"
            elif db in ('mssql', 'sqlserver'):
                # SQL Server usa TOP (va inserito dopo SELECT)
                query = query.replace("SELECT ", f"SELECT TOP {limit} ", 1)
            else:
                # PostgreSQL, MySQL, MariaDB, SQLite usano LIMIT
                query += f"\nLIMIT {limit}"
        
        return query