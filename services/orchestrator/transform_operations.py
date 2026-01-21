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
        """
        Join tra due DataFrame.
        Aggiunge suffissi _L e _R a TUTTE le colonne (tranne la colonna di join)
        per evitare ambiguità.
        """
        if len(inputs) < 2:
            return inputs[0] if inputs else pd.DataFrame()
        
        config = node.data.get('config', {}) if node.data else {}
        left_on = config.get('left_on')
        right_on = config.get('right_on')
        how = config.get('how', 'inner')
        
        try:
            left_df = inputs[0].copy()
            right_df = inputs[1].copy()
            
            # Rinomina TUTTE le colonne con suffissi _L e _R (tranne la colonna di join)
            left_rename = {}
            for col in left_df.columns:
                if col != left_on:  # Non rinominare la colonna di join
                    left_rename[col] = f"{col}_L"
            
            right_rename = {}
            for col in right_df.columns:
                if col != right_on:  # Non rinominare la colonna di join
                    right_rename[col] = f"{col}_R"
            
            left_df = left_df.rename(columns=left_rename)
            right_df = right_df.rename(columns=right_rename)
            
            # Aggiorna i nomi delle colonne di join dopo il rename
            left_join_col = left_on  # Resta uguale, non l'abbiamo rinominata
            right_join_col = right_on  # Resta uguale, non l'abbiamo rinominata
            
            # Esegui il merge
            result = pd.merge(
                left_df, right_df,
                left_on=left_join_col, 
                right_on=right_join_col,
                how=how,
                suffixes=('', '_dup')  # Fallback per eventuali duplicati residui
            )
            
            # Se left_on != right_on, rimuovi la colonna di join duplicata dal right
            # (pandas la tiene entrambe se hanno nomi diversi)
            if left_on != right_on and right_on in result.columns:
                result = result.drop(columns=[right_on])
            
            logger.info(f"Join completato: {len(result)} righe, {len(result.columns)} colonne")
            return result
            
        except Exception as e:
            logger.error(f"Errore Join: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return inputs[0]

    # =========================================================================
    # FORMULA
    # =========================================================================
    
    def op_formula(self, df: pd.DataFrame, node: Node) -> pd.DataFrame:
        """
        Crea una nuova colonna calcolata con un'espressione.
        
        Supporta:
        - Operazioni matematiche: col * 2, col1 + col2
        - Concatenazione stringhe: col1 + '_' + col2
        - Funzioni stringa: col.str.upper(), col.str.len()
        """
        config = node.data.get('config', {}) if node.data else {}
        formula = config.get('formula', '').strip()
        new_column = config.get('new_column', 'calculated').strip()
        
        if not formula:
            logger.warning("Formula: nessuna espressione specificata")
            return df
        
        if not new_column:
            new_column = 'calculated'
        
        try:
            result = df.copy()
            
            # Rileva se è una concatenazione di stringhe (contiene '+' e stringhe letterali come '_')
            if "'" in formula or '"' in formula:
                # Concatenazione stringhe - sostituisci nomi colonne con df['col'].astype(str)
                eval_formula = formula
                for col in result.columns:
                    # Sostituisci nome colonna con accesso DataFrame
                    if col in eval_formula:
                        eval_formula = eval_formula.replace(col, f"result['{col}'].astype(str)")
                
                result[new_column] = eval(eval_formula)
                logger.info(f"Formula (concat): {new_column} = {formula}")
                return result
            
            # Prova prima con eval di pandas (per operazioni matematiche)
            try:
                result[new_column] = result.eval(formula)
                logger.info(f"Formula completata: {new_column} = {formula}")
                return result
            except Exception as e:
                logger.debug(f"Formula eval pandas fallito: {e}")
            
            # Fallback: sostituisci nomi colonne e usa eval Python
            eval_formula = formula
            for col in sorted(result.columns, key=len, reverse=True):  # Ordina per lunghezza decrescente
                if col in eval_formula:
                    eval_formula = eval_formula.replace(col, f"result['{col}']")
            
            result[new_column] = eval(eval_formula)
            logger.info(f"Formula (eval): {new_column} = {formula}")
            return result
            
        except Exception as e:
            logger.error(f"Errore Formula '{formula}': {e}")
            return df

    # =========================================================================
    # AGGREGATE (Group By)
    # =========================================================================
    
    def op_aggregate(self, df: pd.DataFrame, node: Node) -> pd.DataFrame:
        """
        Raggruppa dati e applica funzioni di aggregazione.
        
        Config:
        - group_by: lista colonne per raggruppamento
        - aggregations: lista di {column, function} dove function è count, sum, avg, min, max
        """
        config = node.data.get('config', {}) if node.data else {}
        group_by = config.get('group_by', [])
        aggregations = config.get('aggregations', [])
        
        if not group_by:
            logger.warning("Aggregate: nessuna colonna group_by specificata")
            return df
        
        # Filtra solo colonne esistenti
        group_by = [c for c in group_by if c in df.columns]
        if not group_by:
            logger.warning("Aggregate: nessuna colonna group_by valida")
            return df
        
        try:
            # Se non ci sono aggregazioni specificate, fai solo count
            if not aggregations:
                result = df.groupby(group_by).size().reset_index(name='count')
                logger.info(f"Aggregate (count): {len(df)} -> {len(result)} righe")
                return result
            
            # Costruisci dizionario aggregazioni per pandas
            agg_dict = {}
            for agg in aggregations:
                col = agg.get('column')
                func = agg.get('function', 'count').lower()
                
                if col not in df.columns:
                    continue
                
                # Mappa funzioni
                func_map = {
                    'count': 'count',
                    'sum': 'sum',
                    'avg': 'mean',
                    'mean': 'mean',
                    'min': 'min',
                    'max': 'max',
                    'first': 'first',
                    'last': 'last'
                }
                
                pandas_func = func_map.get(func, 'count')
                
                # Gestisci multiple aggregazioni sulla stessa colonna
                if col in agg_dict:
                    if isinstance(agg_dict[col], list):
                        agg_dict[col].append(pandas_func)
                    else:
                        agg_dict[col] = [agg_dict[col], pandas_func]
                else:
                    agg_dict[col] = pandas_func
            
            if not agg_dict:
                # Fallback a count se nessuna aggregazione valida
                result = df.groupby(group_by).size().reset_index(name='count')
            else:
                # Usa named aggregations per avere nomi colonne descrittivi
                named_aggs = {}
                for col, func in agg_dict.items():
                    if isinstance(func, list):
                        # Multiple funzioni sulla stessa colonna
                        for f in func:
                            named_aggs[f"{col}_{f}"] = (col, f)
                    else:
                        named_aggs[f"{col}_{func}"] = (col, func)
                
                result = df.groupby(group_by).agg(**named_aggs).reset_index()
            
            logger.info(f"Aggregate completato: {len(df)} -> {len(result)} righe, group_by={group_by}")
            return result
            
        except Exception as e:
            logger.error(f"Errore Aggregate: {e}")
            return df

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
        """
        Filtra righe in base a condizioni.
        
        Supporta due formati:
        1. config.condition: stringa pandas query (es: "colonna > 100 & colonna2 == 'valore'")
        2. config.conditions: array di oggetti [{column, operator, value}] (legacy)
        """
        config = node.data.get('config', {}) if node.data else {}
        
        # Formato 1: stringa pandas query (dal nuovo frontend)
        condition_str = config.get('condition', '').strip()
        if condition_str:
            try:
                # Usa pandas query() per filtrare
                result = df.query(condition_str).reset_index(drop=True)
                logger.info(f"Filter (query): {len(df)} -> {len(result)} righe | Condizione: {condition_str}")
                return result
            except Exception as e:
                logger.error(f"Errore Filter query '{condition_str}': {e}")
                # Se la query fallisce, prova con eval
                try:
                    mask = df.eval(condition_str)
                    result = df[mask].reset_index(drop=True)
                    logger.info(f"Filter (eval): {len(df)} -> {len(result)} righe | Condizione: {condition_str}")
                    return result
                except Exception as e2:
                    logger.error(f"Errore Filter eval '{condition_str}': {e2}")
                    return df
        
        # Formato 2: array di condizioni (legacy)
        conditions = config.get('conditions', [])
        
        if not conditions:
            logger.warning("Filter: nessuna condizione specificata")
            return df
        
        try:
            mask = pd.Series([True] * len(df))
            
            for cond in conditions:
                col = cond.get('column')
                op = cond.get('operator', '==')
                value = cond.get('value')
                
                if col not in df.columns:
                    continue
                
                if op == '==' or op == '=':
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
                elif op == 'contains' or op == 'like':
                    mask &= df[col].astype(str).str.contains(str(value), na=False)
                elif op == 'is_null':
                    mask &= df[col].isna()
                elif op == 'is_not_null':
                    mask &= df[col].notna()
            
            result = df[mask].reset_index(drop=True)
            logger.info(f"Filter (conditions): {len(df)} -> {len(result)} righe")
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
        # Supporta sia sort_by che sort_columns
        sort_by = config.get('sort_by', []) or config.get('sort_columns', [])
        
        logger.info(f"Sort config: {config}")
        
        if not sort_by:
            # Prova formato alternativo: column + ascending direttamente nel config
            col = config.get('column') or config.get('sort_column')
            if col and col in df.columns:
                asc = config.get('ascending', True)
                if isinstance(asc, str):
                    asc = asc.lower() not in ['desc', 'false']
                result = df.sort_values(by=col, ascending=asc).reset_index(drop=True)
                logger.info(f"Sort completato: {col} {'ASC' if asc else 'DESC'}")
                return result
            logger.warning("Sort: nessuna colonna specificata")
            return df
        
        try:
            columns = []
            ascending = []
            
            for s in sort_by:
                col = s.get('column')
                if col in df.columns:
                    columns.append(col)
                    asc = s.get('ascending', True)
                    if isinstance(asc, str):
                        asc = asc.lower() not in ['desc', 'false']
                    ascending.append(asc)
            
            if columns:
                result = df.sort_values(by=columns, ascending=ascending).reset_index(drop=True)
                logger.info(f"Sort completato: {columns} {ascending}")
                return result
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