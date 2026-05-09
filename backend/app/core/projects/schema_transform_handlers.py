"""Transform schema handlers for schema inference."""

from __future__ import annotations

import re
from typing import Callable, Dict, List

from app.core.projects.schema_geo_handlers import GeoSchemaHandlers
from app.core.projects.schema_join_handlers import JoinSchemaHandlers


class TransformSchemaHandlers(JoinSchemaHandlers, GeoSchemaHandlers):
    """Owns transform_op dispatch and transform schema implementations."""

    def __init__(self, column_schema_cls: type):
        self._column_schema_cls = column_schema_cls

    def _build_transform_handlers(
        self,
        edges_by_target: dict,
    ) -> Dict[str, Callable[[List[List[ColumnSchema]], dict, str], List[ColumnSchema]]]:
        """Registry transform_op -> schema handler.

        Tutti gli handler esposti dal registry condividono la firma
        canonica `(input_schemas, config, node_id) -> List[ColumnSchema]`.
        Gli handler con signature non standard sono adattati via lambda:

        - `_schema_join` richiede `edges_by_target` (closure su param);
        - `_schema_geo_overlay` richiede `overlay_type` (lambda fissa
          `intersection`/`union`/`difference`/`symmetric_difference`,
          o legge `config["how"]` per `geo_overlay`);
        - `_schema_filter`, `_schema_sort`, `_schema_passthrough`,
          `_schema_cross_join`, `_schema_geo_within` ignorano `config`
          (lambda passa solo `(i, n)`).

        Aliases preservati (presenti nel dispatcher ma non nel
        `transforms_registry.json` — lock parity test):
        - `drop` -> `_schema_drop` (alias storico per `drop_columns`)
        - `geo_to_crs` -> `_schema_passthrough` (alias legacy per
          `geo_reproject`)

        i transform_op tra i marker `# DISPATCH-REGISTRY:start` e
        `# DISPATCH-REGISTRY:end` qui sotto.
        """
        pt = lambda i, c, n: self._schema_passthrough(i, n)
        return {
            # DISPATCH-REGISTRY:start
            # base
            "join": lambda i, c, n: self._schema_join(i, c, n, edges_by_target),
            "sjoin": lambda i, c, n: self._schema_sjoin(i, c, n),
            "aggregate": lambda i, c, n: self._schema_aggregate(i, c, n),
            "formula": lambda i, c, n: self._schema_formula(i, c, n),
            "filter": lambda i, c, n: self._schema_filter(i, n),
            "concat": lambda i, c, n: self._schema_concat(i, c, n),
            "sort": lambda i, c, n: self._schema_sort(i, n),
            "distinct": lambda i, c, n: self._schema_distinct(i, c, n),
            "rename": lambda i, c, n: self._schema_rename(i, c, n),
            "drop": lambda i, c, n: self._schema_drop(i, c, n),
            "drop_columns": lambda i, c, n: self._schema_drop(i, c, n),
            "fill_na": pt,
            "replace": pt,
            "split_column": lambda i, c, n: self._schema_split_column(i, c, n),
            # geo
            "geo_buffer": lambda i, c, n: self._schema_geo_buffer(i, c, n),
            "geo_centroid": lambda i, c, n: self._schema_geo_centroid(i, c, n),
            "geo_area": lambda i, c, n: self._schema_geo_area(i, c, n),
            "geo_clip": lambda i, c, n: self._schema_geo_clip(i, c, n),
            "geo_dissolve": lambda i, c, n: self._schema_geo_dissolve(i, c, n),
            "geo_simplify": pt,
            "geo_convex_hull": lambda i, c, n: self._schema_geo_convex_hull(i, c, n),
            "geo_reproject": pt,
            "geo_intersection": lambda i, c, n: self._schema_geo_overlay(i, c, n, "intersection"),
            "geo_union": lambda i, c, n: self._schema_geo_overlay(i, c, n, "union"),
            "geo_difference": lambda i, c, n: self._schema_geo_overlay(i, c, n, "difference"),
            "geo_to_crs": pt,
            # privacy
            "md5_hash": lambda i, c, n: self._schema_md5_hash(i, c, n),
            "mask_data": lambda i, c, n: self._schema_mask_data(i, c, n),
            # base aggiuntive
            "type_cast": lambda i, c, n: self._schema_type_cast(i, c, n),
            "conditional": lambda i, c, n: self._schema_conditional(i, c, n),
            "text_normalize": lambda i, c, n: self._schema_text_normalize(i, c, n),
            # passthrough espliciti
            "concat_columns": pt,
            "pivot": pt,
            "geo_envelope": pt,
            # spatial
            "geo_from_coords": lambda i, c, n: self._schema_geo_from_coords(i, c, n),
            "add_row_number": lambda i, c, n: self._schema_add_row_number(i, c, n),
            "uuid_generator": lambda i, c, n: self._schema_uuid_generator(i, c, n),
            "string_length": lambda i, c, n: self._schema_string_length(i, c, n),
            "date_extract": lambda i, c, n: self._schema_date_extract(i, c, n),
            "geo_distance": lambda i, c, n: self._schema_geo_distance(i, c, n),
            "geo_to_wkt": lambda i, c, n: self._schema_geo_to_wkt(i, c, n),
            "geo_length": lambda i, c, n: self._schema_geo_length(i, c, n),
            "geo_perimeter": lambda i, c, n: self._schema_geo_perimeter(i, c, n),
            "geo_vertex_count": lambda i, c, n: self._schema_geo_vertex_count(i, c, n),
            "string_extract": lambda i, c, n: self._schema_string_extract(i, c, n),
            "bin": lambda i, c, n: self._schema_bin(i, c, n),
            "string_pad": lambda i, c, n: self._schema_string_pad(i, c, n),
            "geo_bounds_extractor": lambda i, c, n: self._schema_geo_bounds_extractor(i, c, n),
            "geo_count_points_in_polygons": lambda i, c, n: self._schema_geo_count_points_in_polygons(i, c, n),
            "geo_line_builder": lambda i, c, n: self._schema_geo_line_builder(i, c, n),
            "geo_polygon_builder": lambda i, c, n: self._schema_geo_polygon_builder(i, c, n),
            "geo_point_on_surface": lambda i, c, n: self._schema_geo_point_on_surface(i, c, n),
            "geo_boundary": lambda i, c, n: self._schema_geo_boundary(i, c, n),
            "reorder_columns": lambda i, c, n: self._schema_reorder_columns(i, c, n),
            "geo_voronoi": lambda i, c, n: self._schema_geo_voronoi(i, c, n),
            "cross_join": lambda i, c, n: self._schema_cross_join(i, n),
            "geo_within": lambda i, c, n: self._schema_geo_within(i, n),
            "geo_overlay": lambda i, c, n: self._schema_geo_overlay(
                i, c, n, c.get("how", "intersection"),
            ),
            "geo_symmetric_difference": lambda i, c, n: self._schema_geo_overlay(
                i, c, n, "symmetric_difference",
            ),
            "geo_nearest": lambda i, c, n: self._schema_geo_nearest(i, c, n),
            "lookup": lambda i, c, n: self._schema_lookup(i, c, n),
            "table_diff": lambda i, c, n: self._schema_table_diff(i, c, n),
            "melt": lambda i, c, n: self._schema_melt(i, c, n),
            "statistics": lambda i, c, n: self._schema_statistics(i, c, n),
            "window_function": lambda i, c, n: self._schema_window_function(i, c, n),
            # —
            # config-explicit schema contracts; safe deterministic
            # fallback (passthrough) when `output_columns` is absent
            "flatten_json": lambda i, c, n: self._schema_flatten_json(i, c, n),
            "transpose": lambda i, c, n: self._schema_transpose(i, c, n),
            # DISPATCH-REGISTRY:end
        }
    
    # SCHEMA PER OPERAZIONI SPECIFICHE (base
    # Ogni metodo _schema_xxx simula l'effetto della trasformazione sullo
    # schema, senza eseguire l'operazione. Questo permette al frontend di
    # conoscere le colonne output PRIMA dell'esecuzione della pipeline
    
    def _schema_join(
        self,
        input_schemas: List[List[ColumnSchema]],
        config: dict,
        node_id: str,
        edges_by_target: dict,
    ) -> List[ColumnSchema]:
        return JoinSchemaHandlers._schema_join(
            self,
            input_schemas,
            config,
            node_id,
            edges_by_target,
        )

    def _schema_aggregate(
        self, 
        input_schemas: List[List[ColumnSchema]], 
        config: dict,
        node_id: str
    ) -> List[ColumnSchema]:
        """
        Aggregate: group_by columns + aggregated columns
        """
        if not input_schemas:
            return []
        
        input_schema = input_schemas[0]
        input_by_name = {col.name: col for col in input_schema}
        
        group_by = config.get("group_by", [])
        aggs = config.get("aggs", {})  # {"col": "sum", "col2": "count"}
        
        result = []
        
        # Colonne group by (mantengono tipo originale
        for col_name in group_by:
            if col_name in input_by_name:
                col = input_by_name[col_name]
                result.append(self._column_schema_cls(
                    name=col_name,
                    type=col.type,
                    source_node=node_id,
                    is_calculated=False
                ))
        
        # Colonne aggregate
        for col_name, agg_func in aggs.items():
            orig_col = input_by_name.get(col_name)
            
            # Determina tipo output basato su funzione
            if agg_func in ("count", "nunique", "size"):
                out_type = "int"
            elif agg_func in ("sum", "mean", "std", "var", "min", "max"):
                out_type = orig_col.type if orig_col else "real"
                # Sum/mean su int -> real
                if agg_func in ("mean", "std", "var"):
                    out_type = "real"
            elif agg_func == "first" or agg_func == "last":
                out_type = orig_col.type if orig_col else "str"
            else:
                out_type = "str"
            
            result.append(self._column_schema_cls(
                name=col_name,
                type=out_type,
                source_node=node_id,
                is_calculated=True
            ))
        
        return result
    
    def _schema_formula(
        self, 
        input_schemas: List[List[ColumnSchema]], 
        config: dict,
        node_id: str
    ) -> List[ColumnSchema]:
        """
        Formula: input + nuova colonna calcolata
        """
        if not input_schemas:
            return []
        
        # Copia schema input
        result = [self._column_schema_cls(
            name=col.name,
            type=col.type,
            source_node=node_id,
            is_calculated=col.is_calculated
        ) for col in input_schemas[0]]
        
        # Aggiungi nuova colonna
        new_col = config.get("new_column", "calculated")
        output_type = config.get("output_type", "real")  # Default numerico
        
        # Verifica che non esista già
        existing_names = {col.name for col in result}
        if new_col and new_col not in existing_names:
            result.append(self._column_schema_cls(
                name=new_col,
                type=output_type,
                source_node=node_id,
                is_calculated=True
            ))
        
        return result
    
    def _schema_filter(
        self, 
        input_schemas: List[List[ColumnSchema]],
        node_id: str
    ) -> List[ColumnSchema]:
        """
        Filter: stesso schema (filtra righe, non colonne)
        """
        return self._schema_passthrough(input_schemas, node_id)
    
    def _schema_concat(
        self, 
        input_schemas: List[List[ColumnSchema]], 
        config: dict,
        node_id: str
    ) -> List[ColumnSchema]:
        """
        Concat: unione colonne (outer) o intersezione (inner)
        """
        if not input_schemas:
            return []
        
        join_mode = config.get("join", "outer")
        
        if join_mode == "inner":
            # Solo colonne comuni a tutti gli input
            common_cols = None
            for schema in input_schemas:
                col_names = {col.name for col in schema}
                if common_cols is None:
                    common_cols = col_names
                else:
                    common_cols &= col_names
            
            if not common_cols:
                return []
            
            # Prendi tipi dal primo schema
            result = []
            for col in input_schemas[0]:
                if col.name in common_cols:
                    result.append(self._column_schema_cls(
                        name=col.name,
                        type=col.type,
                        source_node=node_id,
                        is_calculated=col.is_calculated
                    ))
            return result
        
        else:  # outer
            # Tutte le colonne da tutti gli input
            seen = {}
            for schema in input_schemas:
                for col in schema:
                    if col.name not in seen:
                        seen[col.name] = self._column_schema_cls(
                            name=col.name,
                            type=col.type,
                            source_node=node_id,
                            is_calculated=col.is_calculated
                        )
            return list(seen.values())
    
    def _schema_sort(
        self, 
        input_schemas: List[List[ColumnSchema]],
        node_id: str
    ) -> List[ColumnSchema]:
        """
        Sort: stesso schema (cambia solo ordine righe)
        """
        return self._schema_passthrough(input_schemas, node_id)
    
    def _schema_distinct(
        self, 
        input_schemas: List[List[ColumnSchema]],
        config: dict,
        node_id: str
    ) -> List[ColumnSchema]:
        """
        Distinct: stesso schema dell'input (rimuove solo righe duplicate)
        """
        if not input_schemas:
            return []

        return [self._column_schema_cls(
            name=col.name,
            type=col.type,
            source_node=node_id,
            is_calculated=col.is_calculated
        ) for col in input_schemas[0]]
    def _schema_rename(
        self, 
        input_schemas: List[List[ColumnSchema]], 
        config: dict,
        node_id: str
    ) -> List[ColumnSchema]:
        """
        Rename: stesso schema con nomi cambiati
        Config: {"columns": {"old_name": "new_name", ...}} or {"old_name": "x", "new_name": "y"}
        """
        if not input_schemas:
            return []
        
        # Supporta entrambi i formati di config
        rename_map = config.get("columns", {})
        if not rename_map and config.get("old_name") and config.get("new_name"):
            rename_map = {config["old_name"]: config["new_name"]}
        
        result = []
        for col in input_schemas[0]:
            new_name = rename_map.get(col.name, col.name)
            result.append(self._column_schema_cls(
                name=new_name,
                type=col.type,
                source_node=node_id,
                is_calculated=col.is_calculated
            ))
        
        return result
    
    def _schema_drop(
        self, 
        input_schemas: List[List[ColumnSchema]], 
        config: dict,
        node_id: str
    ) -> List[ColumnSchema]:
        """
        Drop: input meno colonne specificate
        Config: {"columns": ["col1", "col2"]}
        """
        if not input_schemas:
            return []
        
        drop_cols = set(config.get("columns", []))
        
        result = []
        for col in input_schemas[0]:
            if col.name not in drop_cols:
                result.append(self._column_schema_cls(
                    name=col.name,
                    type=col.type,
                    source_node=node_id,
                    is_calculated=col.is_calculated
                ))
        
        return result
    
    def _schema_split_column(
        self, 
        input_schemas: List[List[ColumnSchema]], 
        config: dict,
        node_id: str
    ) -> List[ColumnSchema]:
        """
        Split Column: input + nuove colonne dalla divisione
        """
        if not input_schemas:
            return []
        
        result = [self._column_schema_cls(
            name=col.name,
            type=col.type,
            source_node=node_id,
            is_calculated=col.is_calculated
        ) for col in input_schemas[0]]
        
        # Aggiungi nuove colonne
        new_columns = config.get("new_columns", [])
        for col_name in new_columns:
            if col_name and col_name not in {c.name for c in result}:
                result.append(self._column_schema_cls(
                    name=col_name,
                    type="str",
                    source_node=node_id,
                    is_calculated=True
                ))
        
        return result
    
    # TRASFORMAZIONI PRIVACY
    # Operazioni di anonimizzazione: hash MD5 e mascheramento dati
    # Non cambiano il tipo colonna, ma possono aggiungere nuove colonne hash
    
    def _schema_md5_hash(
        self,
        input_schemas: List[List[ColumnSchema]],
        config: dict,
        node_id: str
    ) -> List[ColumnSchema]:
        """MD5 Hash: input + nuova colonna hash (str)"""
        if not input_schemas:
            return []
        
        output_col = config.get("output_column", "md5_hash")
        
        result = [self._column_schema_cls(
            name=col.name,
            type=col.type,
            source_node=node_id,
            is_calculated=col.is_calculated
        ) for col in input_schemas[0]]
        
        result.append(self._column_schema_cls(
            name=output_col,
            type="str",
            source_node=node_id,
            is_calculated=True
        ))
        
        return result
    
    def _schema_mask_data(
        self,
        input_schemas: List[List[ColumnSchema]],
        config: dict,
        node_id: str
    ) -> List[ColumnSchema]:
        """Mask Data: input + colonne mascherate (str)"""
        if not input_schemas:
            return []
        
        overwrite = config.get("overwrite", False)
        maskings = config.get("maskings", [])
        
        result = [self._column_schema_cls(
            name=col.name,
            type=col.type,
            source_node=node_id,
            is_calculated=col.is_calculated
        ) for col in input_schemas[0]]
        
        if not overwrite:
            existing_names = {col.name for col in result}
            for mask in maskings:
                masked_name = f"{mask.get('column', '')}_masked"
                if masked_name and masked_name not in existing_names:
                    result.append(self._column_schema_cls(
                        name=masked_name,
                        type="str",
                        source_node=node_id,
                        is_calculated=True
                    ))
        
        return result
    
    # TRASFORMAZIONI BASE AGGIUNTIVE
    # Operazioni aggiunte successivamente: cast tipi, condizionali
    # normalizzazione testo, creazione geometria da coordinate
    
    def _schema_type_cast(
        self,
        input_schemas: List[List[ColumnSchema]],
        config: dict,
        node_id: str
    ) -> List[ColumnSchema]:
        """Type Cast: cambia il tipo della colonna target"""
        if not input_schemas:
            return []
        
        target_column = config.get("column", "")
        target_type = config.get("target_type", "str")
        
        # Mappa tipo target alla notazione schema
        type_map = {
            "str": "str",
            "int": "int",
            "float": "real",
            "bool": "bool",
            "date": "date",
            "datetime": "datetime"
        }
        new_type = type_map.get(target_type, "str")
        
        result = []
        for col in input_schemas[0]:
            if col.name == target_column:
                result.append(self._column_schema_cls(
                    name=col.name,
                    type=new_type,
                    source_node=node_id,
                    is_calculated=True
                ))
            else:
                result.append(self._column_schema_cls(
                    name=col.name,
                    type=col.type,
                    source_node=node_id,
                    is_calculated=col.is_calculated
                ))
        
        return result
    
    def _schema_conditional(
        self,
        input_schemas: List[List[ColumnSchema]],
        config: dict,
        node_id: str
    ) -> List[ColumnSchema]:
        """Conditional: input + nuova colonna output (str)"""
        if not input_schemas:
            return []
        
        output_col = config.get("output_column", "result")
        
        result = [self._column_schema_cls(
            name=col.name,
            type=col.type,
            source_node=node_id,
            is_calculated=col.is_calculated
        ) for col in input_schemas[0]]
        
        existing_names = {col.name for col in result}
        if output_col not in existing_names:
            result.append(self._column_schema_cls(
                name=output_col,
                type="str",
                source_node=node_id,
                is_calculated=True
            ))
        
        return result
    
    def _schema_text_normalize(
        self,
        input_schemas: List[List[ColumnSchema]],
        config: dict,
        node_id: str
    ) -> List[ColumnSchema]:
        """Text Normalize: passthrough se overwrite, altrimenti aggiunge colonne _norm"""
        if not input_schemas:
            return []
        
        overwrite = config.get("overwrite", True)
        columns = config.get("columns", [])
        
        result = [self._column_schema_cls(
            name=col.name,
            type=col.type,
            source_node=node_id,
            is_calculated=col.is_calculated
        ) for col in input_schemas[0]]
        
        if not overwrite:
            existing_names = {col.name for col in result}
            for col_name in columns:
                norm_name = f"{col_name}_norm"
                if norm_name not in existing_names:
                    result.append(self._column_schema_cls(
                        name=norm_name,
                        type="str",
                        source_node=node_id,
                        is_calculated=True
                    ))

        return result


    def _schema_add_row_number(
        self,
        input_schemas: List[List[ColumnSchema]],
        config: dict,
        node_id: str,
    ) -> List[ColumnSchema]:
        """add_row_number: input + colonna `output_column` (int).

        Runtime: `op_add_row_number` (utility.py) aggiunge `output_column`
        come int sequenziale globale o per-partition. Gli altri parametri
        (`start`, `partition_column`, `order_column`, `ascending`) non
        alterano lo schema.
        """
        if not input_schemas:
            return []

        result = self._copy_schema(input_schemas[0], node_id)
        output_col = config.get("output_column") or "row_number"
        if output_col and output_col not in {c.name for c in result}:
            result.append(self._column_schema_cls(
                name=output_col,
                type="int",
                source_node=node_id,
                is_calculated=True,
            ))
        return result

    def _schema_uuid_generator(
        self,
        input_schemas: List[List[ColumnSchema]],
        config: dict,
        node_id: str,
    ) -> List[ColumnSchema]:
        """uuid_generator: input + colonna `output_column` (str UUIDv4).

        Runtime: `op_uuid_generator` aggiunge una colonna stringa con UUID
        v4 per riga.
        """
        if not input_schemas:
            return []

        result = self._copy_schema(input_schemas[0], node_id)
        output_col = config.get("output_column") or "uuid"
        if output_col and output_col not in {c.name for c in result}:
            result.append(self._column_schema_cls(
                name=output_col,
                type="str",
                source_node=node_id,
                is_calculated=True,
            ))
        return result

    def _schema_string_length(
        self,
        input_schemas: List[List[ColumnSchema]],
        config: dict,
        node_id: str,
    ) -> List[ColumnSchema]:
        """string_length: input + colonna `output_column` (int).

        Runtime: `op_string_length` (strings.py) aggiunge la lunghezza di
        `column` in `output_column` (default `"{column}_length"`). Se
        `column` non esiste a runtime il transform e' no-op (warning) —
        qui replichiamo: senza `column` valido o senza output_col
        derivabile, schema invariato.
        """
        if not input_schemas:
            return []

        result = self._copy_schema(input_schemas[0], node_id)
        column = config.get("column") or ""
        configured_out = config.get("output_column")
        if configured_out:
            output_col = configured_out
        elif column:
            output_col = f"{column}_length"
        else:
            return result  # senza column ne' output_column — schema invariato

        if output_col not in {c.name for c in result}:
            result.append(self._column_schema_cls(
                name=output_col,
                type="int",
                source_node=node_id,
                is_calculated=True,
            ))
        return result

    def _schema_date_extract(
        self,
        input_schemas: List[List[ColumnSchema]],
        config: dict,
        node_id: str,
    ) -> List[ColumnSchema]:
        """date_extract: input + 1 colonna per ogni `parts` valida.

        Runtime: `op_date_extract` (utility.py) crea
        `f"{prefix or column + '_'}{part}"` per ogni `part` in
        {year, month, day, quarter, weekday, week, hour, minute, second}.
        Tipo output: `int` per tutte le parti (week e' Int64 in pandas
        ma resta semanticamente int per il FE).

        Config:
        - `column`: colonna sorgente datetime (required)
        - `parts`: list[str] o str singolo (default `["year"]`)
        - `prefix`: str opzionale (se vuoto, usa `f"{column}_"`)

        Le parts non riconosciute sono ignorate (allineato al runtime).
        """
        if not input_schemas:
            return []

        result = self._copy_schema(input_schemas[0], node_id)

        column = config.get("column") or ""
        if not column:
            return result  # no source col -> no schema delta

        raw_parts = config.get("parts")
        if raw_parts is None:
            parts: List[str] = ["year"]
        elif isinstance(raw_parts, str):
            parts = [raw_parts]
        elif isinstance(raw_parts, list):
            parts = [str(p) for p in raw_parts if p]
        else:
            parts = ["year"]

        prefix = config.get("prefix")
        col_prefix = prefix if prefix else f"{column}_"

        valid_parts = {
            "year", "month", "day", "quarter", "weekday",
            "week", "hour", "minute", "second",
        }

        existing = {c.name for c in result}
        for part in parts:
            part_lower = part.lower().strip()
            if part_lower not in valid_parts:
                continue
            col_name = f"{col_prefix}{part_lower}"
            if col_name in existing:
                continue
            result.append(self._column_schema_cls(
                name=col_name,
                type="int",
                source_node=node_id,
                is_calculated=True,
            ))
            existing.add(col_name)

        return result


    def _schema_string_extract(
        self,
        input_schemas: List[List[ColumnSchema]],
        config: dict,
        node_id: str,
    ) -> List[ColumnSchema]:
        """string_extract: input + colonne estratte da regex (str).

        Runtime: `op_string_extract` (strings.py).
        - Pattern con gruppi nominati `(?P<name>...)`: una colonna str
          per ogni gruppo nominato (nomi e ordine derivati da
          `re.compile(pattern).groupindex`).
        - Pattern senza gruppi nominati: una sola colonna str
          `output_column or f"{column}_extracted"` (vale anche per
          `extract_all=True`, che cambia solo i valori di runtime).

        No-op (schema invariato) se `column`/`pattern` mancanti, se
        `column` non esiste in input, o se il pattern e' regex non
        valida (allineato al runtime: warning + return df).

        Caveat: pattern con misto named/unnamed groups → schema
        annunciato copre solo i named groups (allineato al subset
        deterministico). Casi rari nei flussi FE.
        """
        if not input_schemas:
            return []
        result = self._copy_schema(input_schemas[0], node_id)
        column = config.get("column") or ""
        pattern = config.get("pattern") or ""
        if not column or not pattern:
            return result
        existing = {c.name for c in result}
        if column not in existing:
            return result
        try:
            compiled = re.compile(pattern)
        except re.error:
            return result

        named_groups = compiled.groupindex
        if named_groups:
            # Ordine pattern: ordina per indice di gruppo (1-based
            ordered_names = sorted(named_groups, key=named_groups.get)
            for name in ordered_names:
                if name and name not in existing:
                    result.append(self._column_schema_cls(
                        name=name,
                        type="str",
                        source_node=node_id,
                        is_calculated=True,
                    ))
                    existing.add(name)
        else:
            out_col = config.get("output_column") or f"{column}_extracted"
            if out_col and out_col not in existing:
                result.append(self._column_schema_cls(
                    name=out_col,
                    type="str",
                    source_node=node_id,
                    is_calculated=True,
                ))
        return result

    def _schema_bin(
        self,
        input_schemas: List[List[ColumnSchema]],
        config: dict,
        node_id: str,
    ) -> List[ColumnSchema]:
        """bin: input + colonna discretizzata (str).

        Runtime: `op_bin` (analysis.py) usa `pd.cut` e produce una
        colonna categorica/string (le label sono str). Default
        `output_column = f"{column}_bin"`. No-op se `column` mancante
        o non in input (allineato al runtime warning path).

        Tipo registrato come `"str"` (le label categorical vengono
        consumate dal FE come stringhe).
        """
        if not input_schemas:
            return []
        result = self._copy_schema(input_schemas[0], node_id)
        column = config.get("column") or ""
        if not column:
            return result
        existing = {c.name for c in result}
        if column not in existing:
            return result
        out_col = config.get("output_column") or f"{column}_bin"
        if out_col and out_col not in existing:
            result.append(self._column_schema_cls(
                name=out_col,
                type="str",
                source_node=node_id,
                is_calculated=True,
            ))
        return result

    def _schema_string_pad(
        self,
        input_schemas: List[List[ColumnSchema]],
        config: dict,
        node_id: str,
    ) -> List[ColumnSchema]:
        """string_pad: pad la colonna a larghezza fissa.

        Runtime: `op_string_pad` (strings.py) forza `astype(str)` e
        scrive nel target. Schema delta:
        - `output_column` vuoto (o uguale a `column`): la colonna
          sorgente viene sovrascritta come `str`.
        - `output_column` diversa da `column`: la sorgente resta
          invariata (preservato il suo tipo) e viene aggiunta una
          nuova colonna `str`. Se collide con un altro nome esistente,
          quella colonna viene sovrascritta a `str`.

        No-op (schema invariato) se `column` mancante o non in input.
        Registry classifica `output: same`, ma il runtime introduce
        comunque un type-shift verso str → schema parity dedicata
        invece di passthrough generico.
        """
        if not input_schemas:
            return []
        result = self._copy_schema(input_schemas[0], node_id)
        column = config.get("column") or ""
        if not column:
            return result
        existing_names = {c.name for c in result}
        if column not in existing_names:
            return result

        out_col_cfg = config.get("output_column")
        target = out_col_cfg if out_col_cfg else column

        if target == column:
            for i, col in enumerate(result):
                if col.name == column:
                    result[i] = self._column_schema_cls(
                        name=col.name,
                        type="str",
                        source_node=node_id,
                        is_calculated=True,
                    )
                    break
            return result

        if target in existing_names:
            for i, col in enumerate(result):
                if col.name == target:
                    result[i] = self._column_schema_cls(
                        name=col.name,
                        type="str",
                        source_node=node_id,
                        is_calculated=True,
                    )
                    break
        else:
            result.append(self._column_schema_cls(
                name=target,
                type="str",
                source_node=node_id,
                is_calculated=True,
            ))
        return result


    # BE-PHASE2-SCHEMA-INFER-005
    # Handler dedicato per il riordino colonne (`reorder_columns`).
    # Gli handler geospatial dello stesso batch vivono in
    # `schema_geo_handlers.py`.

    def _schema_reorder_columns(
        self,
        input_schemas: List[List[ColumnSchema]],
        config: dict,
        node_id: str,
    ) -> List[ColumnSchema]:
        """reorder_columns: stesso column-set, ordine ricalcolato.

        Runtime: `op_reorder_columns` (columns.py).
        - Se `columns` valorizzata (lista non vuota): le colonne valide
          vengono messe in testa nell'ordine specificato; le rimanenti
          seguono nell'ordine input originale.
        - Se `columns` vuota e `sort_alphabetical=True`: ordine
          alfabetico.
        - Default (entrambe vuote/false): passthrough.

        Le colonne in `columns` ma non presenti nell'input vengono
        ignorate (allineato al filtro runtime
        `[c for c in columns if c in result.columns]`).
        """
        if not input_schemas:
            return []
        input_schema = input_schemas[0]
        by_name = {c.name: c for c in input_schema}

        cols_cfg = config.get("columns") or []
        sort_alpha = bool(config.get("sort_alphabetical", False))

        if cols_cfg:
            valid = [name for name in cols_cfg if name in by_name]
            remaining = [c.name for c in input_schema if c.name not in valid]
            ordered = valid + remaining
        elif sort_alpha:
            ordered = sorted(by_name.keys())
        else:
            ordered = [c.name for c in input_schema]

        return [self._column_schema_cls(
            name=by_name[name].name,
            type=by_name[name].type,
            source_node=node_id,
            is_calculated=by_name[name].is_calculated,
        ) for name in ordered if name in by_name]


    def _schema_melt(
        self,
        input_schemas: List[List[ColumnSchema]],
        config: dict,
        node_id: str,
    ) -> List[ColumnSchema]:
        """melt: wide → long.

        Runtime: `op_melt` (reshape.py) usa `pd.melt`. Output:
        - `id_columns` (preservate, types da input)
        - `var_name` (str, default `"variable"`) — sempre str
        - `value_name` (default `"value"`) — tipo:
          - se i `value_columns` (o tutti i non-id cols se vuoto)
            sono omogenei in tipo, usa quel tipo;
          - altrimenti `str` (mirror del comportamento pandas che
            promuove a `object` su tipi misti).

        `id_columns`/`value_columns` accettati come list[str] o str
        comma-separated (il runtime fa il filtro su `df.columns`, qui
        replichiamo).
        """
        if not input_schemas:
            return []
        input_schema = input_schemas[0]
        by_name = {c.name: c for c in input_schema}

        id_cols = [
            c for c in self._normalize_str_list(config.get("id_columns"))
            if c in by_name
        ]
        raw_value = config.get("value_columns")
        if raw_value is None or raw_value == "" or raw_value == []:
            value_cols_names = [
                c.name for c in input_schema if c.name not in set(id_cols)
            ]
        else:
            normalized = [
                c for c in self._normalize_str_list(raw_value)
                if c in by_name
            ]
            value_cols_names = normalized or [
                c.name for c in input_schema if c.name not in set(id_cols)
            ]

        var_name = config.get("var_name") or "variable"
        value_name = config.get("value_name") or "value"

        types = {by_name[c].type for c in value_cols_names if c in by_name}
        if len(types) == 1:
            val_type = next(iter(types))
        else:
            val_type = "str"

        result: List[ColumnSchema] = []
        for c in id_cols:
            col = by_name[c]
            result.append(self._column_schema_cls(
                name=c,
                type=col.type,
                source_node=node_id,
                is_calculated=False,
            ))
        result.append(self._column_schema_cls(
            name=var_name,
            type="str",
            source_node=node_id,
            is_calculated=True,
        ))
        result.append(self._column_schema_cls(
            name=value_name,
            type=val_type,
            source_node=node_id,
            is_calculated=True,
        ))
        return result

    def _schema_statistics(
        self,
        input_schemas: List[List[ColumnSchema]],
        config: dict,
        node_id: str,
    ) -> List[ColumnSchema]:
        """statistics: input + 1 colonna per ogni stat valida.

        Runtime: `op_statistics` (analysis.py).
        - prefix default = `f"{column}_"` (se `output_prefix` vuoto)
        - col name = `f"{prefix}{stat}"`
        - Tipo:
          - `count` → `int` (count pandas)
          - tutti gli altri (`min`, `max`, `sum`, `mean`, `median`,
            `std`, `var`, `q25`, `q75`) → `real` (numerici).
        - Stats unknown ignorate (allineato al `if stat_name in
          stat_funcs` runtime).
        - `column` mancante / non in input → schema invariato (no-op
          + warning runtime).
        - Default `stats = ["count","min","max","mean","median","std"]`.
        """
        if not input_schemas:
            return []
        result = self._copy_schema(input_schemas[0], node_id)
        column = config.get("column") or ""
        if not column:
            return result
        existing = {c.name for c in result}
        if column not in existing:
            return result

        raw_stats = config.get("stats")
        if raw_stats is None:
            stats = ["count", "min", "max", "mean", "median", "std"]
        elif isinstance(raw_stats, str):
            stats = [s.strip() for s in raw_stats.split(",") if s.strip()]
        elif isinstance(raw_stats, list):
            stats = [str(s).strip() for s in raw_stats if str(s).strip()]
        else:
            stats = ["count", "min", "max", "mean", "median", "std"]

        valid_stats = {
            "count", "min", "max", "sum", "mean", "median",
            "std", "var", "q25", "q75",
        }
        prefix = config.get("output_prefix") or f"{column}_"

        for stat in stats:
            if stat not in valid_stats:
                continue
            col_name = f"{prefix}{stat}"
            if col_name in existing:
                continue
            stat_type = "int" if stat == "count" else "real"
            result.append(self._column_schema_cls(
                name=col_name,
                type=stat_type,
                source_node=node_id,
                is_calculated=True,
            ))
            existing.add(col_name)
        return result

    def _schema_window_function(
        self,
        input_schemas: List[List[ColumnSchema]],
        config: dict,
        node_id: str,
    ) -> List[ColumnSchema]:
        """window_function: input + 1 colonna `output_column`.

        Runtime: `op_window_function` (aggregation.py).
        - `output_column` default = `f"{column}_{function}"`.
        - Funzioni valide: rank, dense_rank, cumsum, cumcount, lag,
          lead, pct_change, running_mean. Funzione unknown → no-op
          (passthrough).
        - Tipo output by function:
          - `rank`, `dense_rank`, `pct_change`, `running_mean`
            → `real` (pandas float64);
          - `cumcount` → `int`;
          - `cumsum`, `lag`, `lead` → preserva il tipo della colonna
            sorgente (approssimazione: lag/lead di int possono
            promuovere a float per NaN ma mantengono la semantica
            del dtype originale).
        - `column` mancante / non in input → schema invariato.
        """
        if not input_schemas:
            return []
        result = self._copy_schema(input_schemas[0], node_id)
        column = config.get("column") or ""
        if not column:
            return result
        by_name = {c.name: c for c in result}
        if column not in by_name:
            return result

        function = config.get("function") or "rank"
        valid_functions = {
            "rank", "dense_rank", "cumsum", "cumcount",
            "lag", "lead", "pct_change", "running_mean",
        }
        if function not in valid_functions:
            return result  # mirror runtime warning + return df

        output_col = config.get("output_column") or f"{column}_{function}"
        if output_col in by_name:
            return result

        if function in ("rank", "dense_rank", "pct_change", "running_mean"):
            out_type = "real"
        elif function == "cumcount":
            out_type = "int"
        else:  # cumsum, lag, lead
            out_type = by_name[column].type

        result.append(self._column_schema_cls(
            name=output_col,
            type=out_type,
            source_node=node_id,
            is_calculated=True,
        ))
        return result

    # — BE-PHASE2-SCHEMA-INFER-009-ZERO-RESIDUALS
    # Handler dedicati per i 2 transform precedentemente classificati
    # `MIGRATE_NEXT_NEEDS_RUNTIME_CONTRACT` (output dipendente dai dati
    # La direzione e' config-explicit: l'utente dichiara lo schema atteso
    # via `output_columns`. Quando il contratto e' assente, fallback
    # deterministico a passthrough (NON sampling, NON I/O
    # Forma del contratto `output_columns` (accettata in entrambe le
    # forme, mixed allowed
    # `List[str]` : ogni stringa diventa una colonna `str`
    # `List[{name, type}]`: tipo letto da `type`, default `str` se
    # vuoto/missing
    # Entry malformate (None, dict senza `name`, non-str/non-dict
    # vengono saltate (mirror della no-throw policy degli altri handler

    def _schema_flatten_json(
        self,
        input_schemas: List[List[ColumnSchema]],
        config: dict,
        node_id: str,
    ) -> List[ColumnSchema]:
        """flatten_json: input + N colonne dichiarate via `output_columns`.

        Runtime: `op_flatten_json` (analysis.py) espande le chiavi del
        dict parsato (data-dependent). Il contratto schema-inference
        e' invece **config-explicit** (BE-PHASE2-SCHEMA-INFER-009):
        l'utente dichiara le chiavi attese tramite
        `config["output_columns"]`. Le chiavi dichiarate vengono
        prefissate da `prefix` o, se assente, da `f"{column}_"`
        (allineato al runtime `col_prefix = prefix or f"{column}_"`).

        Fallback deterministico: se `output_columns` e' assente o
        vuota, lo schema dichiarato e' il passthrough dell'input
        (mirror del runtime no-op + warning, e mirror del fallback
        pre-009 che restava in `_schema_passthrough`).

        Collisioni con colonne esistenti: skip (no duplicate),
        allineato al pattern di tutti gli handler additivi.
        """
        if not input_schemas:
            return []
        result = self._copy_schema(input_schemas[0], node_id)
        output_cols = self._normalize_output_columns(config.get("output_columns"))
        if not output_cols:
            return result

        existing = {c.name for c in result}
        column = config.get("column") or ""
        prefix = config.get("prefix") or (f"{column}_" if column else "")
        for entry in output_cols:
            name = entry["name"]
            full_name = f"{prefix}{name}" if prefix else name
            if not full_name or full_name in existing:
                continue
            result.append(self._column_schema_cls(
                name=full_name,
                type=entry["type"],
                source_node=node_id,
                is_calculated=True,
            ))
            existing.add(full_name)
        return result

    def _schema_transpose(
        self,
        input_schemas: List[List[ColumnSchema]],
        config: dict,
        node_id: str,
    ) -> List[ColumnSchema]:
        """transpose: schema dichiarato via `output_columns`.

        Runtime: `op_transpose` (reshape.py) traspone le righe in
        colonne; i nomi delle nuove colonne dipendono dai valori
        della colonna `id_column` (data-dependent) o sono
        `f"col_{i}"` con N runtime-dependent. Il contratto
        schema-inference e' **config-explicit**: l'utente dichiara
        le colonne output attese tramite `config["output_columns"]`.

        Output:
        - se `id_column` e' specificata e presente nell'input, e'
          preservata in testa col tipo originale (mirror del runtime
          `result.rename(columns={"index": id_column})`);
        - tutte le entry di `output_columns` vengono aggiunte come
          colonne dichiarate.

        Fallback deterministico: se `output_columns` e' assente o
        vuota, lo schema dichiarato e' il passthrough dell'input.
        """
        if not input_schemas:
            return []
        output_cols = self._normalize_output_columns(config.get("output_columns"))
        if not output_cols:
            return self._copy_schema(input_schemas[0], node_id)

        input_by_name = {c.name: c for c in input_schemas[0]}
        result: List[ColumnSchema] = []
        seen: set = set()

        id_column = config.get("id_column") or ""
        if id_column and id_column in input_by_name:
            col = input_by_name[id_column]
            result.append(self._column_schema_cls(
                name=id_column,
                type=col.type,
                source_node=node_id,
                is_calculated=False,
            ))
            seen.add(id_column)

        for entry in output_cols:
            name = entry["name"]
            if not name or name in seen:
                continue
            result.append(self._column_schema_cls(
                name=name,
                type=entry["type"],
                source_node=node_id,
                is_calculated=True,
            ))
            seen.add(name)
        return result

    def _normalize_output_columns(
        self,
        raw,
    ) -> List[Dict[str, str]]:
        """Helper: normalizza `output_columns` in `List[{name, type}]`.

        Accetta:
        - `None` / non-list/non-str → `[]`;
        - `str` singolo non vuoto → `[{name: <s>, type: 'str'}]`;
        - `list` mista di:
          - `str` non vuoti → `{name, type: 'str'}`;
          - `dict` con `name` non vuoto → `{name, type: dict.type or 'str'}`;
          - altri valori (None, dict senza name, non-str/non-dict) → skip.

        Tipi non validati: il FE puo' inviare qualunque stringa per
        `type`. Default `'str'` per entry vuote/missing — coerente
        col pattern del registry per i nuovi field di nominazione.
        """
        if raw is None:
            return []
        if isinstance(raw, str):
            s = raw.strip()
            return [{"name": s, "type": "str"}] if s else []
        if not isinstance(raw, list):
            return []

        out: List[Dict[str, str]] = []
        for entry in raw:
            if isinstance(entry, str):
                s = entry.strip()
                if s:
                    out.append({"name": s, "type": "str"})
            elif isinstance(entry, dict):
                name = str(entry.get("name") or "").strip()
                if not name:
                    continue
                type_ = str(entry.get("type") or "").strip() or "str"
                out.append({"name": name, "type": type_})
            # else: skip non-str/non-dict entries
        return out

    def _normalize_str_list(self, raw) -> List[str]:
        """Helper: normalizza un valore di config in lista di stringhe.

        - `str` → split per virgola;
        - `list` → ogni elemento stringificato e strippato;
        - `None`/altro → lista vuota.

        Vuoti/whitespace-only filtrati. Usato per `id_columns`,
        `value_columns`, `left_keys`, `right_keys`, `compare_columns`,
        `stats` ecc. dove il FE puo' inviare entrambe le shape.
        """
        if raw is None:
            return []
        if isinstance(raw, str):
            return [c.strip() for c in raw.split(",") if c.strip()]
        if isinstance(raw, list):
            return [str(c).strip() for c in raw if str(c).strip()]
        return []

    def _copy_schema(
        self,
        schema: List[ColumnSchema],
        node_id: str,
    ) -> List[ColumnSchema]:
        """Helper pure: ricrea lo schema con `source_node` aggiornato a
        `node_id`. Estratto per evitare la replica del list-comprehension
        in tutti gli handler nuovi."""
        return [self._column_schema_cls(
            name=col.name,
            type=col.type,
            source_node=node_id,
            is_calculated=col.is_calculated,
        ) for col in schema]

    # METODI DI SUPPORTO

    def _schema_passthrough(
        self, 
        input_schemas: List[List[ColumnSchema]],
        node_id: str
    ) -> List[ColumnSchema]:
        """
        Passthrough: stesso schema dell'input
        """
        if not input_schemas:
            return []
        
        return [self._column_schema_cls(
            name=col.name,
            type=col.type,
            source_node=node_id,
            is_calculated=col.is_calculated
        ) for col in input_schemas[0]]
