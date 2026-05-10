"""GeoJSON endpoints.

Serves the bundled cities FeatureCollection plus several edge-case variants
(empty, null geometry, mixed geometry types, invalid feature) so the GeoJSON
ingest path can be exercised end-to-end.
"""

from __future__ import annotations

import copy

from fastapi import APIRouter, HTTPException, Path
from fastapi.responses import JSONResponse

from . import load_fixture

router = APIRouter()


def _cities_geojson() -> dict:
    return copy.deepcopy(load_fixture("cities.geojson"))


@router.get("/cities")
def cities() -> JSONResponse:
    return JSONResponse(content=_cities_geojson())


@router.get("/cities/by_region/{region_id}")
def cities_by_region(region_id: int = Path(..., ge=1)) -> JSONResponse:
    fc = _cities_geojson()
    fc["features"] = [
        f for f in fc["features"] if f["properties"].get("region_id") == region_id
    ]
    if not fc["features"]:
        # Region exists in seed only for 1..3; anything else is unknown.
        if region_id not in (1, 2, 3):
            raise HTTPException(status_code=404, detail="region_not_found")
    return JSONResponse(content=fc)


@router.get("/empty")
def empty() -> JSONResponse:
    return JSONResponse(content={"type": "FeatureCollection", "features": []})


@router.get("/null_geometry")
def null_geometry() -> JSONResponse:
    return JSONResponse(
        content={
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "geometry": None,
                    "properties": {"id": 1, "name": "Ghost", "note": "no geometry"},
                }
            ],
        }
    )


@router.get("/mixed")
def mixed() -> JSONResponse:
    return JSONResponse(
        content={
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "geometry": {
                        "type": "Point",
                        "coordinates": [9.1900, 45.4642],
                    },
                    "properties": {"id": 1, "name": "Milano"},
                },
                {
                    "type": "Feature",
                    "geometry": {
                        "type": "LineString",
                        "coordinates": [
                            [9.1900, 45.4642],
                            [12.4964, 41.9028],
                        ],
                    },
                    "properties": {"id": 2, "name": "Milano-Roma"},
                },
                {
                    "type": "Feature",
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [
                            [
                                [9.0, 45.0],
                                [10.0, 45.0],
                                [10.0, 46.0],
                                [9.0, 46.0],
                                [9.0, 45.0],
                            ]
                        ],
                    },
                    "properties": {"id": 3, "name": "BoxLombardia"},
                },
            ],
        }
    )


@router.get("/invalid")
def invalid() -> JSONResponse:
    # Feature lacks the mandatory 'geometry' field.
    return JSONResponse(
        content={
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {"id": 1, "name": "MissingGeometry"},
                }
            ],
        }
    )
