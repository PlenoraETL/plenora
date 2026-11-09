"""
Modelli Pydantic per il modulo ESEGUIBILI.
"""

from __future__ import annotations

import re
from typing import Any, List, Literal, Optional
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.shared.contracts.payloads import JsonRows, ObjectMap, ObjectRows, StringMap
from app.shared.execution.runtime_input_kinds import RuntimeInputKind, is_server_injected


ProjectType = Literal["estrai", "manipola", "arricchisci"]
# Tabella unica dei tipi: app/shared/execution/runtime_input_kinds.py.
ExecutableInputKind = RuntimeInputKind
ExecutableBindingMode = Literal["data_source", "node_field"]


class ExecutableInputValidation(BaseModel):
    """Vincoli portabili tra form frontend e validazione backend."""

    model_config = ConfigDict(extra="forbid")

    min_length: Optional[int] = Field(None, ge=0)
    max_length: Optional[int] = Field(None, ge=1)
    minimum: Optional[float] = None
    maximum: Optional[float] = None
    pattern: Optional[str] = Field(None, max_length=240)
    options: list[str] = Field(default_factory=list)
    accepted_extensions: list[str] = Field(default_factory=list)
    max_size_bytes: Optional[int] = Field(None, ge=1)

    @model_validator(mode="after")
    def validate_ranges(self):
        if self.pattern:
            try:
                re.compile(self.pattern)
            except re.error as exc:
                raise ValueError("pattern non valido") from exc
        if (
            self.min_length is not None
            and self.max_length is not None
            and self.min_length > self.max_length
        ):
            raise ValueError("min_length non può superare max_length")
        if (
            self.minimum is not None
            and self.maximum is not None
            and self.minimum > self.maximum
        ):
            raise ValueError("minimum non può superare maximum")
        return self


class ConnectionInputOption(BaseModel):
    """Etichetta pubblica di un membro del Connection Set."""

    model_config = ConfigDict(extra="forbid")

    key: str
    label: str


class ExecutableInputDefinition(BaseModel):
    """Un input pubblico dichiarato da un nodo ``project_input``."""

    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1, max_length=80, pattern=r"^[a-z][a-z0-9_]*$")
    label: str = Field(min_length=1, max_length=160)
    kind: ExecutableInputKind
    description: str = ""
    required: bool = True
    default_value: Any = None
    multiple: bool = False
    expected_columns: list[ExpectedColumn] = Field(default_factory=list)
    validation: ExecutableInputValidation = Field(
        default_factory=ExecutableInputValidation
    )
    connection_set_id: Optional[str] = Field(None, max_length=200)
    connection_options: list[ConnectionInputOption] = Field(default_factory=list)
    # «Utente che esegue»: trasformazione del nome utente (espressione della
    # Formula, variabile ``utente``), valutata dal server a ogni esecuzione
    # (shared/execution/runtime_identity, sezione TRASFORMAZIONE).
    transform: Optional[str] = Field(None, max_length=500)
    # «Utente che esegue»: gli amministratori (admin, superadmin: ruolo letto
    # dal server) possono scegliere il valore nel modulo; etichetta del campo
    # (runtime_identity, sezione VALORE SCELTO DAGLI AMMINISTRATORI).
    admin_override: bool = False
    admin_override_label: Optional[str] = Field(None, max_length=160)
    order: int = Field(0, ge=0)

    @model_validator(mode="after")
    def validate_kind_contract(self):
        if self.multiple and self.kind != "file":
            raise ValueError("multiple è supportato solo per input file")
        if self.kind == "select" and not self.validation.options:
            raise ValueError("un input select deve dichiarare almeno una opzione")
        if self.kind == "connection" and not self.connection_set_id:
            raise ValueError(
                "un input connection deve dichiarare connection_set_id"
            )
        if self.kind != "connection" and (
            self.connection_set_id or self.connection_options
        ):
            raise ValueError(
                "connection_set_id/options sono validi solo per input connection"
            )
        if self.kind != "file" and self.expected_columns:
            raise ValueError("expected_columns è valido solo per input file")
        if is_server_injected(self.kind) and (
            self.default_value is not None
            or not self.required
            or self.validation != ExecutableInputValidation()
        ):
            # «Utente che esegue»: il valore lo mette solo il server, sempre.
            raise ValueError(
                "un input impostato dal server non ha valore predefinito, "
                "vincoli o facoltativita'"
            )
        if self.transform is not None and not is_server_injected(self.kind):
            raise ValueError("transform e' valido solo per un input impostato dal server")
        if (self.admin_override or self.admin_override_label is not None) and not is_server_injected(self.kind):
            raise ValueError("admin_override e' valido solo per un input impostato dal server")
        return self


class ExecutableInputBinding(BaseModel):
    """Collega un input a una sorgente dati o a un campo configurabile."""

    model_config = ConfigDict(extra="forbid")

    input_key: str = Field(min_length=1, max_length=80)
    target_node_id: str = Field(min_length=1, max_length=200)
    mode: ExecutableBindingMode
    target_field: Optional[str] = Field(None, max_length=240)

    @model_validator(mode="after")
    def validate_target(self):
        if self.mode == "node_field" and not self.target_field:
            raise ValueError("target_field è obbligatorio per i binding node_field")
        if self.mode == "data_source" and self.target_field:
            raise ValueError("target_field non è ammesso per i binding data_source")
        return self


class ExecutableManifest(BaseModel):
    """Contratto immutabile prodotto dalla pubblicazione del canvas."""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = Field(2, ge=1)
    project_type: ProjectType
    source_project_id: str
    inputs: list[ExecutableInputDefinition] = Field(default_factory=list)
    bindings: list[ExecutableInputBinding] = Field(default_factory=list)
    default_connection_id: Optional[str] = None

    @model_validator(mode="after")
    def validate_references(self):
        keys = [item.key for item in self.inputs]
        if len(keys) != len(set(keys)):
            raise ValueError("Le chiavi degli input devono essere univoche")
        known = set(keys)
        missing = sorted({b.input_key for b in self.bindings} - known)
        if missing:
            raise ValueError(
                "Binding riferiti a input inesistenti: " + ", ".join(missing)
            )
        return self


class ExpectedColumn(BaseModel):
    """Una colonna che la catena si aspetta nel file di input."""
    name: str                              # nome colonna atteso (es. "cod_comune"
    type: Optional[str] = "str"            # tipo inferito (str, int, float, ecc
    required: bool = True                  # obbligatoria per l'esecuzione?
    used_by: Optional[str] = None          # id del nodo che la utilizza (debug/info
    description: Optional[str] = None      # opzionale: descrizione per UI


class EseguibileCreate(BaseModel):
    """Payload per pubblicare un progetto come eseguibile."""
    source_project_id: str
    name: str                              # nome pubblico dell'eseguibile
    description: Optional[str] = ""
    default_output_format: str = "xlsx"    # xlsx, csv, json, geojson, ecc
    default_output_name: Optional[str] = None  # se None → "{name}_{timestamp}"
    icon: Optional[str] = None             # emoji/carattere mostrato sulla card (fallback: ▶


class EseguibileUpdate(BaseModel):
    """Payload per aggiornare metadata (non il canvas)."""
    name: Optional[str] = None
    description: Optional[str] = None
    default_output_format: Optional[str] = None
    default_output_name: Optional[str] = None
    icon: Optional[str] = None


class BindingWarning(BaseModel):
    """Binding dell'Eseguibile pubblicato su un bersaglio che l'esecuzione
    non legge (regola unica ``manifest.check_binding``): l'input parte ma il
    suo valore e' ignorato. Si corregge ripubblicando."""

    input_key: str
    target_node_id: str
    target_field: Optional[str] = None
    motivo: str


class InputChange(BaseModel):
    """Un input della versione attiva che la ripubblicazione toglierebbe o
    cambierebbe (regola unica ``service.input_changes``)."""

    input_key: str
    change: Literal["removed", "kind_changed", "binding_lost", "target_changed", "admin_override_removed"]
    target_node_id: Optional[str] = None
    target_field: Optional[str] = None
    motivo: str
    # ``False``: solo informazione (es. ``admin_override_removed``), la
    # ripubblicazione non chiede conferma per questa riga.
    blocking: bool = True


class EseguibileSummary(BaseModel):
    """Scheda compatta per la lista."""
    id: str
    name: str
    description: Optional[str] = ""
    default_output_format: str
    icon: Optional[str] = None
    group_id: str
    created_by: str
    created_at: str
    updated_at: str
    expected_columns_count: int = 0
    project_type: ProjectType = "arricchisci"
    active_version: int = 1
    inputs_count: int = 1
    # Si esegue con il modulo dinamico (la versione attiva ha un manifest):
    # la stessa regola della finestra di esecuzione (UI conferma U14).
    has_manifest: bool = False
    can_edit: Optional[bool] = None  # calcolato per l'utente corrente
    binding_warnings: List[BindingWarning] = Field(default_factory=list)
    # N1: progetto sorgente cambiato dopo la pubblicazione (None: non si sa,
    # senza progetto sorgente o senza versione attiva).
    source_changed: Optional[bool] = None


class EseguibileSourceStatus(BaseModel):
    """N1: la versione attiva rispetto al canvas attuale del progetto
    sorgente (regola unica ``service.source_status``: stessa impronta?).
    Con ``changed`` c'e' l'anteprima della ripubblicazione."""

    eseguibile_id: str
    name: str
    version: Optional[int] = None
    published_at: Optional[str] = None
    state: Literal["same", "changed", "no_source", "no_version"]
    changed: Optional[bool] = None
    binding_problems: List[BindingWarning] = Field(default_factory=list)
    # Ripubblicazione sicura (09/11/2026): input della versione attiva che la
    # nuova versione toglierebbe o cambierebbe (``service.input_changes``);
    # con almeno uno la ripubblicazione chiede conferma esplicita.
    input_changes: List[InputChange] = Field(default_factory=list)
    publish_error: Optional[str] = None


class RepublishRequest(BaseModel):
    """POST /eseguibili/{id}/versions. ``confirm_removed_inputs``: chi
    ripubblica ha visto e accetta gli input che la nuova versione toglie o
    cambia (``EseguibileSourceStatus.input_changes``); senza, la
    ripubblicazione che ne ha si rifiuta (409)."""

    model_config = ConfigDict(extra="forbid")

    confirm_removed_inputs: bool = False


class EseguibileDetails(BaseModel):
    """Dettaglio completo (usato dal wizard)."""
    id: str
    name: str
    description: Optional[str] = ""
    expected_columns: List[ExpectedColumn]
    default_output_format: str
    default_output_name: Optional[str] = None
    icon: Optional[str] = None
    source_canvas_id: Optional[str] = None
    group_id: str
    created_by: str
    created_at: str
    updated_at: str
    project_type: ProjectType = "arricchisci"
    active_version: int = 1
    active_version_id: Optional[str] = None
    manifest: Optional[ExecutableManifest] = None
    # Stessa regola della lista (db.manifest_present, UI conferma finale G5).
    has_manifest: bool = False
    can_edit: Optional[bool] = None  # calcolato per l'utente corrente
    binding_warnings: List[BindingWarning] = Field(default_factory=list)
    # Flusso file-based (rest_file come ultimo nodo api_enrich
    # il wizard di esecuzione deve disabilitare il selettore di output_format
    # perche' l'estensione e' determinata dal connettore, non dall'utente
    output_is_file_based: bool = False
    inferred_output_extension: Optional[str] = None  # senza punto, es. "zip"


class EseguibileVersionSummary(BaseModel):
    """Revisione pubblicata selezionabile come versione attiva."""

    id: str
    version_number: int
    schema_version: int
    project_type: ProjectType
    source_project_id: Optional[str] = None
    source_hash: str
    status: Literal["draft", "published", "retired"]
    created_by: str
    created_at: str
    inputs_count: int = 0
    active: bool = False


class ColumnMappingEntry(BaseModel):
    """Una riga del mapping: colonna catena → colonna file utente."""
    chain_column: str                      # nome atteso dalla catena
    file_column: str                       # nome presente nel file caricato


class ExecuteRequest(BaseModel):
    """Payload per eseguire un eseguibile con un file utente."""
    file_id: str                           # id del FileSource temp creato al momento dell'upload
    column_mapping: List[ColumnMappingEntry]
    output_format: Optional[str] = None    # override del default
    output_name: Optional[str] = None      # override del default


class ExecutableRunFileInput(BaseModel):
    """Riferimento a un upload già autorizzato e analizzato."""

    model_config = ConfigDict(extra="forbid")

    file_id: str
    column_mapping: list[ColumnMappingEntry] = Field(default_factory=list)


class ExecutableRunRequest(BaseModel):
    """Payload v2: valori indicizzati dalla chiave stabile del manifest."""

    model_config = ConfigDict(extra="forbid")

    version_id: Optional[str] = None
    inputs: ObjectMap = Field(default_factory=dict)
    output_format: Optional[str] = None
    output_name: Optional[str] = None
    # Impronta della stima confermata (POST /preflight): se la preparazione e'
    # cambiata l'avvio risponde 409 preparation_changed (Fase 5 T2, BA2).
    preparation_fingerprint: Optional[str] = None


class ExecutablePreflightRequest(BaseModel):
    """Stima prima di avviare: gli stessi input dell'avvio, senza eseguire."""

    model_config = ConfigDict(extra="forbid")

    version_id: Optional[str] = None
    inputs: ObjectMap = Field(default_factory=dict)


class ExecutableRunResult(BaseModel):
    """Risultato del submit v2, indipendente dal numero di input file."""

    model_config = ConfigDict(extra="forbid")

    async_: bool = Field(True, alias="async")
    task_id: str
    run_id: str
    eseguibile_id: str
    version_id: str
    message: str = "Esecuzione avviata"

    def to_api_dict(self) -> ObjectMap:
        return self.model_dump(by_alias=True)


class AnalyzeUploadResponse(BaseModel):
    """Risposta all'upload di un file per analisi."""
    file_id: str                           # id del FileSource temp
    detected_columns: JsonRows # {name, type, sample_values}
    row_count: int
    auto_mapping: StringMap = Field(default_factory=dict)      # {chain_col: file_col} — suggerimento euristico
    preview_rows: JsonRows = Field(default_factory=list)  # prime 10 righe


class DetectedUploadColumn(BaseModel):
    """Colonna rilevata durante l'analisi upload interna."""

    model_config = ConfigDict(extra="forbid")

    name: str
    type: str
    sample_values: list[str] = Field(default_factory=list)


class AnalyzeFileUploadResult(BaseModel):
    """Payload interno del service; il router decide cosa esporre."""

    model_config = ConfigDict(extra="forbid")

    file_id: str
    file_path: str
    filename: str
    detected_columns: list[DetectedUploadColumn]
    row_count: int
    auto_mapping: StringMap = Field(default_factory=dict)
    preview_rows: ObjectRows = Field(default_factory=list)

    def to_api_dict(self) -> ObjectMap:
        return {
            "file_id": self.file_id,
            "detected_columns": [
                column.model_dump() for column in self.detected_columns
            ],
            "row_count": self.row_count,
            "auto_mapping": self.auto_mapping,
            "preview_rows": self.preview_rows,
        }


class EseguibileExecutionResult(BaseModel):
    """Payload interno per il lancio task di un eseguibile."""

    model_config = ConfigDict(extra="forbid")

    async_: bool = Field(alias="async")
    task_id: str
    project_id: str
    temp_file_source_id: str
    message: str

    def to_api_dict(self) -> ObjectMap:
        return self.model_dump(by_alias=True)
