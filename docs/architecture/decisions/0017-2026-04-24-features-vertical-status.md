# ADR-0017: Features Vertical Status

**Status**: Accepted

**Date**: 2026-04-24

## Context

`backend/app/core/features/` is an active ArcGIS Feature Service integration.
It supports connection management, service discovery, schema inspection,
preview/query behavior, feature writes, and service creation. The frontend uses
that surface through connection management and ArcGIS-oriented canvas nodes.

Some internal response models were unused while two route-local request models
remain part of the route contract.

## Decision

Keep the Features vertical as an active provider-facing module.

Remove unused internal response models when they have no callers and no API
contract value. Keep the live route-local request models until the route
contract is intentionally moved to shared models.

Features is not an IMDF subdomain. ArcGIS provider semantics should move
toward shared connection contracts where they are useful outside Features.

## Operational Contract

- Features owns ArcGIS Feature Service behavior that is specific to service
  discovery, layer access, feature writes, and service creation.
- Shared provider contracts belong under `app.shared.connections`.
- IMDF must consume ArcGIS through narrow provider abstractions, not through a
  broad dependency on `FeaturesService`.
- API response and request shape changes are breaking changes and require
  frontend/test updates.
- Secrets and tokens must be masked before returning provider data to the UI.

## Consequences

Positive:

- The active ArcGIS surface remains available to the frontend.
- Dead model classes can be removed without freezing the provider boundary.
- The architecture has a clear direction for extracting shared provider
  contracts.

Negative:

- `features_service.py` and `arcgis_client.py` remain large provider modules.
- Moving route-local request models later is still a separate refactor.

## References

- [ADR-0023](0023-2026-05-06-arcgis-external-provider-boundary.md)
- [Horizontal Engines](../horizontal-engines.md)
- `backend/app/core/features/`
- `frontend/src/api/features.js`
