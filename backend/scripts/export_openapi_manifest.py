"""Export a small frontend-consumable OpenAPI contract manifest."""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from pathlib import Path
from typing import Any


BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_ROOT.parent
DEFAULT_OUTPUT = REPO_ROOT / "frontend" / "src" / "api" / "openapi-contract.manifest.json"


def _schema_for(model: Any) -> dict[str, Any]:
    if hasattr(model, "model_json_schema"):
        return model.model_json_schema()
    return model.schema()


def _load_openapi_source(source: str) -> tuple[dict[str, Any], str]:
    if source.startswith(("http://", "https://")):
        with urllib.request.urlopen(source, timeout=15) as response:
            return json.loads(response.read().decode("utf-8")), source

    path = Path(source)
    if not path.is_absolute():
        path = REPO_ROOT / path
    return json.loads(path.read_text(encoding="utf-8")), str(path)


def _load_backend_openapi() -> tuple[dict[str, Any], str]:
    if str(BACKEND_ROOT) not in sys.path:
        sys.path.insert(0, str(BACKEND_ROOT))

    os.environ.setdefault("ENVIRONMENT", "development")
    os.environ.setdefault("STORAGE_POLICY", "db")
    os.environ.setdefault("DATABASE_URL", "postgresql://test:test@127.0.0.1:5432/test")

    import app.bootstrap as bootstrap

    bootstrap.bootstrap = lambda: None

    from app.main import app

    return app.openapi(), "backend_app_openapi"


def _load_auth_fragment() -> tuple[dict[str, Any], str]:
    if str(BACKEND_ROOT) not in sys.path:
        sys.path.insert(0, str(BACKEND_ROOT))

    from app.core.auth.models import LoginRequest, TokenResponse

    return (
        {
            "openapi": "3.0.0",
            "paths": {
                "/api/auth/login": {
                    "post": {
                        "operationId": "login_api_auth_login_post",
                        "requestBody": {
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/LoginRequest"}
                                }
                            },
                            "required": True,
                        },
                        "responses": {
                            "200": {
                                "content": {
                                    "application/json": {
                                        "schema": {"$ref": "#/components/schemas/TokenResponse"}
                                    }
                                }
                            }
                        },
                    }
                }
            },
            "components": {
                "schemas": {
                    "LoginRequest": _schema_for(LoginRequest),
                    "TokenResponse": _schema_for(TokenResponse),
                }
            },
        },
        "backend_auth_model_fragment",
    )


def load_openapi(source: str | None, strict: bool) -> tuple[dict[str, Any], str]:
    if source:
        return _load_openapi_source(source)
    try:
        return _load_backend_openapi()
    except Exception:
        if strict:
            raise
        return _load_auth_fragment()


def _ref_name(schema: dict[str, Any] | None) -> str | None:
    if not schema:
        return None
    ref = schema.get("$ref")
    if isinstance(ref, str):
        return ref.rsplit("/", 1)[-1]
    for key in ("allOf", "anyOf", "oneOf"):
        values = schema.get(key)
        if isinstance(values, list):
            for value in values:
                name = _ref_name(value if isinstance(value, dict) else None)
                if name:
                    return name
    return None


def _json_schema_from_content(content: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(content, dict):
        return None
    media = content.get("application/json") or next(iter(content.values()), None)
    if not isinstance(media, dict):
        return None
    schema = media.get("schema")
    return schema if isinstance(schema, dict) else None


def _field_type(schema: dict[str, Any]) -> str | list[str] | None:
    if "type" in schema:
        return schema["type"]
    for key in ("anyOf", "oneOf", "allOf"):
        values = schema.get(key)
        if isinstance(values, list):
            types = [
                value.get("type")
                for value in values
                if isinstance(value, dict) and value.get("type") != "null"
            ]
            if len(types) == 1:
                return types[0]
            if types:
                return types
    return None


def _allows_null(schema: dict[str, Any]) -> bool:
    if schema.get("nullable") is True:
        return True
    if schema.get("type") == "null":
        return True
    for key in ("anyOf", "oneOf", "allOf"):
        values = schema.get(key)
        if isinstance(values, list) and any(
            isinstance(value, dict) and value.get("type") == "null" for value in values
        ):
            return True
    return False


def _normalize_schema(schema: dict[str, Any]) -> dict[str, Any]:
    required = set(schema.get("required") or [])
    properties = schema.get("properties") or {}
    fields: dict[str, Any] = {}
    for name, field_schema in sorted(properties.items()):
        if not isinstance(field_schema, dict):
            continue
        fields[name] = {
            "required": name in required,
            "type": _field_type(field_schema),
            "nullable": _allows_null(field_schema),
        }
    return {"required": sorted(required), "fields": fields}


def build_manifest(openapi: dict[str, Any], source: str) -> dict[str, Any]:
    components = openapi.get("components", {}).get("schemas", {})
    endpoints: list[dict[str, Any]] = []

    for path, path_item in sorted((openapi.get("paths") or {}).items()):
        if not isinstance(path_item, dict):
            continue
        for method, operation in sorted(path_item.items()):
            if method.lower() not in {"get", "post", "put", "patch", "delete"}:
                continue
            if not isinstance(operation, dict):
                continue
            request_schema = _json_schema_from_content(
                (operation.get("requestBody") or {}).get("content")
            )
            response_schemas = []
            for response in (operation.get("responses") or {}).values():
                if not isinstance(response, dict):
                    continue
                name = _ref_name(_json_schema_from_content(response.get("content")))
                if name:
                    response_schemas.append(name)
            endpoints.append(
                {
                    "method": method.upper(),
                    "path": path,
                    "operationId": operation.get("operationId"),
                    "requestSchema": _ref_name(request_schema),
                    "responseSchemas": sorted(set(response_schemas)),
                }
            )

    login_request = components.get("LoginRequest")
    schemas = {}
    if isinstance(login_request, dict):
        schemas["LoginRequest"] = _normalize_schema(login_request)

    token_response = components.get("TokenResponse")
    if isinstance(token_response, dict):
        schemas["TokenResponse"] = _normalize_schema(token_response)

    return {
        "manifestVersion": 1,
        "source": source,
        "openapi": openapi.get("openapi"),
        "endpoints": endpoints,
        "schemas": schemas,
        "critical": {
            "auth.login": {
                "method": "POST",
                "path": "/api/auth/login",
                "requestSchema": "LoginRequest",
            }
        },
    }


def validate_manifest(manifest: dict[str, Any]) -> None:
    login_endpoint = next(
        (
            endpoint
            for endpoint in manifest["endpoints"]
            if endpoint["method"] == "POST" and endpoint["path"] == "/api/auth/login"
        ),
        None,
    )
    if not login_endpoint:
        raise SystemExit("OpenAPI contract drift: POST /api/auth/login is missing")
    if login_endpoint.get("requestSchema") != "LoginRequest":
        raise SystemExit("OpenAPI contract drift: /api/auth/login must use LoginRequest")

    fields = manifest.get("schemas", {}).get("LoginRequest", {}).get("fields", {})
    for required_name in ("username", "password"):
        if not fields.get(required_name, {}).get("required"):
            raise SystemExit(f"OpenAPI contract drift: LoginRequest.{required_name} is not required")
    if "mfa_code" not in fields:
        raise SystemExit("OpenAPI contract drift: LoginRequest.mfa_code is missing")
    if fields["mfa_code"].get("required"):
        raise SystemExit("OpenAPI contract drift: LoginRequest.mfa_code must remain optional")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", help="OpenAPI JSON URL or file. Defaults to backend app import.")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument(
        "--strict-openapi",
        action="store_true",
        help="Fail instead of falling back to the auth model fragment when app import fails.",
    )
    args = parser.parse_args()

    openapi, source = load_openapi(args.source, args.strict_openapi)
    manifest = build_manifest(openapi, source)
    validate_manifest(manifest)

    output = Path(args.output)
    if not output.is_absolute():
        output = REPO_ROOT / output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"Wrote {output.relative_to(REPO_ROOT)} from {source}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
