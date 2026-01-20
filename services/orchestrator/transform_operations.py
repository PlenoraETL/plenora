"""
Transform Operations - Operazioni di trasformazione base per MANIPOLA.

Contiene le trasformazioni non-geospaziali:
- join, union, filter, select, sort, distinct
- rename, drop_columns, fill_na, replace, split_column
"""

import pandas as pd
from typing import List

from models import Node
from utils.logger import get_logger

logger = get_logger(__name__)


class TransformOperations:
    """Operazioni di trasformazione base per DataFrame."""
    
    # =========================================================================
    # JOIN
    # =========================================================================
    
    def op_join(self, inputs: List[pd.DataFrame], node: Node) -> pd.DataFrame:
        """Join tra due DataFrame."""
        if len(inputs) < 2:
            return inputs[0] if inputs else pd.DataFrame()
        
        config = node.data.get('config', {}) if node.data else {}
        left_on = config.get('left_on')
        right_on = config.get('right_on')
        how = config.get('how', 'inner')
        
        try:
            result = pd.merge(
                inputs[0], inputs[1],
                left_on=left_on, right_on=right_on,
                how=how, suffixes=('', '_right')
            )
            logger.info(f"Join completato: {len(result)} righe")
            return result
        except Exception as e:
            logger.error(f"Errore Join: {e}")
            return inputs[0]

    # =========================================================================
    # UNION
    # =========================================================================
    
    def op_union(self, inputs: List[pd.DataFrame], node: Node) -> pd.DataFrame:
        """Union di più DataFrame."""
        if not inputs:
            return pd.DataFrame()
        
        try:
            result = pd.concat(inputs, ignore_index=True)
            logger.info(f"Union completato: {len(result)} righe")
            return result
        except Exception as e:
            logger.error(f"Errore Union: {e}")
            return inputs[0] if inputs else pd.DataFrame()

    # =========================================================================
    # FILTER
    # =========================================================================
    
    def op_filter(self, df: pd.DataFrame, node: Node) -> pd.DataFrame:
        """Filtra righe in base a condizioni."""
        config = node.data.get('config', {}) if node.data else {}
        conditions = config.get('conditions', [])
        
        if not conditions:
            return df
        
        try:
            mask = pd.Series([True] * len(df))
            
            for cond in conditions:
                col = cond.get('column')
                op = cond.get('operator', '==')
                value = cond.get('value')
                
                if col not in df.columns:
                    continue
                
                if op == '==':
                    mask &= df[col] == value
                elif op == '!=':
                    mask &= df[col] != value
                elif op == '>':
                    mask &= df[col] > value
                elif op == '>=':
                    mask &= df[col] >= value
                elif op == '<':
                    mask &= df[col] < value
                elif op == '<=':
                    mask &= df[col] <= value
                elif op == 'contains':
                    mask &= df[col].astype(str).str.contains(str(value), na=False)
                elif op == 'is_null':
                    mask &= df[col].isna()
                elif op == 'is_not_null':
                    mask &= df[col].notna()
            
            result = df[mask].reset_index(drop=True)
            logger.info(f"Filter: {len(df)} -> {len(result)} righe")
            return result
            
        except Exception as e:
            logger.error(f"Errore Filter: {e}")
            return df

    # =========================================================================
    # SELECT
    # =========================================================================
    
    def op_select(self, df: pd.DataFrame, node: Node) -> pd.DataFrame:
        """Seleziona colonne specifiche."""
        config = node.data.get('config', {}) if node.data else {}
        columns = config.get('columns', [])
        
        if not columns:
            return df
        
        try:
            available = [c for c in columns if c in df.columns]
            if available:
                return df[available]
            return df
        except Exception as e:
            logger.error(f"Errore Select: {e}")
            return df

    # =========================================================================
    # SORT
    # =========================================================================
    
    def op_sort(self, df: pd.DataFrame, node: Node) -> pd.DataFrame:
        """Ordina DataFrame."""
        config = node.data.get('config', {}) if node.data else {}
        sort_by = config.get('sort_by', [])
        
        if not sort_by:
            return df
        
        try:
            columns = []
            ascending = []
            
            for s in sort_by:
                col = s.get('column')
                if col in df.columns:
                    columns.append(col)
                    ascending.append(s.get('ascending', True))
            
            if columns:
                return df.sort_values(by=columns, ascending=ascending).reset_index(drop=True)
            return df
        except Exception as e:
            logger.error(f"Errore Sort: {e}")
            return df

    # =========================================================================
    # DISTINCT
    # =========================================================================
    
    def op_distinct(self, df: pd.DataFrame, node: Node) -> pd.DataFrame:
        """Rimuove duplicati."""
        config = node.data.get('config', {}) if node.data else {}
        subset = config.get('subset')
        keep = config.get('keep', 'first')
        
        try:
            if subset:
                subset = [c for c in subset if c in df.columns] or None
            if keep == 'false':
                keep = False
            return df.drop_duplicates(subset=subset, keep=keep).reset_index(drop=True)
        except Exception as e:
            logger.error(f"Errore distinct: {e}")
            return df

    # =========================================================================
    # RENAME
    # =========================================================================
    
    def op_rename(self, df: pd.DataFrame, node: Node) -> pd.DataFrame:
        """Rinomina colonne."""
        config = node.data.get('config', {}) if node.data else {}
        
        # Supporta sia rename_map (dict) che old_name/new_name (singola rinomina)
        rename_map = config.get('rename_map', {})
        
        # Se non c'è rename_map, usa old_name/new_name
        if not rename_map:
            old_name = config.get('old_name')
            new_name = config.get('new_name')
            if old_name and new_name:
                rename_map = {old_name: new_name}
        
        if not rename_map:
            return df
        
        try:
            # Filtra solo colonne esistenti
            valid_renames = {k: v for k, v in rename_map.items() if k in df.columns}
            
            if valid_renames:
                result = df.rename(columns=valid_renames)
                logger.info(f"Rename completato: {list(valid_renames.keys())} -> {list(valid_renames.values())}")
                return result
            
            return df
            
        except Exception as e:
            logger.error(f"Errore Rename: {e}")
            return df

    # =========================================================================
    # DROP COLUMNS
    # =========================================================================
    
    def op_drop_columns(self, df: pd.DataFrame, node: Node) -> pd.DataFrame:
        """Elimina colonne."""
        config = node.data.get('config', {}) if node.data else {}
        columns_to_drop = config.get('columns', [])
        
        if not columns_to_drop:
            return df
        
        try:
            # Filtra solo colonne esistenti
            existing_cols = [c for c in columns_to_drop if c in df.columns]
            
            if existing_cols:
                result = df.drop(columns=existing_cols)
                logger.info(f"Drop columns completato: {existing_cols}")
                return result
            
            return df
            
        except Exception as e:
            logger.error(f"Errore Drop Columns: {e}")
            return df

    # =========================================================================
    # FILL NA
    # =========================================================================
    
    def op_fill_na(self, df: pd.DataFrame, node: Node) -> pd.DataFrame:
        """Riempi valori nulli."""
        config = node.data.get('config', {}) if node.data else {}
        column = config.get('column')  # Se None, applica a tutte
        fill_value = config.get('value', '')
        method = config.get('method')  # 'ffill', 'bfill', o None per valore fisso
        
        try:
            result = df.copy()
            
            if method in ['ffill', 'bfill']:
                if column and column in result.columns:
                    result[column] = result[column].fillna(method=method)
                else:
                    result = result.fillna(method=method)
            else:
                if column and column in result.columns:
                    result[column] = result[column].fillna(fill_value)
                else:
                    result = result.fillna(fill_value)
            
            logger.info(f"Fill NA completato: column={column}, method={method}, value={fill_value}")
            return result
            
        except Exception as e:
            logger.error(f"Errore Fill NA: {e}")
            return df

    # =========================================================================
    # REPLACE
    # =========================================================================
    
    def op_replace(self, df: pd.DataFrame, node: Node) -> pd.DataFrame:
        """Sostituisci valori."""
        config = node.data.get('config', {}) if node.data else {}
        column = config.get('column')
        old_value = config.get('old_value')
        new_value = config.get('new_value')
        use_regex = config.get('regex', False)
        
        if column is None or old_value is None:
            return df
        
        try:
            result = df.copy()
            
            if column in result.columns:
                if use_regex:
                    result[column] = result[column].astype(str).str.replace(
                        str(old_value), str(new_value), regex=True
                    )
                else:
                    result[column] = result[column].replace(old_value, new_value)
                
                logger.info(f"Replace completato: {column} '{old_value}' -> '{new_value}'")
            
            return result
            
        except Exception as e:
            logger.error(f"Errore Replace: {e}")
            return df

    # =========================================================================
    # SPLIT COLUMN
    # =========================================================================
    
    def op_split_column(self, df: pd.DataFrame, node: Node) -> pd.DataFrame:
        """Dividi una colonna in più colonne."""
        config = node.data.get('config', {}) if node.data else {}
        column = config.get('column')
        delimiter = config.get('delimiter', ',')
        new_columns = config.get('new_columns', [])  # Lista nomi nuove colonne
        max_splits = config.get('max_splits', -1)  # -1 = nessun limite
        
        if not column or column not in df.columns:
            return df
        
        try:
            result = df.copy()
            
            # Esegui split
            n_splits = max_splits if max_splits > 0 else None
            split_result = result[column].astype(str).str.split(delimiter, n=n_splits, expand=True)
            
            # Assegna nomi colonne
            if new_columns:
                for i, new_col in enumerate(new_columns):
                    if i < split_result.shape[1]:
                        result[new_col] = split_result[i]
            else:
                # Genera nomi automatici
                for i in range(split_result.shape[1]):
                    result[f"{column}_part{i+1}"] = split_result[i]
            
            logger.info(f"Split column completato: {column} -> {split_result.shape[1]} parti")
            return result
            
        except Exception as e:
            logger.error(f"Errore Split Column: {e}")
            return df
