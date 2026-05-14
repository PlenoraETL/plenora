"""Join and lookup transform schema handlers for schema inference."""

from __future__ import annotations

from typing import TYPE_CHECKING, List

if TYPE_CHECKING:
    from app.shared.execution.schema_types import ColumnSchema


class JoinSchemaHandlers:
    """Mixin for join, lookup, and table comparison schema implementations."""

    def _schema_join(
        self,
        input_schemas: List[List[ColumnSchema]],
        config: dict,
        node_id: str,
        edges_by_target: dict,
    ) -> List[ColumnSchema]:
        """
        Join: merge colonne sinistra + destra con suffissi _L e _R
        """
        if len(input_schemas) < 2:
            return input_schemas[0] if input_schemas else []

        left_schema = input_schemas[0]
        right_schema = input_schemas[1]

        left_on = config.get("left_on", "")
        right_on = config.get("right_on", "")

        result = []

        for col in left_schema:
            new_name = f"{col.name}_L" if col.name != left_on else col.name
            result.append(self._column_schema_cls(
                name=new_name,
                type=col.type,
                source_node=node_id,
                is_calculated=col.is_calculated,
            ))

        for col in right_schema:
            if col.name == right_on and left_on == right_on:
                continue
            new_name = col.name if col.name == right_on else f"{col.name}_R"
            result.append(self._column_schema_cls(
                name=new_name,
                type=col.type,
                source_node=node_id,
                is_calculated=col.is_calculated,
            ))

        return result

    def _schema_cross_join(
        self,
        input_schemas: List[List[ColumnSchema]],
        node_id: str,
    ) -> List[ColumnSchema]:
        """cross_join: cartesian product LEFT x RIGHT."""
        if len(input_schemas) < 2:
            return input_schemas[0] if input_schemas else []

        left_schema = input_schemas[0]
        right_schema = input_schemas[1]
        left_names = {c.name for c in left_schema}
        right_names = {c.name for c in right_schema}
        collisions = left_names & right_names

        result: List[ColumnSchema] = []
        for col in left_schema:
            new_name = f"{col.name}_x" if col.name in collisions else col.name
            result.append(self._column_schema_cls(
                name=new_name,
                type=col.type,
                source_node=node_id,
                is_calculated=col.is_calculated,
            ))
        for col in right_schema:
            new_name = f"{col.name}_y" if col.name in collisions else col.name
            result.append(self._column_schema_cls(
                name=new_name,
                type=col.type,
                source_node=node_id,
                is_calculated=col.is_calculated,
            ))
        return result

    def _schema_lookup(
        self,
        input_schemas: List[List[ColumnSchema]],
        config: dict,
        node_id: str,
    ) -> List[ColumnSchema]:
        """lookup: single-input value-mapping."""
        if not input_schemas:
            return []
        result = self._copy_schema(input_schemas[0], node_id)
        column = config.get("column") or ""
        mapping = config.get("mapping")
        if not column or not mapping:
            return result
        existing = {c.name for c in result}
        if column not in existing:
            return result

        out_col_cfg = config.get("output_column")
        target = out_col_cfg if out_col_cfg else column
        if target == column:
            return result
        if target in existing:
            return result

        src_col = next(c for c in result if c.name == column)
        result.append(self._column_schema_cls(
            name=target,
            type=src_col.type,
            source_node=node_id,
            is_calculated=True,
        ))
        return result

    def _schema_table_diff(
        self,
        input_schemas: List[List[ColumnSchema]],
        config: dict,
        node_id: str,
    ) -> List[ColumnSchema]:
        """table_diff: compare LEFT vs RIGHT with diff metadata columns."""
        if not input_schemas:
            return []
        if len(input_schemas) < 2:
            return self._copy_schema(input_schemas[0], node_id)

        left_schema = input_schemas[0]
        right_schema = input_schemas[1]

        left_keys = self._normalize_str_list(config.get("left_keys"))
        right_keys = self._normalize_str_list(config.get("right_keys"))

        if (
            not left_keys
            or not right_keys
            or len(left_keys) != len(right_keys)
        ):
            return self._copy_schema(left_schema, node_id)

        left_by_name = {c.name: c for c in left_schema}
        right_by_name = {c.name: c for c in right_schema}
        if not all(k in left_by_name for k in left_keys):
            return self._copy_schema(left_schema, node_id)
        if not all(k in right_by_name for k in right_keys):
            return self._copy_schema(left_schema, node_id)

        key_rename = {
            rk: lk for lk, rk in zip(left_keys, right_keys) if lk != rk
        }
        right_post: List[ColumnSchema] = []
        for col in right_schema:
            new_name = key_rename.get(col.name, col.name)
            right_post.append(self._column_schema_cls(
                name=new_name,
                type=col.type,
                source_node=col.source_node,
                is_calculated=col.is_calculated,
            ))
        right_post_by_name = {c.name: c for c in right_post}

        key_cols = list(left_keys)
        key_set = set(key_cols)
        left_non_key = [c.name for c in left_schema if c.name not in key_set]
        right_non_key = [c.name for c in right_post if c.name not in key_set]

        common_value_cols = [c for c in left_non_key if c in right_non_key]
        compare_cols = self._normalize_str_list(config.get("compare_columns"))
        if compare_cols:
            common_value_cols = [
                c for c in compare_cols if c in common_value_cols
            ]

        only_old_cols = [c for c in left_non_key if c not in right_non_key]
        only_new_cols = [c for c in right_non_key if c not in left_non_key]

        result: List[ColumnSchema] = []
        for k in key_cols:
            col = left_by_name[k]
            result.append(self._column_schema_cls(
                name=k,
                type=col.type,
                source_node=node_id,
                is_calculated=False,
            ))
        for c in common_value_cols:
            col = left_by_name[c]
            result.append(self._column_schema_cls(
                name=c,
                type=col.type,
                source_node=node_id,
                is_calculated=False,
            ))
        for c in only_old_cols:
            col = left_by_name[c]
            result.append(self._column_schema_cls(
                name=c,
                type=col.type,
                source_node=node_id,
                is_calculated=False,
            ))
        for c in only_new_cols:
            col = right_post_by_name[c]
            result.append(self._column_schema_cls(
                name=c,
                type=col.type,
                source_node=node_id,
                is_calculated=False,
            ))
        for fixed in ("_diff_status", "_diff_columns", "_diff_old_values"):
            result.append(self._column_schema_cls(
                name=fixed,
                type="str",
                source_node=node_id,
                is_calculated=True,
            ))
        return result
