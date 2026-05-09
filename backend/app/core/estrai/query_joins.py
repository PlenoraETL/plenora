"""
Rendering delle clausole FROM/JOIN per QueryGenerator.
"""

from typing import Callable, List

from app.shared.models import Edge, Node
from app.db.sql_translator import sql_translator


class QueryJoinRenderer:
    """Builds FROM/JOIN clauses while reusing QueryGenerator validation helpers."""

    def __init__(self, generator):
        self.db_type = generator.db_type
        self._get_node_table_name: Callable[[Node], str] = generator._get_node_table_name
        self._qualify_table_name: Callable[[str], str] = generator._qualify_table_name
        self._safe_identifier: Callable[[str, str], str] = generator._safe_identifier
        self._is_valid_identifier: Callable[[str], bool] = generator._is_valid_identifier
        self._wrap_col_with_func: Callable[[str, str], str] = generator._wrap_col_with_func
        self._generate_join = generator._genera_join
        self._generate_on_spatial = generator._genera_on_spaziale
        self._generate_on_attribute = generator._genera_on_attributivo

    def generate_from(self, table_nodes: List[Node], edges: List[Edge]) -> str:
        """
        Genera clausola FROM con eventuali JOIN.

        La prima tabella diventa il FROM principale, le successive vengono
        aggiunte come JOIN in base alla configurazione degli edges.

        IMPORTANTE: I nomi tabella vengono qualificati con lo schema se presente.
        Es: 'dbo.ac' per SQL Server

        Traccia i nodi gia' processati per permettere a generate_join di scegliere
        l'edge corretto quando un nodo ha piu' connessioni.
        """
        if not table_nodes:
            raise ValueError("Nessuna tabella selezionata")

        first_node = table_nodes[0]
        raw_name = self._get_node_table_name(first_node)
        qualified_table_name = self._qualify_table_name(raw_name) if first_node.type != 'datasource_node' else raw_name
        from_clause = qualified_table_name
        if first_node.alias:
            self._safe_identifier(first_node.alias, "Alias tabella FROM")
            from_clause += f" {first_node.alias}"

        processed_ids = {first_node.id}

        for node in table_nodes[1:]:
            join_str = self._generate_join(node, edges, table_nodes, processed_ids)
            from_clause += f"\n{join_str}"
            processed_ids.add(node.id)

        return from_clause

    def generate_join(
        self,
        node: Node,
        edges: List[Edge],
        all_nodes: List[Node],
        processed_ids: set = None
    ) -> str:
        """
        Genera una singola clausola JOIN per un nodo tabella.

        Supporta join su attributi, join spaziali e CROSS JOIN espliciti.
        """
        edge = None

        if processed_ids:
            edge = next(
                (e for e in edges
                 if e.target == node.id and e.source in processed_ids),
                None
            )
            if not edge:
                edge = next(
                    (e for e in edges
                     if e.source == node.id and e.target in processed_ids),
                    None
                )

        if not edge:
            edge = next(
                (e for e in edges if e.target == node.id and e.source != node.id),
                next(
                    (e for e in edges if e.source == node.id and e.target != node.id),
                    None
                )
            )

        raw_name = self._get_node_table_name(node)
        qualified_table_name = self._qualify_table_name(raw_name) if node.type != 'datasource_node' else raw_name

        if not edge:
            raise ValueError(
                f"Nodo tabella '{self._get_node_table_name(node)}' "
                f"(id={node.id}) non ha alcun edge di relazione con le "
                f"altre tabelle del FROM. Un CROSS JOIN accidentale "
                f"puo' generare un prodotto cartesiano di dimensioni "
                f"esplosive (query OOM). Aggiungi un edge al nodo, "
                f"oppure se il CROSS JOIN e' intenzionale, aggiungi un "
                f"edge con join_type='cross' esplicito."
            )

        join_type = (edge.join_type or 'LEFT').upper()

        if join_type not in ('LEFT', 'RIGHT', 'INNER', 'OUTER', 'FULL', 'FULL OUTER', 'CROSS'):
            join_type = 'LEFT'
        if join_type in ('FULL', 'OUTER'):
            join_type = 'FULL OUTER'

        if join_type == 'CROSS':
            join_str = f"CROSS JOIN {qualified_table_name}"
            if node.alias:
                self._safe_identifier(node.alias, "Alias tabella CROSS JOIN")
                join_str += f" {node.alias}"
            return join_str

        table_alias = node.alias or self._get_node_table_name(node)

        if edge.target == node.id:
            other_node_id = edge.source
            is_reversed = False
        else:
            other_node_id = edge.target
            is_reversed = True

        other_node = next((n for n in all_nodes if n.id == other_node_id), None)
        source_alias = other_node.alias or other_node.table_name if other_node else 'unknown'

        is_spatial = getattr(edge, 'is_spatial', False)

        if is_spatial:
            if is_reversed:
                on_clause = self._generate_on_spatial(edge, table_alias, source_alias)
            else:
                on_clause = self._generate_on_spatial(edge, source_alias, table_alias)
        else:
            on_clause = self._generate_on_attribute(
                edge, source_alias, table_alias, node, join_type, is_reversed
            )

        join_str = f"{join_type} JOIN {qualified_table_name}"
        if node.alias:
            self._safe_identifier(node.alias, "Alias tabella JOIN")
            join_str += f" {node.alias}"
        join_str += f" ON {on_clause}"

        return join_str

    def generate_on_spatial(self, edge: Edge, source_alias: str, table_alias: str) -> str:
        """
        Genera la condizione ON per un join spaziale.
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
                dist = spatial_distance or 0
                return sql_translator.get_spatial_predicate(
                    predicate='dwithin',
                    db_type=db,
                    geom1=geom1, geom2=geom2,
                    distance=dist
                )
            return sql_translator.get_spatial_predicate(
                predicate=predicate_key,
                db_type=db,
                geom1=geom1, geom2=geom2
            )
        except (ValueError, KeyError, AttributeError):
            _VALID_SPATIAL_PREDICATES = {
                'ST_Intersects', 'ST_Contains', 'ST_Within', 'ST_Touches',
                'ST_Overlaps', 'ST_Crosses', 'ST_DWithin', 'ST_Equals',
                'ST_Disjoint', 'ST_Covers', 'ST_CoveredBy',
            }
            if spatial_predicate not in _VALID_SPATIAL_PREDICATES:
                raise ValueError(f"Predicato spaziale non valido: {spatial_predicate}")
            if spatial_predicate == 'ST_DWithin' and spatial_distance:
                try:
                    safe_distance = float(spatial_distance)
                except (ValueError, TypeError):
                    raise ValueError(f"Distanza spaziale non numerica: {spatial_distance}")
                return f"ST_DWithin({geom1}, {geom2}, {safe_distance})"
            return f"{spatial_predicate}({geom1}, {geom2})"

    def generate_on_attribute(
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
        """
        parts = []

        join_conditions = getattr(edge, 'join_conditions', None)

        if join_conditions and len(join_conditions) > 0:
            valid_conditions = [
                c for c in join_conditions
                if c.get('source_column') and c.get('target_column')
            ]

            for cond in valid_conditions:
                if is_reversed:
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

                col_expr = self._wrap_col_with_func(f"{filter_table_alias}.{col}", func)

                _VALID_ON_OPERATORS = {
                    '=', '!=', '<>', '>', '<', '>=', '<=',
                    'LIKE', 'IN', 'IS NULL', 'IS NOT NULL',
                }
                if operator not in _VALID_ON_OPERATORS:
                    raise ValueError(f"Operatore ON filter non valido: {operator}")

                if operator in ('IS NULL', 'IS NOT NULL'):
                    parts.append(f"{col_expr} {operator}")
                elif operator == 'IN':
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
