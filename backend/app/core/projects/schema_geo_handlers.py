"""Geospatial transform schema handlers for schema inference."""

from __future__ import annotations

from typing import TYPE_CHECKING, List

if TYPE_CHECKING:
    from app.core.projects.schema_inference import ColumnSchema


class GeoSchemaHandlers:
    """Mixin for geospatial transform schema implementations."""

    def _schema_sjoin(
        self, 
        input_schemas: List[List[ColumnSchema]], 
        config: dict,
        node_id: str
    ) -> List[ColumnSchema]:
        """
        Spatial Join: simile a join ma con index_right aggiunto
        """
        if len(input_schemas) < 2:
            return input_schemas[0] if input_schemas else []
        
        left_schema = input_schemas[0]
        right_schema = input_schemas[1]
        
        result = []
        
        # Tutte le colonne da sinistra
        for col in left_schema:
            result.append(self._column_schema_cls(
                name=col.name,
                type=col.type,
                source_node=node_id,
                is_calculated=col.is_calculated
            ))
        
        # Colonne da destra (con suffisso per evitare conflitti
        left_names = {col.name for col in left_schema}
        for col in right_schema:
            new_name = col.name
            if new_name in left_names:
                new_name = f"{col.name}_right"
            result.append(self._column_schema_cls(
                name=new_name,
                type=col.type,
                source_node=node_id,
                is_calculated=col.is_calculated
            ))
        
        # GeoPandas aggiunge index_right
        result.append(self._column_schema_cls(
            name="index_right",
            type="int",
            source_node=node_id,
            is_calculated=True
        ))
        
        return result

    def _schema_geo_buffer(
        self, 
        input_schemas: List[List[ColumnSchema]], 
        config: dict,
        node_id: str
    ) -> List[ColumnSchema]:
        """
        Buffer: input + nuova colonna buffer (geometry:polygon)
        
        Se output_column è diverso da geom_column, aggiunge una nuova colonna.
        Se sono uguali, sostituisce la geometria esistente.
        """
        if not input_schemas:
            return []
        
        geom_col = config.get("geom_column", "geometry")
        output_col = config.get("output_column", "buffer_geom")
        
        # Copia tutte le colonne esistenti
        result = [self._column_schema_cls(
            name=col.name,
            type=col.type,
            source_node=node_id,
            is_calculated=col.is_calculated
        ) for col in input_schemas[0]]
        
        # Se output_column è diverso, aggiungi nuova colonna
        if output_col and output_col != geom_col:
            # Verifica che non esista già
            existing_names = {col.name for col in result}
            if output_col not in existing_names:
                result.append(self._column_schema_cls(
                    name=output_col,
                    type="geometry:polygon",
                    source_node=node_id,
                    is_calculated=True
                ))
        else:
            # Sostituisci la geometria esistente
            for i, col in enumerate(result):
                if col.name == geom_col:
                    result[i] = self._column_schema_cls(
                        name=col.name,
                        type="geometry:polygon",
                        source_node=node_id,
                        is_calculated=True
                    )
                    break
        
        return result

    def _schema_geo_centroid(
        self, 
        input_schemas: List[List[ColumnSchema]], 
        config: dict,
        node_id: str
    ) -> List[ColumnSchema]:
        """
        Centroid: input + nuova colonna centroid (geometry:point)
        
        Se output_column è diverso da geom_column, aggiunge una nuova colonna.
        """
        if not input_schemas:
            return []
        
        geom_col = config.get("geom_column", "geometry")
        output_col = config.get("output_column", "centroid")
        
        # Copia tutte le colonne esistenti
        result = [self._column_schema_cls(
            name=col.name,
            type=col.type,
            source_node=node_id,
            is_calculated=col.is_calculated
        ) for col in input_schemas[0]]
        
        # Se output_column è diverso, aggiungi nuova colonna
        if output_col and output_col != geom_col:
            existing_names = {col.name for col in result}
            if output_col not in existing_names:
                result.append(self._column_schema_cls(
                    name=output_col,
                    type="geometry:point",
                    source_node=node_id,
                    is_calculated=True
                ))
        else:
            # Sostituisci la geometria esistente
            for i, col in enumerate(result):
                if col.name == geom_col:
                    result[i] = self._column_schema_cls(
                        name=col.name,
                        type="geometry:point",
                        source_node=node_id,
                        is_calculated=True
                    )
                    break
        
        return result

    def _schema_geo_area(
        self, 
        input_schemas: List[List[ColumnSchema]], 
        config: dict,
        node_id: str
    ) -> List[ColumnSchema]:
        """
        Area: input + colonna area (real)
        """
        if not input_schemas:
            return []
        
        result = [self._column_schema_cls(
            name=col.name,
            type=col.type,
            source_node=node_id,
            is_calculated=col.is_calculated
        ) for col in input_schemas[0]]
        
        area_col = config.get("output_column", "area_m2")
        
        # Verifica che non esista già
        existing_names = {col.name for col in result}
        if area_col not in existing_names:
            result.append(self._column_schema_cls(
                name=area_col,
                type="real",
                source_node=node_id,
                is_calculated=True
            ))
        
        return result

    def _schema_geo_clip(
        self, 
        input_schemas: List[List[ColumnSchema]], 
        config: dict,
        node_id: str
    ) -> List[ColumnSchema]:
        """
        Clip: stesso schema del primo input (layer da ritagliare)
        """
        if not input_schemas:
            return []
        
        return self._schema_passthrough(input_schemas, node_id)

    def _schema_geo_dissolve(
        self, 
        input_schemas: List[List[ColumnSchema]], 
        config: dict,
        node_id: str
    ) -> List[ColumnSchema]:
        """
        Dissolve: colonna group by + geometria
        """
        if not input_schemas:
            return []
        
        by_column = config.get("by_column")
        geom_col = config.get("geom_column", "geometry")
        
        result = []
        
        # Se c'è group by, includi quella colonna
        if by_column:
            for col in input_schemas[0]:
                if col.name == by_column:
                    result.append(self._column_schema_cls(
                        name=col.name,
                        type=col.type,
                        source_node=node_id,
                        is_calculated=False
                    ))
                    break
        
        # Aggiungi colonna geometria
        result.append(self._column_schema_cls(
            name=geom_col,
            type="geometry",
            source_node=node_id,
            is_calculated=True
        ))
        
        return result

    def _schema_geo_convex_hull(
        self, 
        input_schemas: List[List[ColumnSchema]], 
        config: dict,
        node_id: str
    ) -> List[ColumnSchema]:
        """
        Convex Hull: input + nuova colonna convex_hull (geometry:polygon)
        """
        if not input_schemas:
            return []
        
        geom_col = config.get("geom_column", "geometry")
        output_col = config.get("output_column", "convex_hull")
        
        result = [self._column_schema_cls(
            name=col.name,
            type=col.type,
            source_node=node_id,
            is_calculated=col.is_calculated
        ) for col in input_schemas[0]]
        
        # Aggiungi nuova colonna se diversa
        if output_col and output_col != geom_col:
            existing_names = {col.name for col in result}
            if output_col not in existing_names:
                result.append(self._column_schema_cls(
                    name=output_col,
                    type="geometry:polygon",
                    source_node=node_id,
                    is_calculated=True
                ))
        
        return result

    def _schema_geo_overlay(
        self, 
        input_schemas: List[List[ColumnSchema]], 
        config: dict,
        node_id: str,
        overlay_type: str
    ) -> List[ColumnSchema]:
        """
        Overlay operations (intersection, union, difference): 
        Combina colonne da entrambi gli input + geometria risultante
        """
        _ = overlay_type
        if len(input_schemas) < 2:
            return input_schemas[0] if input_schemas else []
        
        left_schema = input_schemas[0]
        right_schema = input_schemas[1]
        
        result = []
        
        # Colonne da sinistra
        for col in left_schema:
            result.append(self._column_schema_cls(
                name=col.name,
                type=col.type,
                source_node=node_id,
                is_calculated=col.is_calculated
            ))
        
        # Colonne da destra (con suffisso per evitare conflitti
        left_names = {col.name for col in left_schema}
        for col in right_schema:
            # Skip geometry columns from right (result uses combined geometry
            if col.type.startswith("geometry"):
                continue
            new_name = col.name
            if new_name in left_names:
                new_name = f"{col.name}_2"
            result.append(self._column_schema_cls(
                name=new_name,
                type=col.type,
                source_node=node_id,
                is_calculated=col.is_calculated
            ))
        
        return result

    def _schema_geo_from_coords(
        self,
        input_schemas: List[List[ColumnSchema]],
        config: dict,
        node_id: str
    ) -> List[ColumnSchema]:
        """Geo from Coords: input + nuova colonna geometry"""
        if not input_schemas:
            return []
        
        output_col = config.get("output_column", "geometry")
        crs = config.get("crs", "EPSG:4326")
        
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
                type=f"geometry:point:{crs.replace('EPSG:', '')}",
                source_node=node_id,
                is_calculated=True
            ))
        
        return result

    def _schema_geo_distance(
        self,
        input_schemas: List[List[ColumnSchema]],
        config: dict,
        node_id: str,
    ) -> List[ColumnSchema]:
        """geo_distance: input sinistro + colonna `output_column` (real).

        Runtime: `op_geo_distance` (spatial.py) e' multi-input (dfs[0] =
        left, dfs[1] = right). L'output e' un GeoDataFrame con TUTTE le
        colonne del LEFT + `output_column` (distanza float). Il right
        non contribuisce colonne allo schema output.

        Right input vuoto/mancante: il runtime ritorna `dfs[0]` (o un
        empty GDF) — lo schema dichiarato resta comunque LEFT +
        output_column (allineato col percorso success-path canonico).
        """
        if not input_schemas:
            return []

        # left schema (primo input). Se manca anche un secondo input
        # il runtime degrada graziosamente; lo schema FE annunciato resta
        # comunque coerente col success-path canonico (left + distance
        result = self._copy_schema(input_schemas[0], node_id)
        output_col = config.get("output_column") or "distance_m"
        if output_col and output_col not in {c.name for c in result}:
            result.append(self._column_schema_cls(
                name=output_col,
                type="real",
                source_node=node_id,
                is_calculated=True,
            ))
        return result

    def _schema_geo_to_wkt(
        self,
        input_schemas: List[List[ColumnSchema]],
        config: dict,
        node_id: str,
    ) -> List[ColumnSchema]:
        """geo_to_wkt: input + 1 o 3 colonne in base a `mode`.

        Runtime: `op_geo_to_wkt` (spatial.py) supporta 3 modalita':
        - `"wkt"` (default): aggiunge `wkt_column` (str)
        - `"latlon"`: aggiunge `lat_column` (real) + `lon_column` (real)
        - `"both"`: aggiunge tutte e 3

        Config defaults: `mode="wkt"`, `wkt_column="geometry_wkt"`,
        `lat_column="latitude"`, `lon_column="longitude"`.
        """
        if not input_schemas:
            return []

        result = self._copy_schema(input_schemas[0], node_id)
        existing = {c.name for c in result}

        mode = (config.get("mode") or "wkt").lower().strip()
        if mode not in ("wkt", "latlon", "both"):
            mode = "wkt"  # allineato al default runtime

        if mode in ("wkt", "both"):
            wkt_col = config.get("wkt_column") or "geometry_wkt"
            if wkt_col and wkt_col not in existing:
                result.append(self._column_schema_cls(
                    name=wkt_col, type="str",
                    source_node=node_id, is_calculated=True,
                ))
                existing.add(wkt_col)

        if mode in ("latlon", "both"):
            lat_col = config.get("lat_column") or "latitude"
            lon_col = config.get("lon_column") or "longitude"
            if lat_col and lat_col not in existing:
                result.append(self._column_schema_cls(
                    name=lat_col, type="real",
                    source_node=node_id, is_calculated=True,
                ))
                existing.add(lat_col)
            if lon_col and lon_col not in existing:
                result.append(self._column_schema_cls(
                    name=lon_col, type="real",
                    source_node=node_id, is_calculated=True,
                ))
                existing.add(lon_col)

        return result

    def _schema_geo_length(
        self,
        input_schemas: List[List[ColumnSchema]],
        config: dict,
        node_id: str,
    ) -> List[ColumnSchema]:
        """geo_length: input + colonna `output_column` (real).

        Runtime: `op_geo_length` (spatial.py) aggiunge `output_column`
        (default `"length_m"`) come float in metri (auto-UTM se il CRS
        e' geografico). `geom_column` e `metric_crs` non influenzano
        lo schema FE.
        """
        if not input_schemas:
            return []
        result = self._copy_schema(input_schemas[0], node_id)
        output_col = config.get("output_column") or "length_m"
        if output_col and output_col not in {c.name for c in result}:
            result.append(self._column_schema_cls(
                name=output_col,
                type="real",
                source_node=node_id,
                is_calculated=True,
            ))
        return result

    def _schema_geo_perimeter(
        self,
        input_schemas: List[List[ColumnSchema]],
        config: dict,
        node_id: str,
    ) -> List[ColumnSchema]:
        """geo_perimeter: input + colonna `output_column` (real).

        Runtime: `op_geo_perimeter` (spatial.py) aggiunge `output_column`
        (default `"perimeter_m"`) come float in metri. `metric_crs` non
        impatta lo schema.
        """
        if not input_schemas:
            return []
        result = self._copy_schema(input_schemas[0], node_id)
        output_col = config.get("output_column") or "perimeter_m"
        if output_col and output_col not in {c.name for c in result}:
            result.append(self._column_schema_cls(
                name=output_col,
                type="real",
                source_node=node_id,
                is_calculated=True,
            ))
        return result

    def _schema_geo_vertex_count(
        self,
        input_schemas: List[List[ColumnSchema]],
        config: dict,
        node_id: str,
    ) -> List[ColumnSchema]:
        """geo_vertex_count: input + colonna `output_column` (int).

        Runtime: `op_geo_vertex_count` (spatial.py) aggiunge il numero
        di vertici per ogni geometria (default colonna `"vertex_count"`).
        """
        if not input_schemas:
            return []
        result = self._copy_schema(input_schemas[0], node_id)
        output_col = config.get("output_column") or "vertex_count"
        if output_col and output_col not in {c.name for c in result}:
            result.append(self._column_schema_cls(
                name=output_col,
                type="int",
                source_node=node_id,
                is_calculated=True,
            ))
        return result

    def _schema_geo_bounds_extractor(
        self,
        input_schemas: List[List[ColumnSchema]],
        config: dict,
        node_id: str,
    ) -> List[ColumnSchema]:
        """geo_bounds_extractor: input + 4 colonne bounding box (real).

        Runtime: `op_geo_bounds_extractor` (spatial.py) aggiunge
        `f"{prefix}min_x"`, `f"{prefix}min_y"`, `f"{prefix}max_x"`,
        `f"{prefix}max_y"` (notazione `min_x`, NON `minx` — il prefisso
        e' applicato al nome canonico con underscore). `prefix` default
        `""`.
        """
        if not input_schemas:
            return []
        result = self._copy_schema(input_schemas[0], node_id)
        prefix = config.get("prefix") or ""
        existing = {c.name for c in result}
        for suffix in ("min_x", "min_y", "max_x", "max_y"):
            col_name = f"{prefix}{suffix}"
            if col_name and col_name not in existing:
                result.append(self._column_schema_cls(
                    name=col_name,
                    type="real",
                    source_node=node_id,
                    is_calculated=True,
                ))
                existing.add(col_name)
        return result

    def _schema_geo_count_points_in_polygons(
        self,
        input_schemas: List[List[ColumnSchema]],
        config: dict,
        node_id: str,
    ) -> List[ColumnSchema]:
        """geo_count_points_in_polygons: LEFT (poligoni) + colonna
        conteggio (int) + colonna aggregata opzionale (real).

        Runtime: `op_geo_count_points_in_polygons` (spatial.py) e'
        multi-input (dfs[0]=poligoni LEFT, dfs[1]=punti RIGHT). Output:
        - tutte le colonne del LEFT (poligoni)
        - `count_column` (default `"point_count"`, int)
        - se `aggregate_column` non vuoto: `f"{aggregate_column}_{func}"`
          dove `func` e' uno tra `sum/mean/min/max` (default `"sum"`,
          fallback su valori unknown). Tipo: `real`.

        Il RIGHT non contribuisce colonne. La colonna temporanea
        `__poly_idx__` viene droppata dal runtime, quindi non appare
        nello schema dichiarato.
        """
        if not input_schemas:
            return []
        result = self._copy_schema(input_schemas[0], node_id)
        existing = {c.name for c in result}

        count_col = config.get("count_column") or "point_count"
        if count_col and count_col not in existing:
            result.append(self._column_schema_cls(
                name=count_col,
                type="int",
                source_node=node_id,
                is_calculated=True,
            ))
            existing.add(count_col)

        agg_col = config.get("aggregate_column") or ""
        if agg_col:
            raw_func = config.get("aggregate_func") or "sum"
            func = raw_func if raw_func in ("sum", "mean", "min", "max") else "sum"
            agg_name = f"{agg_col}_{func}"
            if agg_name not in existing:
                result.append(self._column_schema_cls(
                    name=agg_name,
                    type="real",
                    source_node=node_id,
                    is_calculated=True,
                ))
                existing.add(agg_name)
        return result

    def _schema_geo_line_builder(
        self,
        input_schemas: List[List[ColumnSchema]],
        config: dict,
        node_id: str,
    ) -> List[ColumnSchema]:
        """geo_line_builder: ristrutturazione completa.

        Runtime: `op_geo_line_builder` (spatial.py) raggruppa per
        `group_column` e produce un GeoDataFrame con SOLO le colonne
        `[group_column, output_column]`. Tutte le altre colonne
        dell'input vengono scartate.

        Output:
        - `group_column` con tipo dell'input (preservato se trovato).
        - `output_column` (default `"geometry"`) di tipo
          `geometry:line`.

        Se `group_column` manca dalla config: schema invariato
        (passthrough). Senza `group_column` il runtime crash a
        groupby; non guess su nuovo schema.
        """
        return self._schema_geo_builder(
            input_schemas, config, node_id, geom_subtype="line",
        )

    def _schema_geo_polygon_builder(
        self,
        input_schemas: List[List[ColumnSchema]],
        config: dict,
        node_id: str,
    ) -> List[ColumnSchema]:
        """geo_polygon_builder: ristrutturazione completa.

        Runtime: `op_geo_polygon_builder` (spatial.py) raggruppa per
        `group_column` e produce un GeoDataFrame con SOLO le colonne
        `[group_column, output_column]`. output_column di tipo
        `geometry:polygon` (Polygon chiuso automaticamente).
        """
        return self._schema_geo_builder(
            input_schemas, config, node_id, geom_subtype="polygon",
        )

    def _schema_geo_builder(
        self,
        input_schemas: List[List[ColumnSchema]],
        config: dict,
        node_id: str,
        geom_subtype: str,
    ) -> List[ColumnSchema]:
        """Helper condiviso per `geo_line_builder` / `geo_polygon_builder`.

        I due transform hanno la stessa shape (rebuild a 2 colonne)
        ma un sottotipo geometrico diverso.
        """
        if not input_schemas:
            return []
        group_col = config.get("group_column") or ""
        output_col = config.get("output_column") or "geometry"
        if not group_col:
            return self._copy_schema(input_schemas[0], node_id)

        # Preserva il tipo originale di group_column se presente
        input_by_name = {c.name: c for c in input_schemas[0]}
        group_type = input_by_name[group_col].type if group_col in input_by_name else "str"

        result: List[ColumnSchema] = [
            self._column_schema_cls(
                name=group_col,
                type=group_type,
                source_node=node_id,
                is_calculated=False,
            ),
        ]
        if output_col and output_col != group_col:
            result.append(self._column_schema_cls(
                name=output_col,
                type=f"geometry:{geom_subtype}",
                source_node=node_id,
                is_calculated=True,
            ))
        return result

    def _schema_geo_point_on_surface(
        self,
        input_schemas: List[List[ColumnSchema]],
        config: dict,
        node_id: str,
    ) -> List[ColumnSchema]:
        """geo_point_on_surface: input + colonna `output_column`
        (geometry:point), oppure sostituisce la geometria sorgente.

        Runtime: `op_geo_point_on_surface` (spatial.py) chiama
        `representative_point` e lo scrive in `output_column`
        (default `"point_on_surface"`). Se `output_column` coincide
        con `geom_column` la sorgente viene sovrascritta a
        `geometry:point`. Stesso pattern del `_schema_geo_centroid`
        legacy.
        """
        if not input_schemas:
            return []
        geom_col = config.get("geom_column", "geometry")
        output_col = config.get("output_column") or "point_on_surface"
        result = self._copy_schema(input_schemas[0], node_id)

        if output_col and output_col != geom_col:
            existing = {c.name for c in result}
            if output_col not in existing:
                result.append(self._column_schema_cls(
                    name=output_col,
                    type="geometry:point",
                    source_node=node_id,
                    is_calculated=True,
                ))
        else:
            for i, col in enumerate(result):
                if col.name == geom_col:
                    result[i] = self._column_schema_cls(
                        name=col.name,
                        type="geometry:point",
                        source_node=node_id,
                        is_calculated=True,
                    )
                    break
        return result

    def _schema_geo_boundary(
        self,
        input_schemas: List[List[ColumnSchema]],
        config: dict,
        node_id: str,
    ) -> List[ColumnSchema]:
        """geo_boundary: input + colonna `output_column` (geometry).

        Runtime: `op_geo_boundary` (spatial.py) calcola
        `gdf.geometry.boundary` e lo scrive in `output_column`
        (default `"boundary"`). Il sottotipo dipende dall'input
        (poligono → line, line → multipoint), quindi il tipo
        dichiarato e' `"geometry"` generico — non vincoliamo a
        sottotipo per non escludere downstream legittimi.

        Se `output_column == geom_column`, sovrascrive la geometria
        sorgente a `"geometry"` (perde subtype precedente, allineato
        al runtime che cambia il tipo geometrico).
        """
        if not input_schemas:
            return []
        geom_col = config.get("geom_column", "geometry")
        output_col = config.get("output_column") or "boundary"
        result = self._copy_schema(input_schemas[0], node_id)

        if output_col and output_col != geom_col:
            existing = {c.name for c in result}
            if output_col not in existing:
                result.append(self._column_schema_cls(
                    name=output_col,
                    type="geometry",
                    source_node=node_id,
                    is_calculated=True,
                ))
        else:
            for i, col in enumerate(result):
                if col.name == geom_col:
                    result[i] = self._column_schema_cls(
                        name=col.name,
                        type="geometry",
                        source_node=node_id,
                        is_calculated=True,
                    )
                    break
        return result

    def _schema_geo_voronoi(
        self,
        input_schemas: List[List[ColumnSchema]],
        config: dict,
        node_id: str,
    ) -> List[ColumnSchema]:
        """geo_voronoi: input + sostituzione del tipo della colonna
        geometria a `geometry:polygon`.

        Runtime: `op_geo_voronoi` (spatial.py) preserva tutte le colonne
        di input (`result = gdf.copy`) e sovrascrive `result['geometry']`
        con poligoni di Voronoi. Il numero di righe non cambia (1
        polygon per punto input).

        Schema delta: cambia il tipo della colonna `geom_column`
        (default `"geometry"`) a `geometry:polygon` e marca la colonna
        come `is_calculated=True`. Se la colonna `geom_column` non
        esiste nello schema input, ritorna passthrough invariato
        (caso degenere, non si tenta di indovinare).
        """
        if not input_schemas:
            return []
        geom_col = config.get("geom_column") or "geometry"
        result = self._copy_schema(input_schemas[0], node_id)
        for i, col in enumerate(result):
            if col.name == geom_col:
                result[i] = self._column_schema_cls(
                    name=col.name,
                    type="geometry:polygon",
                    source_node=node_id,
                    is_calculated=True,
                )
                break
        return result

    def _schema_geo_within(
        self,
        input_schemas: List[List[ColumnSchema]],
        node_id: str,
    ) -> List[ColumnSchema]:
        """geo_within: filter-like su LEFT.

        Runtime: `op_geo_within` (spatial.py) esegue uno spatial join
        `predicate="within"` poi proietta SOLO le colonne LEFT
        (`result = joined[keep_cols]`) e fa drop_duplicates. Schema
        delta nullo: output = LEFT.
        """
        if not input_schemas:
            return []
        return self._copy_schema(input_schemas[0], node_id)

    def _schema_geo_nearest(
        self,
        input_schemas: List[List[ColumnSchema]],
        config: dict,
        node_id: str,
    ) -> List[ColumnSchema]:
        """geo_nearest: LEFT + RIGHT non-geom + colonna distanza.

        Runtime: `op_geo_nearest` (spatial.py) usa
        `gpd.sjoin_nearest(left, right, how="left", distance_col=...)`.
        - Output preserva tutte le colonne di LEFT.
        - Aggiunge le colonne non-geometriche di RIGHT (geopandas
          rimuove la geometria del RIGHT in sjoin/sjoin_nearest), con
          suffisso `_right` sulle collisioni con LEFT (allineato al
          pattern del legacy `_schema_sjoin`).
        - Aggiunge `output_column` (default `"distance_m"`, real).
        - Il runtime droppa esplicitamente `index_right`, quindi NON
          viene aggiunto allo schema (differisce da `_schema_sjoin`).
        """
        if len(input_schemas) < 2:
            return input_schemas[0] if input_schemas else []

        left_schema = input_schemas[0]
        right_schema = input_schemas[1]
        result = self._copy_schema(left_schema, node_id)
        left_names = {c.name for c in result}

        for col in right_schema:
            if col.type.startswith("geometry"):
                continue
            new_name = (
                f"{col.name}_right" if col.name in left_names else col.name
            )
            result.append(self._column_schema_cls(
                name=new_name,
                type=col.type,
                source_node=node_id,
                is_calculated=col.is_calculated,
            ))

        output_col = config.get("output_column") or "distance_m"
        if output_col and output_col not in {c.name for c in result}:
            result.append(self._column_schema_cls(
                name=output_col,
                type="real",
                source_node=node_id,
                is_calculated=True,
            ))
        return result
