"""
Spatial Operations - Operazioni geospaziali per MANIPOLA.

Contiene tutte le trasformazioni geo_*:
- sjoin, buffer, centroid, area, clip
- dissolve, intersection, union, difference
- simplify, convex_hull, reproject
"""

import pandas as pd
import geopandas as gpd
from typing import List

from models import Node
from utils.logger import get_logger
from utils.geo_utils import (
    to_geodataframe,
    geometry_to_wkt,
    ensure_same_crs,
    get_utm_crs_for_geometry
)

logger = get_logger(__name__)


class SpatialOperations:
    """Operazioni geospaziali per trasformazione dati."""
    
    # =========================================================================
    # SPATIAL JOIN
    # =========================================================================
    
    def op_sjoin(self, inputs: List[pd.DataFrame], node: Node) -> pd.DataFrame:
        """Spatial join tra due GeoDataFrame."""
        if len(inputs) < 2:
            return inputs[0] if inputs else pd.DataFrame()
        
        config = node.data.get('config', {}) if node.data else {}
        predicate = config.get('predicate', 'intersects')
        how = config.get('how', 'inner')
        left_geom = config.get('left_geom')
        right_geom = config.get('right_geom')
        
        try:
            left_gdf = to_geodataframe(inputs[0], left_geom)
            right_gdf = to_geodataframe(inputs[1], right_geom)
            
            left_gdf, right_gdf = ensure_same_crs(left_gdf, right_gdf)
            
            result = gpd.sjoin(left_gdf, right_gdf, how=how, predicate=predicate)
            
            if 'index_right' in result.columns:
                result = result.drop(columns=['index_right'])
            
            # Converti geometry in WKT
            if 'geometry' in result.columns:
                result['geometry'] = result['geometry'].apply(geometry_to_wkt)
            
            logger.info(f"Spatial Join completato: {len(result)} righe")
            return pd.DataFrame(result)
            
        except Exception as e:
            logger.error(f"Errore Spatial Join: {e}")
            return inputs[0] if inputs else pd.DataFrame()

    # =========================================================================
    # BUFFER
    # =========================================================================
    
    def op_geo_buffer(self, df: pd.DataFrame, node: Node) -> pd.DataFrame:
        """Crea buffer intorno alle geometrie."""
        config = node.data.get('config', {}) if node.data else {}
        geom_column = config.get('geom_column')
        distance = config.get('distance', 100)
        output_column = config.get('output_column', 'buffer_geom')
        
        try:
            gdf = to_geodataframe(df, geom_column)
            original_crs = gdf.crs
            
            # Riproietta in CRS metrico se necessario
            if original_crs and original_crs.is_geographic:
                utm_crs = get_utm_crs_for_geometry(gdf)
                gdf_projected = gdf.to_crs(utm_crs)
            else:
                gdf_projected = gdf
            
            buffer_geom = gdf_projected.geometry.buffer(distance)
            
            # Riproietta al CRS originale
            if original_crs and original_crs.is_geographic:
                buffer_gdf = gpd.GeoDataFrame(geometry=buffer_geom, crs=gdf_projected.crs)
                buffer_gdf = buffer_gdf.to_crs(original_crs)
                buffer_geom = buffer_gdf.geometry
            
            result = df.copy()
            result[output_column] = buffer_geom.apply(geometry_to_wkt)
            
            logger.info(f"Buffer completato: colonna '{output_column}' aggiunta")
            return result
            
        except Exception as e:
            logger.error(f"Errore Buffer: {e}")
            return df

    # =========================================================================
    # CENTROID
    # =========================================================================
    
    def op_geo_centroid(self, df: pd.DataFrame, node: Node) -> pd.DataFrame:
        """Calcola centroide delle geometrie."""
        config = node.data.get('config', {}) if node.data else {}
        geom_column = config.get('geom_column')
        output_column = config.get('output_column', 'centroid')
        
        try:
            gdf = to_geodataframe(df, geom_column)
            centroids = gdf.geometry.centroid
            
            result = df.copy()
            result[output_column] = centroids.apply(geometry_to_wkt)
            
            logger.info(f"Centroid completato: colonna '{output_column}' aggiunta")
            return result
            
        except Exception as e:
            logger.error(f"Errore Centroid: {e}")
            return df

    # =========================================================================
    # AREA
    # =========================================================================
    
    def op_geo_area(self, df: pd.DataFrame, node: Node) -> pd.DataFrame:
        """Calcola area in metri quadrati."""
        config = node.data.get('config', {}) if node.data else {}
        geom_column = config.get('geom_column')
        output_column = config.get('output_column', 'area_m2')
        
        try:
            gdf = to_geodataframe(df, geom_column)
            
            # Riproietta in CRS metrico se necessario
            if gdf.crs and gdf.crs.is_geographic:
                utm_crs = get_utm_crs_for_geometry(gdf)
                gdf_projected = gdf.to_crs(utm_crs)
            else:
                gdf_projected = gdf
            
            result = df.copy()
            result[output_column] = gdf_projected.geometry.area
            
            logger.info(f"Area completato: colonna '{output_column}' aggiunta")
            return result
            
        except Exception as e:
            logger.error(f"Errore Area: {e}")
            return df

    # =========================================================================
    # CLIP
    # =========================================================================
    
    def op_geo_clip(self, inputs: List[pd.DataFrame], node: Node) -> pd.DataFrame:
        """Ritaglia geometrie usando una maschera."""
        if len(inputs) < 2:
            return inputs[0] if inputs else pd.DataFrame()
        
        config = node.data.get('config', {}) if node.data else {}
        input_geom = config.get('input_geom')
        mask_geom = config.get('mask_geom')
        
        try:
            input_gdf = to_geodataframe(inputs[0], input_geom)
            mask_gdf = to_geodataframe(inputs[1], mask_geom)
            
            input_gdf, mask_gdf = ensure_same_crs(input_gdf, mask_gdf)
            
            result = gpd.clip(input_gdf, mask_gdf)
            
            if 'geometry' in result.columns:
                result['geometry'] = result['geometry'].apply(geometry_to_wkt)
            
            logger.info(f"Clip completato: {len(result)} righe")
            return pd.DataFrame(result)
            
        except Exception as e:
            logger.error(f"Errore Clip: {e}")
            return inputs[0] if inputs else pd.DataFrame()

    # =========================================================================
    # DISSOLVE
    # =========================================================================
    
    def op_geo_dissolve(self, df: pd.DataFrame, node: Node) -> pd.DataFrame:
        """Dissolve: unisce geometrie per attributo."""
        config = node.data.get('config', {}) if node.data else {}
        geom_column = config.get('geom_column')
        by_column = config.get('by_column')  # Colonna per raggruppamento
        aggfunc = config.get('aggfunc', 'first')  # Funzione aggregazione per altri campi
        
        try:
            gdf = to_geodataframe(df, geom_column)
            
            if by_column and by_column in gdf.columns:
                result = gdf.dissolve(by=by_column, aggfunc=aggfunc).reset_index()
            else:
                # Dissolve tutto in una singola geometria
                result = gdf.dissolve(aggfunc=aggfunc).reset_index()
            
            if 'geometry' in result.columns:
                result['geometry'] = result['geometry'].apply(geometry_to_wkt)
            
            logger.info(f"Dissolve completato: {len(result)} righe")
            return pd.DataFrame(result)
            
        except Exception as e:
            logger.error(f"Errore Dissolve: {e}")
            return df

    # =========================================================================
    # INTERSECTION
    # =========================================================================
    
    def op_geo_intersection(self, inputs: List[pd.DataFrame], node: Node) -> pd.DataFrame:
        """Intersezione geometrica tra due layer."""
        if len(inputs) < 2:
            return inputs[0] if inputs else pd.DataFrame()
        
        config = node.data.get('config', {}) if node.data else {}
        left_geom = config.get('left_geom')
        right_geom = config.get('right_geom')
        
        try:
            left_gdf = to_geodataframe(inputs[0], left_geom)
            right_gdf = to_geodataframe(inputs[1], right_geom)
            
            left_gdf, right_gdf = ensure_same_crs(left_gdf, right_gdf)
            
            result = gpd.overlay(left_gdf, right_gdf, how='intersection')
            
            if 'geometry' in result.columns:
                result['geometry'] = result['geometry'].apply(geometry_to_wkt)
            
            logger.info(f"Intersection completato: {len(result)} righe")
            return pd.DataFrame(result)
            
        except Exception as e:
            logger.error(f"Errore Intersection: {e}")
            return inputs[0] if inputs else pd.DataFrame()

    # =========================================================================
    # UNION (geometrica)
    # =========================================================================
    
    def op_geo_union(self, inputs: List[pd.DataFrame], node: Node) -> pd.DataFrame:
        """Unione geometrica di due layer."""
        if len(inputs) < 2:
            return inputs[0] if inputs else pd.DataFrame()
        
        config = node.data.get('config', {}) if node.data else {}
        left_geom = config.get('left_geom')
        right_geom = config.get('right_geom')
        
        try:
            left_gdf = to_geodataframe(inputs[0], left_geom)
            right_gdf = to_geodataframe(inputs[1], right_geom)
            
            left_gdf, right_gdf = ensure_same_crs(left_gdf, right_gdf)
            
            result = gpd.overlay(left_gdf, right_gdf, how='union')
            
            if 'geometry' in result.columns:
                result['geometry'] = result['geometry'].apply(geometry_to_wkt)
            
            logger.info(f"Union geometrica completata: {len(result)} righe")
            return pd.DataFrame(result)
            
        except Exception as e:
            logger.error(f"Errore Union geometrica: {e}")
            return inputs[0] if inputs else pd.DataFrame()

    # =========================================================================
    # DIFFERENCE
    # =========================================================================
    
    def op_geo_difference(self, inputs: List[pd.DataFrame], node: Node) -> pd.DataFrame:
        """Differenza geometrica (A - B)."""
        if len(inputs) < 2:
            return inputs[0] if inputs else pd.DataFrame()
        
        config = node.data.get('config', {}) if node.data else {}
        left_geom = config.get('left_geom')
        right_geom = config.get('right_geom')
        
        try:
            left_gdf = to_geodataframe(inputs[0], left_geom)
            right_gdf = to_geodataframe(inputs[1], right_geom)
            
            left_gdf, right_gdf = ensure_same_crs(left_gdf, right_gdf)
            
            result = gpd.overlay(left_gdf, right_gdf, how='difference')
            
            if 'geometry' in result.columns:
                result['geometry'] = result['geometry'].apply(geometry_to_wkt)
            
            logger.info(f"Difference completato: {len(result)} righe")
            return pd.DataFrame(result)
            
        except Exception as e:
            logger.error(f"Errore Difference: {e}")
            return inputs[0] if inputs else pd.DataFrame()

    # =========================================================================
    # SIMPLIFY
    # =========================================================================
    
    def op_geo_simplify(self, df: pd.DataFrame, node: Node) -> pd.DataFrame:
        """Semplifica geometrie (Douglas-Peucker)."""
        config = node.data.get('config', {}) if node.data else {}
        geom_column = config.get('geom_column')
        tolerance = config.get('tolerance', 0.001)  # Tolleranza in unità del CRS
        preserve_topology = config.get('preserve_topology', True)
        
        try:
            gdf = to_geodataframe(df, geom_column)
            
            simplified = gdf.geometry.simplify(tolerance, preserve_topology=preserve_topology)
            
            result = df.copy()
            # Sostituisci la geometria originale con quella semplificata
            geom_col_name = geom_column or 'geometry'
            result[geom_col_name] = simplified.apply(geometry_to_wkt)
            
            logger.info(f"Simplify completato: tolerance={tolerance}")
            return result
            
        except Exception as e:
            logger.error(f"Errore Simplify: {e}")
            return df

    # =========================================================================
    # CONVEX HULL
    # =========================================================================
    
    def op_geo_convex_hull(self, df: pd.DataFrame, node: Node) -> pd.DataFrame:
        """Calcola inviluppo convesso delle geometrie."""
        config = node.data.get('config', {}) if node.data else {}
        geom_column = config.get('geom_column')
        output_column = config.get('output_column', 'convex_hull')
        
        try:
            gdf = to_geodataframe(df, geom_column)
            
            convex_hulls = gdf.geometry.convex_hull
            
            result = df.copy()
            result[output_column] = convex_hulls.apply(geometry_to_wkt)
            
            logger.info(f"Convex Hull completato: colonna '{output_column}' aggiunta")
            return result
            
        except Exception as e:
            logger.error(f"Errore Convex Hull: {e}")
            return df

    # =========================================================================
    # REPROJECT
    # =========================================================================
    
    def op_geo_reproject(self, df: pd.DataFrame, node: Node) -> pd.DataFrame:
        """Riproietta geometrie in un nuovo CRS."""
        config = node.data.get('config', {}) if node.data else {}
        geom_column = config.get('geom_column')
        target_crs = config.get('target_crs', 'EPSG:4326')
        
        try:
            gdf = to_geodataframe(df, geom_column)
            
            if gdf.crs is None:
                # Se non ha CRS, assumiamo WGS84
                gdf = gdf.set_crs('EPSG:4326')
            
            reprojected = gdf.to_crs(target_crs)
            
            result = df.copy()
            geom_col_name = geom_column or 'geometry'
            result[geom_col_name] = reprojected.geometry.apply(geometry_to_wkt)
            
            logger.info(f"Reproject completato: {gdf.crs} -> {target_crs}")
            return result
            
        except Exception as e:
            logger.error(f"Errore Reproject: {e}")
            return df
