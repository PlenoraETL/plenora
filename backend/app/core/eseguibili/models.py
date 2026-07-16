"""
Modelli Pydantic per il modulo ESEGUIBILI.
"""

from __future__ import annotations

from typing import Any, List, Literal, Optional
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.shared.contracts.payloads import JsonRows, ObjectMap, ObjectRows, StringMap


ProjectType = Literal["estrai", "manipola", "arricchisci"]
ExecutableInputKind = Literal[
    "file",
    "text",
    "integer",
    "number",
    "boolean",
    "date",
    "datetime",
    "select",
]
ExecutableBindingMode = Literal["data_source", "node_field"]


class ExecutableInputValidation(BaseModel):
    """Vincoli portabili tra form frontend e validazione backend."""

    model_config = ConfigDict(extra="forbid")

    min_length: Optional[int] = Field(None, ge=0)
    max_length: Optional[int] = Field(None, ge=1)
    minimum: Optional[float] = None
    maximum: Optional[float] = None
    pattern: Optional[str] = None
    options: list[str] = Field(default_factory=list)
    accepted_extensions: list[str] = Field(default_factory=list)
    max_size_bytes: Optional[int] = Field(None, ge=1)

    @model_validator(mode="after")
    def validate_ranges(self):
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
    order: int = Field(0, ge=0)

    @model_validator(mode="after")
    def validate_kind_contract(self):
        if self.multiple and self.kind != "file":
            raise ValueError("multiple è supportato solo per input file")
        if self.kind == "select" and not self.validation.options:
            raise ValueError("un input select deve dichiarare almeno una opzione")
        if self.kind != "file" and self.expected_columns:
            raise ValueError("expected_columns è valido solo per input file")
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
    can_edit: Optional[bool] = None  # calcolato per l'utente corrente


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
    can_edit: Optional[bool] = None  # calcolato per l'utente corrente
    # Flusso file-based (rest_file come ultimo nodo api_enrich
    # il wizard di esecuzione deve disabilitare il selettore di output_format
    # perche' l'estensione e' determinata dal connettore, non dall'utente
    output_is_file_based: bool = False
    inferred_output_extension: Optional[str] = None  # senza punto, es. "zip"


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
    inputs: dict[str, Any] = Field(default_factory=dict)
    output_format: Optional[str] = None
    output_name: Optional[str] = None


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
