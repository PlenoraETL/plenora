"""Formula and conditional transform schema handlers for schema inference."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, List

if TYPE_CHECKING:
    from app.shared.execution.schema_types import ColumnSchema


def _stable_calculated_column_id(node_id: str, output_key: str) -> str:
    return f"col_{uuid.uuid5(uuid.NAMESPACE_URL, f'manipola:{node_id}:{output_key}').hex}"


def _stable_calculated_source_name(node_id: str, output_key: str) -> str:
    return f"calculated:{node_id}:{output_key}"


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

        if config.get("items") is not None:
            result = self._copy_schema(input_schemas[0], node_id)
            for index, item in enumerate(config["items"]):
                name = item["new_column"]
                column = self._column_schema_cls(
                    name=name, type=item.get("output_type", "real"),
                    id=_stable_calculated_column_id(node_id, f"formula:{index}"),
                    source_name=_stable_calculated_source_name(node_id, f"formula:{index}"),
                    source_node=node_id, is_calculated=True,
                )
                existing = next((i for i, col in enumerate(result) if col.name == name), None)
                if existing is None:
                    result.append(column)
                else:
                    result[existing] = column
            return result

        result = self._copy_schema(input_schemas[0], node_id)

        new_col = config.get("new_column", "calculated")
        output_type = config.get("output_type", "real")

        existing_names = {col.name for col in result}
        if new_col and new_col not in existing_names:
            result.append(self._column_schema_cls(
                name=new_col,
                type=output_type,
                id=_stable_calculated_column_id(node_id, "formula"),
                source_name=_stable_calculated_source_name(node_id, "formula"),
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

        result = self._copy_schema(input_schemas[0], node_id)

        existing_names = {col.name for col in result}
        if output_col not in existing_names:
            result.append(self._column_schema_cls(
                name=output_col,
                type="str",
                id=_stable_calculated_column_id(node_id, "conditional"),
                source_name=_stable_calculated_source_name(node_id, "conditional"),
                source_node=node_id,
                is_calculated=True,
            ))

        return result
