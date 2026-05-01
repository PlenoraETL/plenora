# ADR-0007: `typing.Protocol` + `runtime_checkable` per backend storage, anziche' `abc.ABC`

**Status**: Accepted
**Date**: 2026-04-21
**Deciders**: @CloudIA

## Context

URBY-ETL astrae due tipi di backend infrastructure:

1. **Secrets backend**: oggi AWS SSM, domani potenzialmente Azure Key Vault o GCP Secret Manager.
2. **Object storage**: oggi AWS S3, domani Azure Blob Storage o Google Cloud Storage.

L'app core non deve conoscere il provider. Deve dipendere da un **contract** (firma dei metodi) e ottenere l'impl concreta via factory `get_secrets_backend()` / `get_object_storage()`. Centralizzazione in `app/shared/cloud_backends.py`.

Domanda di design: come esprimere il contract in Python?

Due strade canoniche:

- `abc.ABC` + `@abstractmethod` — la classica eredita-e-implementa. L'impl deve `class S3Storage(ObjectStorage): ...` e implementare ogni `@abstractmethod` altrimenti `TypeError` al costruttore.
- `typing.Protocol` — structural typing. L'impl NON deve ereditare; basta avere i metodi con la firma giusta. Il type checker (mypy/pyright) verifica staticamente; `runtime_checkable` permette `isinstance(obj, Protocol)` a runtime.

## Decision

**`typing.Protocol` + `@runtime_checkable`** per entrambi i contract.

Codice: `app/shared/cloud_backends.py:46-58` (`SecretsBackend`), `:62-105` (`ObjectStorage`).

Esempio:

```python
@runtime_checkable
class ObjectStorage(Protocol):
    def list_objects(self, prefix: str) -> List[ObjectMeta]: ...
    def upload(self, local_path: Path, key: str) -> None: ...
    def upload_bytes(self, data: bytes, key: str) -> None: ...
    def download(self, key: str, local_path: Path) -> None: ...
    def get_object_stream(self, key: str) -> dict: ...
    def delete(self, key: str) -> None: ...
```

Le impl concrete (`AwsS3ObjectStorage`, future `AzureBlobObjectStorage`) sono classi normali senza ereditarieta'. Il duck-typing static e' enforced da mypy in CI.

## Rationale

- **Python-idiomatic**. Il linguaggio supporta structural typing nativamente da 3.8. Usarlo segnala "questa e' una facade, non una gerarchia". Gli sviluppatori Python senior lo riconoscono immediatamente.
- **Nessun boilerplate `class X(ABC)`**. Le impl concrete sono classi normali — piu' leggibili, piu' facili da testare isolatamente (non serve mock di ABC).
- **Compatibilita' con codice esterno**. Se domani un cliente passa un wrapper custom (es. un oggetto minio `Minio` adaptato in un thin wrapper), puo' soddisfare il Protocol senza dover ereditare da nostra classe. Riduce coupling.
- **`runtime_checkable` per controlli difensivi**. Il factory (`get_object_storage()`) puo' fare `assert isinstance(instance, ObjectStorage)` come tripwire per detectare config errate che passano un oggetto non-conforme. Con ABC si otterrebbe lo stesso, ma con accoppiamento eredita-o-muori.
- **Static checking forte**. Mypy con `--strict` verifica che le impl abbiano firme coerenti. Il type checker e' il vero enforcer, non il costruttore. Il vantaggio: detect al PR, non al runtime.
- **Evita il trap "ABC con default method"**. Con `abc.ABC` e' tentante mettere default impl in metodi non-abstract, creando gerarchie con stato. Con `Protocol` c'e' meno invito a questo anti-pattern (il Protocol e' puramente contract, senza stato).

## Alternatives considered

- **`abc.ABC` con `@abstractmethod`** — **Rejected**. Scelta canonica Python "OOP-stile Java", ma con tre svantaggi: (1) ogni impl deve ereditare, (2) test mock richiedono MagicMock(spec=ObjectStorage) con la classe vera, (3) classi gia' esistenti (es. di librerie terze) non possono essere adattate senza wrapper. Nessuno di questi e' bloccante, ma sommati preferiscono Protocol.
- **Duck-typing puro senza Protocol** — **Rejected**. Funziona ma perde static checking. Un refactor che rinomina `upload_bytes` → `put_bytes` in un'impl rompe l'altra silenziosamente. Protocol + mypy cattura al PR.
- **Generic Protocol con TypeVar per provider-specific extensions** — **Rejected** per overengineering. Le impl attuali (`AwsSsmSecretsBackend`, `AwsS3ObjectStorage`) non hanno proprieta' provider-specific esposte al caller. Quando e se nascera' il caso (es. tag S3 su upload), si aggiunge un Protocol separato `ObjectStorageWithTags` con ereditarieta' strutturale multipla. Cross-that-bridge-when.
- **Adapter class (pattern Gang of Four)** — **Rejected per Fase 0/1**. Aggiunge uno strato in piu' senza benefit tangibile; le impl concrete sono gia' "adapter" di boto3. Doppio strato = complessita' per niente.

## Consequences

### Positive
- Un nuovo provider = una classe nuova + un ramo in factory `get_object_storage()`. Zero modifiche al contract.
- Test unit delle impl concrete: isolate, senza mock complicati. Test del factory: `isinstance(get_object_storage(), ObjectStorage)` basta come smoke.
- Type-safety garantita staticamente — ogni call-site che usa `ObjectStorage` e' verificato da mypy.
- Il contract e' documentato dal docstring di ogni metodo del Protocol (vedi `cloud_backends.py:46-105`) — serve da spec scrittura per i nuovi provider.

### Negative
- `runtime_checkable` ha un costo: `isinstance(obj, Protocol)` non e' zero (itera gli attributi dell'oggetto per verificare la presenza di metodi). Trascurabile per check one-off al boot, non va messo in hot-path.
- Mypy deve essere attivo in CI. Senza type checker, il Protocol e' "solo documentazione". Il repo gia' usa mypy ma non e' strict su tutto — follow-up gia' tracciato.
- Developer abituati a Java/C# cercano `extends` e non lo trovano. Documentazione minima nel docstring del Protocol basta.

### Neutral
- Se in futuro servira' *ereditare state condiviso* tra impl (cache, retry wrapper), si potra' introdurre un `BaseObjectStorage` concrete + Protocol, con le impl che ereditano dal base e soddisfano il Protocol. Composizione > ereditarieta' stretta.

## References

- Source: `backend/app/shared/cloud_backends.py:29` — import `Protocol, runtime_checkable`
- Source: `backend/app/shared/cloud_backends.py:46-58` — `SecretsBackend` Protocol
- Source: `backend/app/shared/cloud_backends.py:62-105` — `ObjectStorage` Protocol
- Source: `backend/app/shared/cloud_backends.py:112+` — `AwsSsmSecretsBackend`, `AwsS3ObjectStorage` (impl concrete senza ereditarieta')
- Related: ADR-0011 (FileSourcesDispatch threshold — consumer di questo contract)
- Invariants: `docs/architecture/invariants.md` §storage — "un solo adapter per provider, nessun boto3 fuori da `cloud_backends`"
- Python docs: PEP 544 — Protocols, structural subtyping
