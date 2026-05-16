"""Dependency checks for datasource lifecycle operations."""

from __future__ import annotations

import json
from typing import Any, Callable, TypedDict


class ProjectCanvasRow(TypedDict, total=False):
    id: str
    name: str
    project_type: str
    group_id: str
    canvas: object


class ProjectDatasourceDependency(TypedDict):
    id: str
    name: str
    project_type: str
    group_id: str


ProjectCanvasRowsLoader = Callable[[], list[ProjectCanvasRow]]


def find_projects_using_datasource(
    ds_id: str,
    *,
    rows_loader: ProjectCanvasRowsLoader,
    logger: Any,
) -> list[ProjectDatasourceDependency]:
    """Return projects whose canvas references the datasource id.

    Raises RuntimeError when every project row fails validation, so callers can
    fail closed instead of deleting a datasource with unknown dependencies.
    """
    rows = rows_loader()
    using: list[ProjectDatasourceDependency] = []
    check_failures = 0
    for row in rows:
        row_id = row.get("id")
        try:
            canvas = row.get("canvas") or {}
            if isinstance(canvas, str):
                try:
                    canvas = json.loads(canvas)
                except Exception as json_err:
                    logger.warning(
                        f"_find_projects_using_datasource: canvas JSON "
                        f"malformato per progetto {row_id}: {json_err}"
                    )
                    check_failures += 1
                    continue
            nodes = canvas.get("nodes") or [] if isinstance(canvas, dict) else []
            for node in nodes:
                if not isinstance(node, dict):
                    continue
                top_datasource_id = node.get("datasource_id")
                nested = node.get("data") or {}
                nested_datasource_id = (
                    nested.get("datasource_id") if isinstance(nested, dict) else None
                )
                if top_datasource_id == ds_id or nested_datasource_id == ds_id:
                    using.append(
                        {
                            "id": str(row.get("id") or ""),
                            "name": str(row.get("name") or ""),
                            "project_type": str(row.get("project_type") or ""),
                            "group_id": str(row.get("group_id") or ""),
                        }
                    )
                    break
        except Exception as row_err:
            logger.warning(
                f"_find_projects_using_datasource: errore check canvas "
                f"progetto {row_id}: {row_err}"
            )
            check_failures += 1
            continue

    if rows and check_failures == len(rows):
        raise RuntimeError(
            f"Impossibile verificare dipendenze datasource: "
            f"{check_failures} progetto/i non checkabili"
        )
    return using
