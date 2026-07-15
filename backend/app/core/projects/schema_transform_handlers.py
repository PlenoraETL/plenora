"""Transform schema handlers for schema inference."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, List

from app.core.projects.types import TransformHandlerRegistry
from app.core.projects.schema_formula_handlers import FormulaSchemaHandlers
from app.core.projects.schema_geo_handlers import GeoSchemaHandlers
from app.core.projects.schema_join_handlers import JoinSchemaHandlers
from app.core.projects.schema_reshape_handlers import ReshapeSchemaHandlers
from app.core.projects.schema_table_handlers import TableSchemaHandlers

if TYPE_CHECKING:
    from app.shared.execution.schema_types import ColumnSchema


class TransformSchemaHandlers:
    """Owns transform_op dispatch and transform schema implementations."""

    def __init__(self, column_schema_cls: type):
        self._column_schema_cls = column_schema_cls
        self._join_handlers = JoinSchemaHandlers()
        self._geo_handlers = GeoSchemaHandlers()
        self._reshape_handlers = ReshapeSchemaHandlers()
        self._formula_handlers = FormulaSchemaHandlers()
        self._table_handlers = TableSchemaHandlers()
        for handler in (
            self._join_handlers,
            self._geo_handlers,
            self._reshape_handlers,
            self._formula_handlers,
            self._table_handlers,
        ):
            self._configure_handler(handler)

    def _configure_handler(self, handler: object) -> None:
        """Give composed handlers the shared schema-construction helpers."""
        handler._column_schema_cls = self._column_schema_cls
        handler._copy_column = self._copy_column
        handler._copy_schema = self._copy_schema
        handler._normalize_str_list = self._normalize_str_list
        handler._schema_passthrough = self._schema_passthrough

    def build_transform_handlers(
        self,
        edges_by_target: dict,
    ) -> TransformHandlerRegistry:
        """Public dispatcher boundary for SchemaInferenceService composition."""
        return self._build_transform_handlers(edges_by_target)

    def schema_passthrough(
        self,
        input_schemas: List[List[ColumnSchema]],
        node_id: str,
    ) -> List[ColumnSchema]:
        """Public fallback boundary for unknown transform operations."""
        return self._schema_passthrough(input_schemas, node_id)

    def _build_transform_handlers(
        self,
        edges_by_target: dict,
    ) -> TransformHandlerRegistry:
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
        - `drop` -> `_schema_drop` (alias compatibile per `drop_columns`)
        - `geo_to_crs` -> `_schema_passthrough` (alias legacy per
          `geo_reproject`)

        i transform_op tra i marker `# DISPATCH-REGISTRY:start` e
        `# DISPATCH-REGISTRY:end` qui sotto.
        """
        def pt(
            i: List[List[ColumnSchema]],
            _c: dict,
            n: str,
        ) -> List[ColumnSchema]:
            return self._schema_passthrough(i, n)

        return {
            # DISPATCH-REGISTRY:start
            # base
            "join": lambda i, c, n: self._schema_join(i, c, n, edges_by_target),
            "sjoin": lambda i, c, n: self._geo_handlers._schema_sjoin(i, c, n),
            "aggregate": lambda i, c, n: self._table_handlers._schema_aggregate(i, c, n),
            "formula": lambda i, c, n: self._formula_handlers._schema_formula(i, c, n),
            "filter": lambda i, c, n: self._schema_filter(i, n),
            "concat": lambda i, c, n: self._table_handlers._schema_concat(i, c, n),
            "sort": lambda i, c, n: self._schema_sort(i, n),
            "distinct": lambda i, c, n: self._table_handlers._schema_distinct(i, c, n),
            "rename": lambda i, c, n: self._schema_rename(i, c, n),
            "drop": lambda i, c, n: self._schema_drop(i, c, n),
            "drop_columns": lambda i, c, n: self._schema_drop(i, c, n),
            "fill_na": pt,
            "replace": pt,
            "split_column": lambda i, c, n: self._schema_split_column(i, c, n),
            # geo
            "geo_buffer": lambda i, c, n: self._geo_handlers._schema_geo_buffer(i, c, n),
            "geo_centroid": lambda i, c, n: self._geo_handlers._schema_geo_centroid(i, c, n),
            "geo_area": lambda i, c, n: self._geo_handlers._schema_geo_area(i, c, n),
            "geo_clip": lambda i, c, n: self._geo_handlers._schema_geo_clip(i, c, n),
            "geo_dissolve": lambda i, c, n: self._geo_handlers._schema_geo_dissolve(i, c, n),
            "geo_simplify": pt,
            "geo_convex_hull": lambda i, c, n: self._geo_handlers._schema_geo_convex_hull(i, c, n),
            "geo_reproject": pt,
            "geo_intersection": lambda i, c, n: self._geo_handlers._schema_geo_overlay(i, c, n, "intersection"),
            "geo_union": lambda i, c, n: self._geo_handlers._schema_geo_overlay(i, c, n, "union"),
            "geo_difference": lambda i, c, n: self._geo_handlers._schema_geo_overlay(i, c, n, "difference"),
            "geo_to_crs": pt,
            # privacy
            "md5_hash": lambda i, c, n: self._schema_md5_hash(i, c, n),
            "mask_data": lambda i, c, n: self._schema_mask_data(i, c, n),
            # base aggiuntive
            "type_cast": lambda i, c, n: self._schema_type_cast(i, c, n),
            "conditional": lambda i, c, n: self._formula_handlers._schema_conditional(i, c, n),
            "text_normalize": lambda i, c, n: self._schema_text_normalize(i, c, n),
            # reshape / output strutturali
            "concat_columns": lambda i, c, n: self._schema_concat_columns(i, c, n),
            "pivot": lambda i, c, n: self._reshape_handlers._schema_pivot(i, c, n),
            "geo_envelope": lambda i, c, n: self._geo_handlers._schema_geo_envelope(i, c, n),
            # spatial
            "geo_from_coords": lambda i, c, n: self._geo_handlers._schema_geo_from_coords(i, c, n),
            "add_row_number": lambda i, c, n: self._schema_add_row_number(i, c, n),
            "uuid_generator": lambda i, c, n: self._schema_uuid_generator(i, c, n),
            "string_length": lambda i, c, n: self._schema_string_length(i, c, n),
            "date_extract": lambda i, c, n: self._schema_date_extract(i, c, n),
            "geo_distance": lambda i, c, n: self._geo_handlers._schema_geo_distance(i, c, n),
            "geo_to_wkt": lambda i, c, n: self._geo_handlers._schema_geo_to_wkt(i, c, n),
            "geo_length": lambda i, c, n: self._geo_handlers._schema_geo_length(i, c, n),
            "geo_perimeter": lambda i, c, n: self._geo_handlers._schema_geo_perimeter(i, c, n),
            "geo_vertex_count": lambda i, c, n: self._geo_handlers._schema_geo_vertex_count(i, c, n),
            "string_extract": lambda i, c, n: self._schema_string_extract(i, c, n),
            "bin": lambda i, c, n: self._schema_bin(i, c, n),
            "string_pad": lambda i, c, n: self._schema_string_pad(i, c, n),
            "geo_bounds_extractor": lambda i, c, n: self._geo_handlers._schema_geo_bounds_extractor(i, c, n),
            "geo_count_points_in_polygons": lambda i, c, n: self._geo_handlers._schema_geo_count_points_in_polygons(i, c, n),
            "geo_line_builder": lambda i, c, n: self._geo_handlers._schema_geo_line_builder(i, c, n),
            "geo_polygon_builder": lambda i, c, n: self._geo_handlers._schema_geo_polygon_builder(i, c, n),
            "geo_point_on_surface": lambda i, c, n: self._geo_handlers._schema_geo_point_on_surface(i, c, n),
            "geo_boundary": lambda i, c, n: self._geo_handlers._schema_geo_boundary(i, c, n),
            "reorder_columns": lambda i, c, n: self._table_handlers._schema_reorder_columns(i, c, n),
            "geo_voronoi": lambda i, c, n: self._geo_handlers._schema_geo_voronoi(i, c, n),
            "cross_join": lambda i, c, n: self._join_handlers._schema_cross_join(i, n),
            "geo_within": lambda i, c, n: self._geo_handlers._schema_geo_within(i, n),
            "geo_overlay": lambda i, c, n: self._geo_handlers._schema_geo_overlay(
                i, c, n, c.get("how", "intersection"),
            ),
            "geo_symmetric_difference": lambda i, c, n: self._geo_handlers._schema_geo_overlay(
                i, c, n, "symmetric_difference",
            ),
            "geo_nearest": lambda i, c, n: self._geo_handlers._schema_geo_nearest(i, c, n),
            "lookup": lambda i, c, n: self._join_handlers._schema_lookup(i, c, n),
            "table_diff": lambda i, c, n: self._join_handlers._schema_table_diff(i, c, n),
            "melt": lambda i, c, n: self._reshape_handlers._schema_melt(i, c, n),
            "statistics": lambda i, c, n: self._table_handlers._schema_statistics(i, c, n),
            "window_function": lambda i, c, n: self._table_handlers._schema_window_function(i, c, n),
            # —
            # config-explicit schema contracts; safe deterministic
            # fallback (passthrough) when `output_columns` is absent
            "flatten_json": lambda i, c, n: self._reshape_handlers._schema_flatten_json(i, c, n),
            "transpose": lambda i, c, n: self._reshape_handlers._schema_transpose(i, c, n),
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
        return self._join_handlers._schema_join(
            input_schemas,
            config,
            node_id,
            edges_by_target,
        )

    def _schema_filter(
        self, 
        input_schemas: List[List[ColumnSchema]],
        node_id: str
    ) -> List[ColumnSchema]:
        """
        Filter: stesso schema (filtra righe, non colonne)
        """
        return self._schema_passthrough(input_schemas, node_id)
    
    def _schema_sort(
        self, 
        input_schemas: List[List[ColumnSchema]],
        node_id: str
    ) -> List[ColumnSchema]:
        """
        Sort: stesso schema (cambia solo ordine righe)
        """
        return self._schema_passthrough(input_schemas, node_id)
    
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
        if not rename_map and isinstance(config.get("renames"), list):
            rename_map = {
                r.get("old_name"): r.get("new_name")
                for r in config.get("renames", [])
                if isinstance(r, dict) and r.get("old_name") and r.get("new_name")
            }
        if not rename_map and config.get("old_name") and config.get("new_name"):
            rename_map = {config["old_name"]: config["new_name"]}
        
        result = []
        for col in input_schemas[0]:
            new_name = rename_map.get(col.name, col.name)
            result.append(self._copy_column(col, node_id, name=new_name))
        
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
                result.append(self._copy_column(col, node_id))
        
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
        
        result = self._copy_schema(input_schemas[0], node_id)
        
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
        
        result = self._copy_schema(input_schemas[0], node_id)
        
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
        
        result = self._copy_schema(input_schemas[0], node_id)
        
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
                result.append(self._copy_column(col, node_id, type_=new_type, is_calculated=True))
            else:
                result.append(self._copy_column(col, node_id))
        
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
        
        result = self._copy_schema(input_schemas[0], node_id)
        
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

    def _schema_concat_columns(
        self,
        input_schemas: List[List[ColumnSchema]],
        config: dict,
        node_id: str,
    ) -> List[ColumnSchema]:
        """concat_columns: input + colonna stringa `output_column`.

        Runtime: `op_concat_columns` preserva tutte le colonne input e scrive
        la concatenazione in `output_column` (default `concatenated`). La
        schema inference deve quindi esporre la nuova colonna ai nodi a valle.
        """
        if not input_schemas:
            return []

        result = self._copy_schema(input_schemas[0], node_id)
        output_col = config.get("output_column") or "concatenated"
        if output_col and output_col not in {c.name for c in result}:
            result.append(self._column_schema_cls(
                name=output_col,
                type="str",
                source_node=node_id,
                is_calculated=True,
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
        if not column or column not in {c.name for c in result}:
            return result
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
                    result[i] = self._copy_column(col, node_id, type_="str", is_calculated=True)
                    break
            return result

        if target in existing_names:
            for i, col in enumerate(result):
                if col.name == target:
                    result[i] = self._copy_column(col, node_id, type_="str", is_calculated=True)
                    break
        else:
            result.append(self._column_schema_cls(
                name=target,
                type="str",
                source_node=node_id,
                is_calculated=True,
            ))
        return result

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
        return [self._copy_column(
            col,
            node_id,
        ) for col in schema]

    def _copy_column(
        self,
        col: ColumnSchema,
        node_id: str,
        *,
        name: str | None = None,
        type_: str | None = None,
        is_calculated: bool | None = None,
    ) -> ColumnSchema:
        """Copy a schema column preserving stable identity metadata."""
        return self._column_schema_cls(
            name=col.name,
            type=type_ or col.type,
            id=getattr(col, "id", None),
            source_name=getattr(col, "source_name", None) or col.name,
            source_node=node_id,
            is_calculated=col.is_calculated if is_calculated is None else is_calculated,
        ) if name is None else self._column_schema_cls(
            name=name,
            type=type_ or col.type,
            id=getattr(col, "id", None),
            source_name=getattr(col, "source_name", None) or col.name,
            source_node=node_id,
            is_calculated=col.is_calculated if is_calculated is None else is_calculated,
        )

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
        
        return self._copy_schema(input_schemas[0], node_id)
