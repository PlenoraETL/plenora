"""Canvas access and reference validation helpers for projects."""

from __future__ import annotations

from typing import Any, Callable, Optional

from app.core.auth.visibility import visible_resource_ids
from app.core.projects.canvas_models import CanvasState
from app.core.projects.types import CanvasResourceRefs


OwnedProjectLookup = Callable[[set[str], str], set[str]]
VisibleProjectLookup = Callable[[Any, Optional[str], bool], set[str]]
VisibleResourceLookup = Callable[[str, Optional[str], bool], set[str]]


def visible_project_ids_strict(
    project_ids: Any,
    owner_group_id: Optional[str],
    is_superadmin: bool,
    *,
    list_owned_project_ids_fn: OwnedProjectLookup,
) -> set[str]:
    """Return visible project ids using ownership-only project visibility."""
    ids = {pid for pid in (project_ids or ()) if pid}
    if not ids:
        return set()
    if is_superadmin:
        return ids
    if not owner_group_id:
        return set()
    return list_owned_project_ids_fn(ids, owner_group_id)


def metadata_group_id(metadata: Any) -> Optional[str]:
    """Return the group id from typed project metadata."""
    return getattr(metadata, "group_id", None)


def annotate_inaccessible_canvas_refs(
    canvas_dict: dict[str, Any],
    owner_group_id: Optional[str],
    is_superadmin: bool,
    *,
    visible_resource_ids_fn: VisibleResourceLookup = visible_resource_ids,
    visible_project_ids_fn: VisibleProjectLookup,
) -> None:
    """Annotate serialized canvas nodes whose resources are not visible."""
    if is_superadmin or canvas_dict is None or not owner_group_id:
        return
    nodes = canvas_dict.get("nodes") or []
    if not nodes:
        return

    file_source_ids: set[str] = set()
    project_ids: set[str] = set()
    connection_ids: set[str] = set()
    datasource_ids: set[str] = set()
    arcgis_connection_ids: set[str] = set()
    for node in nodes:
        node_type = node.get("type")
        node_data = node.get("data") or {}
        if node_type == "file_source":
            file_source_id = node.get("file_source_id") or node_data.get("file_source_id")
            if file_source_id:
                file_source_ids.add(file_source_id)
        if node.get("connection_id") or node_data.get("connection_id"):
            connection_ids.add(str(node.get("connection_id") or node_data.get("connection_id")))
        if node.get("source_connection_id") or node_data.get("source_connection_id"):
            connection_ids.add(str(node.get("source_connection_id") or node_data.get("source_connection_id")))
        if node.get("datasource_id") or node_data.get("datasource_id"):
            datasource_ids.add(str(node.get("datasource_id") or node_data.get("datasource_id")))
        if node_type == "project_source":
            source_project_id = node.get("source_project_id") or node_data.get(
                "source_project_id"
            )
            if source_project_id:
                project_ids.add(source_project_id)
        elif node_type == "carica_arcgis":
            arcgis_connection_id = node.get("arcgis_connection_id") or node_data.get(
                "arcgis_connection_id"
            )
            if arcgis_connection_id:
                arcgis_connection_ids.add(arcgis_connection_id)

    if (
        not file_source_ids
        and not project_ids
        and not connection_ids
        and not datasource_ids
        and not arcgis_connection_ids
    ):
        return

    visible_file_sources = (
        visible_resource_ids_fn("file_sources", owner_group_id, False)
        if file_source_ids
        else set()
    )
    visible_projects = visible_project_ids_fn(
        project_ids,
        owner_group_id,
        False,
    )
    visible_connections = (
        visible_resource_ids_fn("connections", owner_group_id, False)
        if connection_ids
        else set()
    )
    visible_datasources = (
        visible_resource_ids_fn("datasources", owner_group_id, False)
        if datasource_ids
        else set()
    )
    visible_arcgis_connections = (
        visible_resource_ids_fn("arcgis_connections", owner_group_id, False)
        if arcgis_connection_ids
        else set()
    )

    annotate_denied_refs(
        nodes,
        visible_file_sources=visible_file_sources,
        visible_projects=visible_projects,
        visible_connections=visible_connections,
        visible_datasources=visible_datasources,
        visible_arcgis_connections=visible_arcgis_connections,
    )


def annotate_denied_refs(
    nodes: list[dict[str, Any]],
    *,
    visible_file_sources: set[str],
    visible_projects: set[str],
    visible_connections: set[str],
    visible_datasources: set[str],
    visible_arcgis_connections: set[str],
) -> None:
    """Mutate response nodes by marking references outside the visible sets."""
    for node in nodes:
        node_type = node.get("type")
        node_data = node.get("data")
        if node_data is None:
            node_data = {}
            node["data"] = node_data
        connection_id = (
            node.get("connection_id")
            or node_data.get("connection_id")
            or node.get("source_connection_id")
            or node_data.get("source_connection_id")
        )
        if connection_id and str(connection_id) not in visible_connections:
            node_data["access_denied"] = True
            node_data["access_denied_reason"] = (
                f"Connessione '{connection_id}' non visibile al gruppo del progetto"
            )
            node_data["inaccessible_resource_type"] = "connection"
        datasource_id = node.get("datasource_id") or node_data.get("datasource_id")
        if datasource_id and str(datasource_id) not in visible_datasources:
            node_data["access_denied"] = True
            node_data["access_denied_reason"] = (
                f"DataSource '{datasource_id}' non visibile al gruppo del progetto"
            )
            node_data["inaccessible_resource_type"] = "datasource"
        if node_type == "file_source":
            file_source_id = node.get("file_source_id") or node_data.get("file_source_id")
            if file_source_id and file_source_id not in visible_file_sources:
                node_data["access_denied"] = True
                node_data["access_denied_reason"] = (
                    f"File sorgente '{file_source_id}' non visibile al gruppo del progetto"
                )
                node_data["inaccessible_resource_type"] = "file_source"
        elif node_type == "project_source":
            source_project_id = node.get("source_project_id") or node_data.get(
                "source_project_id"
            )
            if source_project_id and source_project_id not in visible_projects:
                node_data["access_denied"] = True
                node_data["access_denied_reason"] = (
                    f"Progetto sorgente '{source_project_id}' non visibile al gruppo del progetto"
                )
                node_data["inaccessible_resource_type"] = "project_source"
        elif node_type == "carica_arcgis":
            arcgis_connection_id = node.get("arcgis_connection_id") or node_data.get(
                "arcgis_connection_id"
            )
            if arcgis_connection_id and arcgis_connection_id not in visible_arcgis_connections:
                node_data["access_denied"] = True
                node_data["access_denied_reason"] = (
                    f"Connessione ArcGIS '{arcgis_connection_id}' non visibile al gruppo del progetto"
                )
                node_data["inaccessible_resource_type"] = "arcgis_connection"


def collect_canvas_resource_refs(canvas: CanvasState) -> CanvasResourceRefs:
    """Extract cross-tenant resource ids referenced by a canvas."""
    file_source_ids: set[str] = set()
    project_ids: set[str] = set()
    connection_ids: set[str] = set()
    datasource_ids: set[str] = set()
    arcgis_connection_ids: set[str] = set()
    for node in canvas.nodes:
        node_data = getattr(node, "data", None)
        connection_id = getattr(node, "connection_id", None) or (
            node_data.get("connection_id") if node_data else None
        )
        if connection_id:
            connection_ids.add(str(connection_id))
        source_connection_id = (
            node_data.get("source_connection_id") if node_data else None
        )
        if source_connection_id:
            connection_ids.add(str(source_connection_id))
        datasource_id = getattr(node, "datasource_id", None) or (
            node_data.get("datasource_id") if node_data else None
        )
        if datasource_id:
            datasource_ids.add(str(datasource_id))
        if node.type == "file_source":
            file_source_id = getattr(node, "file_source_id", None) or (
                node_data.get("file_source_id") if node_data else None
            )
            if file_source_id:
                file_source_ids.add(file_source_id)
        elif node.type == "project_source":
            source_project_id = getattr(node, "source_project_id", None) or (
                node_data.get("source_project_id") if node_data else None
            )
            if source_project_id:
                project_ids.add(source_project_id)
        elif node.type == "carica_arcgis":
            arcgis_connection_id = getattr(node, "arcgis_connection_id", None) or (
                node_data.get("arcgis_connection_id") if node_data else None
            )
            if arcgis_connection_id:
                arcgis_connection_ids.add(arcgis_connection_id)
    return {
        "file_sources": file_source_ids,
        "projects": project_ids,
        "connections": connection_ids,
        "datasources": datasource_ids,
        "arcgis_connections": arcgis_connection_ids,
    }


def lookup_api_type(api_id: Optional[str]) -> Optional[str]:
    """Return the configured API type for an enrich API connection."""
    if not api_id:
        return None
    try:
        from app.core.arricchisci.api_connector import api_connection_service

        cfg = api_connection_service.get_api_connection(api_id, include_token=False)
        return (cfg or {}).get("api_type")
    except Exception:
        return None


def find_orphan_processing_nodes(
    canvas: CanvasState,
    *,
    api_type_lookup: Callable[[Optional[str]], Optional[str]] = lookup_api_type,
) -> list[str]:
    """Return node ids that require an incoming edge but do not have one."""
    source_like = {
        "source",
        "feature_source",
        "project_source",
        "file_source",
        "staticValues",
        "datasource_node",
        "comment",
    }
    target_ids = {edge.target for edge in canvas.edges}
    api_type_cache: dict[str, Optional[str]] = {}

    def is_source_like(node: Any) -> bool:
        if node.type in source_like:
            return True
        if node.type != "api_enrich":
            return False

        node_api_type = getattr(node, "api_type", None)
        if node_api_type == "generator":
            return True
        if node_api_type:
            return False

        api_id = getattr(node, "api_id", None)
        if not api_id:
            return False
        if api_id not in api_type_cache:
            api_type_cache[api_id] = api_type_lookup(api_id)
        return api_type_cache[api_id] == "generator"

    return [
        node.id
        for node in canvas.nodes
        if not is_source_like(node) and node.id not in target_ids
    ]


def find_inaccessible_canvas_refs(
    refs: dict[str, set[str]],
    *,
    owner_group_id: Optional[str],
    is_superadmin: bool,
    visible_resource_ids_fn: VisibleResourceLookup = visible_resource_ids,
    visible_project_ids_fn: VisibleProjectLookup,
) -> CanvasResourceRefs:
    """Return referenced resource ids that are not visible for the owner group."""
    invalid_file_sources: set[str] = set()
    invalid_projects: set[str] = set()
    invalid_connections: set[str] = set()
    invalid_datasources: set[str] = set()
    invalid_arcgis_connections: set[str] = set()

    if refs["file_sources"]:
        visible_file_sources = visible_resource_ids_fn(
            "file_sources",
            owner_group_id,
            is_superadmin,
        )
        invalid_file_sources = refs["file_sources"] - visible_file_sources
    if refs["projects"]:
        visible_projects = visible_project_ids_fn(
            refs["projects"],
            owner_group_id,
            is_superadmin,
        )
        invalid_projects = refs["projects"] - visible_projects
    if refs.get("connections"):
        visible_connections = visible_resource_ids_fn(
            "connections",
            owner_group_id,
            is_superadmin,
        )
        invalid_connections = refs["connections"] - visible_connections
    if refs.get("datasources"):
        visible_datasources = visible_resource_ids_fn(
            "datasources",
            owner_group_id,
            is_superadmin,
        )
        invalid_datasources = refs["datasources"] - visible_datasources
    if refs["arcgis_connections"]:
        visible_arcgis_connections = visible_resource_ids_fn(
            "arcgis_connections",
            owner_group_id,
            is_superadmin,
        )
        invalid_arcgis_connections = refs["arcgis_connections"] - visible_arcgis_connections

    return {
        "file_sources": invalid_file_sources,
        "projects": invalid_projects,
        "connections": invalid_connections,
        "datasources": invalid_datasources,
        "arcgis_connections": invalid_arcgis_connections,
    }


def format_inaccessible_canvas_refs(invalid_refs: dict[str, set[str]]) -> list[str]:
    """Format inaccessible refs for user-facing validation errors."""
    parts: list[str] = []
    if invalid_refs["file_sources"]:
        parts.append(f"file_source: {', '.join(sorted(invalid_refs['file_sources']))}")
    if invalid_refs["projects"]:
        parts.append(f"project_source: {', '.join(sorted(invalid_refs['projects']))}")
    if invalid_refs.get("connections"):
        parts.append(f"connection: {', '.join(sorted(invalid_refs['connections']))}")
    if invalid_refs.get("datasources"):
        parts.append(f"datasource: {', '.join(sorted(invalid_refs['datasources']))}")
    if invalid_refs["arcgis_connections"]:
        parts.append(
            "arcgis_connection: "
            f"{', '.join(sorted(invalid_refs['arcgis_connections']))}"
        )
    return parts
