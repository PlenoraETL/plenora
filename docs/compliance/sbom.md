# SBOM

Software Bill of Materials status for Plenora.

## Current Scope

Backend Python dependencies:

- `backend/requirements.txt`
- `backend/requirements-dev.txt`

Frontend npm SBOM is tracked separately and is not yet the canonical release
SBOM.

## Format

Preferred format: CycloneDX JSON.

Output target:

```text
docs/compliance/sbom.json
```

## Generate

```bash
cd <repo-root>
python backend/scripts/generate_sbom.py
```

The generator should be run:

- before external audit delivery;
- after dependency upgrades;
- before a 1.0 release candidate;
- when a relevant public CVE appears.

## Vulnerability Gate

Release gate:

- zero open exploitable CRITICAL findings;
- zero open exploitable HIGH findings;
- MEDIUM/LOW findings documented with applicability and owner.

When a finding appears, record:

- package;
- installed version;
- advisory ID;
- severity;
- whether the vulnerable path is reachable in Plenora;
- fix or accepted-risk rationale.

## Gaps

- Frontend SBOM automation is not yet canonical.
- Python dependency hash pinning is not complete.
- Container image scanning is still a separate release-readiness item.

## References

- [Secrets management](secrets-management.md)
- [ISO 27001 Statement of Applicability](soa-iso-27001.md)
- [Release 1.0 readiness](../product/release-1.0.md)
