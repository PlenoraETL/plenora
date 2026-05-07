"""
Modelli dati per il modulo ARRICCHISCI.

Architettura
============
Questo modulo definisce tutte le strutture dati (dataclass) utilizzate dal
sistema di arricchimento: status per riga, errori, statistiche aggregate,
opzioni di esecuzione e risultati. Tutte le classi sono immutabili o
quasi-immutabili per facilitare il debugging e la serializzazione JSON.

Enumerazioni:
- RowStatus: stato elaborazione per riga (SUCCESS, ERROR, SKIPPED, RETRY_EXHAUSTED, PENDING)
- BatchInputFormat: formato payload batch (ARRAY, FLAT_ARRAY, OBJECT)

Dataclass per singola riga:
- RowError: errore con dettagli (indice riga, tipo errore, HTTP status, retry count)

Dataclass aggregate:
- EnrichmentStats: contatori globali (success, errors, cached, skipped, api_calls_made/saved)
- EnrichmentResult: risultato finale con stats + errori + timestamp
- PreviewResult: risultato preview con stime tempo e campione dati
- EnrichmentValidationResult: risultato validazione input (colonne mancanti, righe nulle)

Configurazione:
- EnrichmentOptions: opzioni esecuzione (retry, rate_limit, timeout, parallelismo,
  deduplicazione, soglia errori, validazione input)
- BatchConfig: configurazione batch nativo (endpoint, formato, dimensione max)

Le proprietà calcolate (success_rate, cache_hit_rate) sono definite come @property
per evitare inconsistenze quando le stats vengono aggiornate incrementalmente.
"""

from enum import Enum
from typing import Dict, List, Any, Optional
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone


# ENUMERAZIONI

class RowStatus(str, Enum):
    """Stato elaborazione singola riga."""
    SUCCESS = "success"
    ERROR = "error"
    SKIPPED = "skipped"           # Input non valido / nullo
    CACHED = "cached"             # Risultato da cache (deduplicazione)
    RETRY_EXHAUSTED = "retry_exhausted"  # Max retry raggiunto


class BatchInputFormat(str, Enum):
    """Formato input per batch API native."""
    ARRAY = "array"               # [{"q": "addr1"}, {"q": "addr2"}]
    FLAT_ARRAY = "flat_array"     # ["addr1", "addr2"] (singolo parametro)
    OBJECT = "object"             # {"items": [{"q": "addr1"}, ...]}


# CONFIGURAZIONE BATCH

@dataclass
class BatchConfig:
    """
    Configurazione per API che supportano batch nativo.
    
    Esempio config JSON:
    "batch": {
        "enabled": true,
        "max_size": 100,
        "input_key": "addresses",
        "input_format": "array",
        "output_path": "results"
    }
    """
    enabled: bool = False
    max_size: int = 100                              # Max righe per singola chiamata batch
    input_key: str = "items"                         # Chiave array nella request
    input_format: BatchInputFormat = BatchInputFormat.ARRAY
    output_path: str = ""                            # Path all'array di risultati (vuoto = root)
    endpoint_override: Optional[str] = None          # Endpoint diverso per batch
    method_override: Optional[str] = None            # Metodo diverso (es. POST invece di GET)
    
    @classmethod
    def from_dict(cls, data: Dict) -> "BatchConfig":
        """Crea BatchConfig da dizionario (es. da JSON template)."""
        if not data:
            return cls()
        
        input_format = data.get("input_format", "array")
        if isinstance(input_format, str):
            input_format = BatchInputFormat(input_format)
        
        return cls(
            enabled=data.get("enabled", False),
            max_size=data.get("max_size", 100),
            input_key=data.get("input_key", "items"),
            input_format=input_format,
            output_path=data.get("output_path", ""),
            endpoint_override=data.get("endpoint_override"),
            method_override=data.get("method_override")
        )
    
    def to_dict(self) -> Dict:
        return {
            "enabled": self.enabled,
            "max_size": self.max_size,
            "input_key": self.input_key,
            "input_format": self.input_format.value,
            "output_path": self.output_path,
            "endpoint_override": self.endpoint_override,
            "method_override": self.method_override
        }


# MODELLI PER SINGOLA RIGA

@dataclass
class RowError:
    """Dettaglio errore per una riga."""
    row_index: int
    status: RowStatus
    error_message: str
    error_type: str = ""          # es. "timeout", "http_429", "validation"
    input_params: Dict = field(default_factory=dict)
    http_status: Optional[int] = None
    retry_count: int = 0
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    
    def to_dict(self) -> Dict:
        result = asdict(self)
        result["status"] = self.status.value
        return result


# STATISTICHE E RISULTATI AGGREGATI

@dataclass
class EnrichmentStats:
    """Statistiche esecuzione enrichment."""
    total_rows: int = 0
    success: int = 0
    errors: int = 0
    skipped: int = 0
    cached: int = 0               # Righe servite da cache
    retry_exhausted: int = 0
    
    # Performance
    api_calls_made: int = 0       # Chiamate effettive (dopo deduplicazione)
    api_calls_saved: int = 0      # Chiamate risparmiate da cache
    avg_response_time_ms: float = 0
    total_time_ms: float = 0
    
    # Rate limiting
    rate_limit_hits: int = 0      # Volte che abbiamo dovuto attendere
    
    @property
    def success_rate(self) -> float:
        """Percentuale successo."""
        if self.total_rows == 0:
            return 0.0
        return (self.success + self.cached) / self.total_rows * 100
    
    @property
    def cache_hit_rate(self) -> float:
        """Percentuale hit cache."""
        total_lookups = self.api_calls_made + self.cached
        if total_lookups == 0:
            return 0.0
        return self.cached / total_lookups * 100
    
    def to_dict(self) -> Dict:
        result = asdict(self)
        result["success_rate"] = round(self.success_rate, 2)
        result["cache_hit_rate"] = round(self.cache_hit_rate, 2)
        return result


# OPZIONI DI ESECUZIONE

@dataclass
class EnrichmentOptions:
    """Opzioni configurabili per l'esecuzione."""
    
    # Gestione errori
    continue_on_error: bool = True        # Continua anche se righe falliscono
    stop_on_error_threshold: int = 0      # Ferma se errori > N (0 = mai)
    save_partial_results: bool = True     # Salva anche se non completato
    generate_error_report: bool = True    # Genera CSV con errori
    
    # Deduplicazione
    enable_deduplication: bool = True     # Usa cache per valori duplicati
    cache_ttl_seconds: int = 3600         # TTL cache (1 ora default)
    
    # Preview
    preview_mode: bool = False            # Se True, esegue solo su campione
    preview_sample_size: int = 10         # Righe per preview
    
    # Retry
    max_retries: int = 3
    retry_backoff_factor: float = 2.0     # Exponential backoff
    retry_on_status: List[int] = field(default_factory=lambda: [429, 500, 502, 503, 504])
    
    # Rate limiting
    rate_limit: float = 5.0               # Richieste/secondo
    respect_retry_after: bool = True      # Rispetta header Retry-After
    
    # Parallelismo
    parallel_workers: int = 1             # Numero worker paralleli (1 = sequenziale)
    
    # Timeout
    request_timeout: int = 30             # Timeout lettura risposta (read timeout)
    connect_timeout: float = 5.0          # Timeout connessione TCP (connect timeout)
    # requests accetta (connect, read) come tupla — separare i due timeout evita
    # di aspettare request_timeout secondi su host irraggiungibili

    # SSL — disabilitare solo in ambienti di sviluppo con certificati self-signed
    disable_ssl_verify: bool = False      # Se True, disabilita verifica certificato SSL
    
    # Validazione
    validate_input: bool = True           # Valida input prima di chiamare API
    skip_null_params: bool = True         # Salta righe con parametri nulli
    
    def to_dict(self) -> Dict:
        return asdict(self)


@dataclass
class EnrichmentResult:
    """Risultato completo dell'enrichment."""
    success: bool
    stats: EnrichmentStats
    errors: List[RowError] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    
    # Metadata
    task_id: str = ""
    project_id: str = ""
    api_name: str = ""
    started_at: str = ""
    completed_at: str = ""
    
    # Output
    output_file: Optional[str] = None
    error_report_file: Optional[str] = None
    
    def to_dict(self) -> Dict:
        return {
            "success": self.success,
            "stats": self.stats.to_dict(),
            "errors": [e.to_dict() for e in self.errors],
            "warnings": self.warnings,
            "task_id": self.task_id,
            "project_id": self.project_id,
            "api_name": self.api_name,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "output_file": self.output_file,
            "error_report_file": self.error_report_file,
        }
    
    def add_error(self, row_index: int, message: str, 
                  error_type: str = "", input_params: Dict = None,
                  http_status: int = None, retry_count: int = 0):
        """Helper per aggiungere errore."""
        self.errors.append(RowError(
            row_index=row_index,
            status=RowStatus.ERROR,
            error_message=message,
            error_type=error_type,
            input_params=input_params or {},
            http_status=http_status,
            retry_count=retry_count
        ))
        self.stats.errors += 1


@dataclass
class PreviewResult:
    """Risultato preview su campione."""
    sample_size: int
    success_count: int
    error_count: int
    success_rate: float
    avg_time_per_row_ms: float
    estimated_total_time_seconds: float
    estimated_api_calls: int          # Dopo deduplicazione
    unique_param_combinations: int    # Combinazioni uniche trovate
    preview_data: List[Dict] = field(default_factory=list)  # Prime N righe risultato
    errors: List[RowError] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    
    def to_dict(self) -> Dict:
        return {
            "sample_size": self.sample_size,
            "success_count": self.success_count,
            "error_count": self.error_count,
            "success_rate": round(self.success_rate, 2),
            "avg_time_per_row_ms": round(self.avg_time_per_row_ms, 2),
            "estimated_total_time_seconds": round(self.estimated_total_time_seconds, 2),
            "estimated_total_time_formatted": self._format_time(self.estimated_total_time_seconds),
            "estimated_api_calls": self.estimated_api_calls,
            "unique_param_combinations": self.unique_param_combinations,
            "preview_data": self.preview_data,
            "errors": [e.to_dict() for e in self.errors],
            "warnings": self.warnings,
        }
    
    @staticmethod
    def _format_time(seconds: float) -> str:
        """Formatta tempo in formato leggibile."""
        if seconds < 60:
            return f"{int(seconds)}s"
        elif seconds < 3600:
            mins = int(seconds // 60)
            secs = int(seconds % 60)
            return f"{mins}m {secs}s"
        else:
            hours = int(seconds // 3600)
            mins = int((seconds % 3600) // 60)
            return f"{hours}h {mins}m"


@dataclass 
class EnrichmentValidationResult:
    """Risultato validazione input."""
    valid: bool
    total_rows: int
    valid_rows: int
    invalid_rows: int
    missing_columns: List[str] = field(default_factory=list)
    null_param_rows: List[int] = field(default_factory=list)  # Indici righe con param nulli
    warnings: List[str] = field(default_factory=list)
    
    def to_dict(self) -> Dict:
        return {
            "valid": self.valid,
            "total_rows": self.total_rows,
            "valid_rows": self.valid_rows,
            "invalid_rows": self.invalid_rows,
            "missing_columns": self.missing_columns,
            "null_param_rows_count": len(self.null_param_rows),
            "null_param_rows_sample": self.null_param_rows[:10],  # Prime 10
            "warnings": self.warnings,
        }
