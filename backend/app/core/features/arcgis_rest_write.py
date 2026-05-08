"""Typed ArcGIS REST write endpoint contracts.

These contracts own the provider-specific FeatureServer write payload shapes
inside the features domain. Public callers may still receive legacy dict
results from ``ArcGISClient``; request construction and response parsing stay
explicit here.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from typing import Mapping, Optional, Sequence


class ArcGISFeatureEditOperation(str, Enum):
    """Layer-level FeatureServer write endpoints."""

    ADD = "addFeatures"
    UPDATE = "updateFeatures"

    @property
    def result_key(self) -> str:
        return "addResults" if self is ArcGISFeatureEditOperation.ADD else "updateResults"

    @property
    def count_key(self) -> str:
        return "added_count" if self is ArcGISFeatureEditOperation.ADD else "updated_count"


@dataclass(frozen=True)
class ArcGISCreateServiceRequest:
    """Request contract for portal ``createService``."""

    base_url: str
    username: str
    create_parameters: Mapping[str, object]
    folder_id: str = ""

    def endpoint(self) -> str:
        user_path = f"{self.base_url.rstrip('/')}/content/users/{self.username}"
        if self.folder_id:
            user_path = f"{user_path}/{self.folder_id}"
        return f"{user_path}/createService"

    def body(self) -> dict[str, object]:
        return {
            "createParameters": json.dumps(dict(self.create_parameters)),
            "outputType": "featureService",
            "f": "json",
        }


@dataclass(frozen=True)
class ArcGISAddToDefinitionRequest:
    """Request contract for FeatureServer ``addToDefinition``."""

    admin_url: str
    definition: Mapping[str, object]

    def endpoint(self) -> str:
        return f"{self.admin_url.rstrip('/')}/addToDefinition"

    def body(self) -> dict[str, object]:
        return {"addToDefinition": json.dumps(dict(self.definition)), "f": "json"}


@dataclass(frozen=True)
class ArcGISUpdateDefinitionRequest:
    """Request contract for FeatureServer ``updateDefinition``."""

    admin_url: str
    definition: Mapping[str, object]

    def endpoint(self) -> str:
        return f"{self.admin_url.rstrip('/')}/updateDefinition"

    def body(self) -> dict[str, object]:
        return {"updateDefinition": json.dumps(dict(self.definition)), "f": "json"}


@dataclass(frozen=True)
class ArcGISFeatureBatchWriteRequest:
    """Request contract for layer ``addFeatures`` and ``updateFeatures``."""

    layer_url: str
    operation: ArcGISFeatureEditOperation
    features: Sequence[Mapping[str, object]]

    def endpoint(self) -> str:
        return f"{self.layer_url.rstrip('/')}/{self.operation.value}"

    def body(self) -> dict[str, object]:
        return {"features": json.dumps(list(self.features)), "f": "json"}


@dataclass(frozen=True)
class ArcGISDeleteFeaturesRequest:
    """Request contract for layer ``deleteFeatures``."""

    layer_url: str
    object_ids: Sequence[int] = ()
    where: Optional[str] = None

    def endpoint(self) -> str:
        return f"{self.layer_url.rstrip('/')}/deleteFeatures"

    def body(self) -> dict[str, object]:
        data: dict[str, object] = {"f": "json"}
        if self.object_ids:
            data["objectIds"] = ",".join(str(oid) for oid in self.object_ids)
        elif self.where:
            data["where"] = self.where
        else:
            raise ValueError("Specificare object_ids o where per l'eliminazione")
        return data


@dataclass(frozen=True)
class ArcGISApplyEditsRequest:
    """Request contract for layer ``applyEdits``."""

    layer_url: str
    adds: Sequence[Mapping[str, object]] = ()
    updates: Sequence[Mapping[str, object]] = ()
    deletes: Sequence[int] = ()
    rollback_on_failure: bool = True
    use_global_ids: bool = False

    def endpoint(self) -> str:
        return f"{self.layer_url.rstrip('/')}/applyEdits"

    def body(self) -> dict[str, object]:
        if not self.adds and not self.updates and not self.deletes:
            raise ValueError("applyEdits richiede almeno un add, update o delete")

        data: dict[str, object] = {
            "f": "json",
            "rollbackOnFailure": _arcgis_bool(self.rollback_on_failure),
            "useGlobalIds": _arcgis_bool(self.use_global_ids),
        }
        if self.adds:
            data["adds"] = json.dumps(list(self.adds))
        if self.updates:
            data["updates"] = json.dumps(list(self.updates))
        if self.deletes:
            data["deletes"] = ",".join(str(oid) for oid in self.deletes)
        return data


@dataclass(frozen=True)
class ArcGISFeatureWriteResult:
    """Typed result for one add/update/delete endpoint family."""

    success: bool
    count_key: str
    count: int
    errors: tuple[Mapping[str, object], ...] = ()

    def to_compat_dict(self) -> dict[str, object]:
        return {
            "success": self.success,
            self.count_key: self.count,
            "errors": [dict(error) for error in self.errors],
        }


@dataclass(frozen=True)
class ArcGISApplyEditsResult:
    """Typed result for ``applyEdits``."""

    success: bool
    added_count: int = 0
    updated_count: int = 0
    deleted_count: int = 0
    errors: tuple[Mapping[str, object], ...] = ()

    def to_compat_dict(self) -> dict[str, object]:
        return {
            "success": self.success,
            "added_count": self.added_count,
            "updated_count": self.updated_count,
            "deleted_count": self.deleted_count,
            "errors": [dict(error) for error in self.errors],
        }


def parse_feature_edit_result(
    operation: ArcGISFeatureEditOperation,
    payload: Mapping[str, object],
) -> ArcGISFeatureWriteResult:
    if "error" in payload:
        return ArcGISFeatureWriteResult(
            success=False,
            count_key=operation.count_key,
            count=0,
            errors=(_mapping_error(payload["error"]),),
        )

    raw_results = payload.get(operation.result_key) or []
    results = [result for result in raw_results if isinstance(result, Mapping)]
    successes = sum(1 for result in results if result.get("success"))
    errors = tuple(dict(result) for result in results if not result.get("success"))
    return ArcGISFeatureWriteResult(
        success=bool(results) and not errors,
        count_key=operation.count_key,
        count=successes,
        errors=errors,
    )


def parse_delete_features_result(payload: Mapping[str, object]) -> ArcGISFeatureWriteResult:
    if "error" in payload:
        return ArcGISFeatureWriteResult(
            success=False,
            count_key="deleted_count",
            count=0,
            errors=(_mapping_error(payload["error"]),),
        )

    raw_results = payload.get("deleteResults") or []
    results = [result for result in raw_results if isinstance(result, Mapping)]
    successes = sum(1 for result in results if result.get("success"))
    errors = tuple(dict(result) for result in results if not result.get("success"))
    return ArcGISFeatureWriteResult(
        success=bool(results) and not errors,
        count_key="deleted_count",
        count=successes,
        errors=errors,
    )


def parse_apply_edits_result(payload: Mapping[str, object]) -> ArcGISApplyEditsResult:
    if "error" in payload:
        return ArcGISApplyEditsResult(
            success=False,
            errors=(_mapping_error(payload["error"]),),
        )

    add_results = _result_sequence(payload.get("addResults"))
    update_results = _result_sequence(payload.get("updateResults"))
    delete_results = _result_sequence(payload.get("deleteResults"))
    errors = tuple(
        dict(result)
        for result in (*add_results, *update_results, *delete_results)
        if not result.get("success")
    )
    return ArcGISApplyEditsResult(
        success=not errors,
        added_count=sum(1 for result in add_results if result.get("success")),
        updated_count=sum(1 for result in update_results if result.get("success")),
        deleted_count=sum(1 for result in delete_results if result.get("success")),
        errors=errors,
    )


def _arcgis_bool(value: bool) -> str:
    return "true" if value else "false"


def _mapping_error(value: object) -> Mapping[str, object]:
    if isinstance(value, Mapping):
        return dict(value)
    return {"message": str(value)}


def _result_sequence(value: object) -> tuple[Mapping[str, object], ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return ()
    return tuple(dict(item) for item in value if isinstance(item, Mapping))

