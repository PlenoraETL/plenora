# Contributing

Internal contributor guide for Plenora. Keep changes small enough to
review, keep contracts explicit, and avoid adding compatibility layers
without a removal plan.

## Branches

- `main` is protected and should stay releasable.
- Use short-lived branches for normal work.
- Use semantic prefixes: `feat/`, `fix/`, `refactor/`, `test/`, `docs/`,
  `chore/`, `ops/`.
- Do not force-push shared release branches.

## Commits

Use concise Conventional Commit style when possible:

```text
<type>(<scope>): <subject>
```

Examples:

```text
refactor(resources): move artifact loading behind resolver
docs(architecture): refresh horizontal engine map
test(auth): lock facade strangler contract
```

The commit body should explain why the change exists when the diff is not
self-evident.

## Review Rules

Every change should answer these questions:

- Does it move the system toward the horizontal engine structure?
- Does it remove or reduce a legacy path instead of adding a new one?
- Does it preserve tenant and group isolation?
- Does it avoid large in-memory payloads and temp-file leaks?
- Does it keep frontend and backend contracts aligned?
- Does the documentation still describe the real system?

## Scope Rules

- Do not mix frontend and backend changes unless the work is explicitly
  full-stack.
- Do not add new business features during architecture cleanup batches.
- Do not introduce new global state or request context magic.
- Do not add broad helpers at `app.shared` root. Use a thematic package.
- Do not add new error codes unless an existing class cannot express the
  failure.
- Keep Python imports at module top by default. Local imports are allowed only
  for real circular imports, optional/heavy dependencies, or bootstrap/runtime
  boundaries, and should carry a short `NOTE:` when the reason is not obvious.

## Code Comments

Comments are part of the code contract. Keep them sparse, current, and useful.

- Explain why a choice exists, not what the code already says.
- Do not keep commented-out code. Delete it.
- Use only standard maintenance tags: `TODO`, `FIXME`, `NOTE`, `HACK`.
- Keep file-level context at the top of non-trivial modules: purpose and
  responsibility in the project.
- Golden rule: if the file purpose cannot be explained in two concise lines,
  the file is probably doing too much and should be split instead of documented
  harder.
- Use structured docstrings for public functions, API handlers, service
  methods, database helpers, and non-obvious private helpers.
- Do not add docstrings to trivial private wrappers when the signature and
  name are already sufficient.

Python docstrings should use this compact shape:

```python
def load_resource(ref: ResourceRef, ctx: ExecutionContext) -> LoadedResource:
    """
    Loads a tenant-bound resource through the registered loader.

    Args:
        ref: Resource identifier and expected storage kind.
        ctx: Execution context carrying tenant and project scope.

    Returns:
        Loaded resource metadata and a bounded data handle.

    Tags:
        @logic @resources
    """
```

Tags should describe the function's responsibility, for example `@api`, `@db`,
`@logic`, `@resources`, `@storage`, `@security`, `@utils`, `@ui`, or `@ops`.

## Tests

Prefer targeted tests while iterating. Use wider runs for auth, shared
runtime, installation, scheduler, and release-candidate changes.

Backend:

```bash
cd backend
pytest tests2/<domain>/<file>.py -v
pytest tests2/security/ tests2/orchestrator/ -v
```

Frontend:

```bash
cd frontend
npx vitest run tests/pages/<file>.test.jsx
npx vitest run tests/components/<file>.test.jsx
npm run build
```

Contract tests are useful when they enforce an architectural rule. Avoid
tests that only pin implementation noise.

## Documentation

Update docs in the same batch when behavior, installation, architecture, or
public contracts change.

Use:

- `README.md` for product and quickstart entry points.
- `docs/architecture/` for current system shape and ADRs.
- `docs/development/` for developer workflow.
- `docs/ops/` for installation, runtime, backup, and cleanroom notes.

ADRs are historical records. Add a new ADR or superseding note instead of
rewriting old decisions as if they never happened.
