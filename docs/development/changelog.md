# Changelog

This file is a lightweight human index. Git history remains the source of
truth for exact changes.

## 2026-05-07

- Documentation was collapsed into a smaller canonical set.
- Live install docs were rewritten around executable acceptance gates.
- Release 1.0 readiness was promoted to a product document.
- Compliance docs were shortened into current working evidence.
- Historical audit material was explicitly marked as read-only.

## Current Development Themes

- Horizontal backend engine boundaries.
- Resource and artifact safety.
- Clean installation on a fresh Linux host.
- Docker validation, including the remaining frontend delivery gap.
- Documentation cleanup and removal of obsolete operational noise.

## Where To Look

- Product readiness: [`../product/release-1.0.md`](../product/release-1.0.md)
- Architecture: [`../architecture/overview.md`](../architecture/overview.md)
- Horizontal engines: [`../architecture/horizontal-engines.md`](../architecture/horizontal-engines.md)
- Install, non-Docker: [`../ops/install-non-docker.md`](../ops/install-non-docker.md)
- Install, Docker: [`../ops/install-docker.md`](../ops/install-docker.md)

For exact commit-by-commit history, use:

```bash
git log --oneline --decorate --graph
```
