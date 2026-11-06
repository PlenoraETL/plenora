"""
Rendering delle clausole FROM/JOIN per QueryGenerator.
"""

from typing import Callable, List

from app.core.estrai.query_aliases import effective_alias
from app.core.projects.canvas_models import Edge, Node
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
        from_clause = self.table_reference(first_node, "Alias tabella FROM")

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
        table_node_ids = {n.id for n in all_nodes}
        table_edges = [
            e for e in edges
            if e.source in table_node_ids and e.target in table_node_ids
        ]

        if processed_ids:
            edge = next(
                (e for e in table_edges
                 if e.target == node.id and e.source in processed_ids),
                None
            )
            if not edge:
                edge = next(
                    (e for e in table_edges
                     if e.source == node.id and e.target in processed_ids),
                    None
                )

        if not edge:
            edge = next(
                (e for e in table_edges if e.target == node.id and e.source != node.id),
                next(
                    (e for e in table_edges if e.source == node.id and e.target != node.id),
                    None
                )
            )

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
            return f"CROSS JOIN {self.table_reference(node, 'Alias tabella CROSS JOIN')}"

        table_alias = effective_alias(node)

        if edge.target == node.id:
            other_node_id = edge.source
            is_reversed = False
        else:
            other_node_id = edge.target
            is_reversed = True

        other_node = next((n for n in all_nodes if n.id == other_node_id), None)
        source_alias = effective_alias(other_node) if other_node else 'unknown'

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

        return f"{join_type} JOIN {self.table_reference(node, 'Alias tabella JOIN')} ON {on_clause}"

    def table_reference(self, node: Node, context: str) -> str:
        """``tabella [alias]`` di FROM e JOIN: l'unico punto che li emette.

        L'alias si scrive quando il nodo ne ha uno, scritto dall'utente o
        automatico (``query_aliases.with_unique_aliases``, self-join); senza
        alias la tabella si riferisce con il suo nome, che è
        ``effective_alias`` (stesso SQL di prima).
        """
        reference = self._qualify_table_name(self._get_node_table_name(node))
        if node.alias:
            self._safe_identifier(node.alias, context)
            reference += f" {node.alias}"
        return reference

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
            raise ValueError(
                "JOIN senza condizioni: configura colonne di join, "
                "join_conditions oppure usa join_type='cross' se il prodotto "
                "cartesiano e' intenzionale."
            )

        return " AND ".join(parts)
