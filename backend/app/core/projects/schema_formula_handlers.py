"""Formula and conditional transform schema handlers for schema inference."""

from __future__ import annotations

from typing import TYPE_CHECKING, List

if TYPE_CHECKING:
    from app.core.projects.schema_inference import ColumnSchema


class FormulaSchemaHandlers:
    """Mixin for expression-like schema implementations."""

    def _schema_formula(
        self,
        input_schemas: List[List[ColumnSchema]],
        config: dict,
        node_id: str,
    ) -> List[ColumnSchema]:
        """
        Formula: input + nuova colonna calcolata
        """
        if not input_schemas:
            return []

        result = [self._column_schema_cls(
            name=col.name,
            type=col.type,
            source_node=node_id,
            is_calculated=col.is_calculated,
        ) for col in input_schemas[0]]

        new_col = config.get("new_column", "calculated")
        output_type = config.get("output_type", "real")

        existing_names = {col.name for col in result}
        if new_col and new_col not in existing_names:
            result.append(self._column_schema_cls(
                name=new_col,
                type=output_type,
                source_node=node_id,
                is_calculated=True,
            ))

        return result

    def _schema_conditional(
        self,
        input_schemas: List[List[ColumnSchema]],
        config: dict,
        node_id: str,
    ) -> List[ColumnSchema]:
        """Conditional: input + nuova colonna output (str)"""
        if not input_schemas:
            return []

        output_col = config.get("output_column", "result")

        result = [self._column_schema_cls(
            name=col.name,
            type=col.type,
            source_node=node_id,
            is_calculated=col.is_calculated,
        ) for col in input_schemas[0]]

        existing_names = {col.name for col in result}
        if output_col not in existing_names:
            result.append(self._column_schema_cls(
                name=output_col,
                type="str",
                source_node=node_id,
                is_calculated=True,
            ))

        return result
