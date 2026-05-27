"""
Sistema plugin per trasformazioni MANIPOLA.

Keeps transformation behavior isolated from orchestration callers.
"""

import json as _json

import json
import importlib
import re
from pathlib import Path
from typing import Any, Callable, List, Optional

from app.core.dataframes.ports import FrameLike
from app.core.manipola.types import (
    JsonObject,
    RegistryMap,
    RegistryRows,
    StringMap,
    StringSetMap,
)
from app.core.projects.canvas_models import Node
from app.utils.logger import get_logger

logger = get_logger(__name__)

FunctionRegistry = dict[str, Callable[..., Any]]


class PluginExecutor:
    """
    Executor con caricamento dinamico delle trasformazioni dal registry JSON.

    Le funzioni vengono importate dinamicamente al formato:
    "joins.op_join" → app.core.manipola.transforms.joins.op_join
    "spatial.op_geo_buffer" → app.core.manipola.transforms.spatial.op_geo_buffer
    """

    def __init__(self, registry_path: Optional[str] = None):
        """
        Args:
            registry_path: path al file transforms_registry.json.
                          Se None, usa il path di default nella stessa directory.

        Raises:
            ValueError: se `registry_path` e' passato e punta fuori dal
                        perimetro di `app/core/manipola/`.
        """
        _allowed_root = Path(__file__).parent.resolve()

        if registry_path is None:
            registry_path = _allowed_root / "transforms_registry.json"
        else:
            # Difesa in profondità: registry_path deve restare dentro
            # app/core/manipola/. Il registry JSON definisce quali
            # funzioni Python il plugin executor importera' via
            # importlib.import_module: e' il vero input-sensitive del
            # modulo, non il codice delle transform. Senza questo guard
            # un call site futuro che accettasse un path da request body
            # farebbe caricare un JSON arbitrario dal filesystem e
            # aprirebbe la catena: JSON controllato → function name
            # controllato → importlib su dotted path (confinato al
            # namespace app.core.manipola.transforms, ma comunque
            # indesiderato). Il check e' fail-closed: fuori perimetro
            # → ValueError, niente fallback silenzioso
            registry_path = Path(registry_path).resolve()
            if not registry_path.is_relative_to(_allowed_root):
                raise ValueError(
                    f"registry_path fuori dal perimetro di "
                    f"{_allowed_root}: {registry_path}"
                )

        self.registry_path = registry_path
        self.registry: RegistryMap = {}      # transform_id → config completa
        self.categories: RegistryMap = {}     # category_id → metadati categoria
        self.functions: FunctionRegistry = {}  # transform_id → funzione Python

        self._carica_registry()
        self._carica_funzioni()

    # CARICAMENTO

    def _carica_registry(self):
        """Carica il registry JSON con definizioni trasformazioni e categorie."""
        try:
            with open(self.registry_path, 'r', encoding='utf-8') as f:
                data = json.load(f)

            self.registry = data.get('transforms', {})
            self.categories = data.get('categories', {})

            logger.info(
                f"Caricato registry: {len(self.registry)} trasformazioni, "
                f"{len(self.categories)} categorie"
            )
        except FileNotFoundError:
            logger.error(f"[GESTITO] Registry non trovato: {self.registry_path}")
            raise
        except json.JSONDecodeError as e:
            logger.error(f"[CRASH] Errore parsing registry JSON: {e}", exc_info=True)
            raise

    def _carica_funzioni(self):
        """
        Importa dinamicamente tutte le funzioni dal registry.

        Il campo 'function' nel registry ha formato "modulo.nome_funzione":
        - "joins.op_join" → app.core.manipola.transforms.joins.op_join
        - "spatial.op_geo_buffer" → app.core.manipola.transforms.spatial.op_geo_buffer
        """
        for transform_id, config in self.registry.items():
            func_path = config.get('function')
            if not func_path:
                logger.warning(f"Trasformazione '{transform_id}' senza funzione definita")
                continue

            # Separa "modulo.funzione" → ("modulo", "funzione"
            parts = func_path.rsplit('.', 1)
            if len(parts) != 2:
                logger.warning(f"Path funzione invalido per '{transform_id}': {func_path}")
                continue

            module_name, func_name = parts

            try:
                full_module = f"app.core.manipola.transforms.{module_name}"
                module = importlib.import_module(full_module)
                func = getattr(module, func_name)
                self.functions[transform_id] = func
            except ImportError as e:
                logger.error(f"[CRASH] Errore import '{transform_id}': {e}", exc_info=True)
            except AttributeError as e:
                logger.error(f"[CRASH] Funzione '{func_name}' non trovata in '{module_name}': {e}", exc_info=True)

    def reload(self):
        """Ricarica registry e funzioni. Utile durante lo sviluppo."""
        self._carica_registry()
        self._carica_funzioni()

    # INTERROGAZIONE REGISTRY

    def get_registry(self) -> RegistryMap:
        """Restituisce il registry completo (usato dall'API frontend)."""
        return self.registry

    def get_categories(self) -> RegistryMap:
        """Restituisce le categorie definite nel registry."""
        return self.categories

    def get_transform_config(self, transform_id: str) -> Optional[JsonObject]:
        """Restituisce la configurazione di una singola trasformazione."""
        return self.registry.get(transform_id)

    def get_transforms_by_category(self, category: str) -> RegistryMap:
        """Restituisce tutte le trasformazioni di una categoria."""
        return {
            tid: config
            for tid, config in self.registry.items()
            if config.get('category') == category
        }

    def list_transforms(self) -> RegistryRows:
        """Elenca tutte le trasformazioni con informazioni sintetiche per la UI."""
        return [
            {
                'id': tid,
                'name': config.get('name'),
                'category': config.get('category'),
                'icon': config.get('icon'),
                'description': config.get('description')
            }
            for tid, config in self.registry.items()
        ]

    # ESECUZIONE

    def execute(
        self,
        transform_id: str,
        inputs: List[FrameLike],
        node: Node
    ) -> FrameLike:
        """
        Esegue una trasformazione con la configurazione dal nodo canvas.

        Args:
            transform_id: ID trasformazione (es. "geo_buffer", "join")
            inputs: lista DataFrame/GeoDataFrame di input
            node: nodo del canvas con configurazione in node.data.config

        Returns:
            DataFrame/GeoDataFrame risultato

        Raises:
            ValueError: se trasformazione non trovata o parametri invalidi
        """
        config = self.registry.get(transform_id)
        if not config:
            raise ValueError(f"Trasformazione '{transform_id}' non trovata nel registry")

        func = self.functions.get(transform_id)
        if not func:
            raise ValueError(f"Funzione per '{transform_id}' non caricata")

        # Estrai configurazione dal nodo canvas (formato flat o legacy data wrapper
        node_config = node.config or (node.data.get('config', {}) if node.data else {})
        node_config = self._resolve_column_references(node_config, node, config)
        kwargs = self._prepara_kwargs(config, node_config)


        try:
            # Le trasformazioni 'multi' ricevono la lista completa di input
            # es. join tra due tabelle). Le 'single' ricevono solo il primo DataFrame
            if config.get('inputs') == 'multi':
                return func(inputs, **kwargs)
            else:
                if not inputs:
                    raise ValueError("Nessun input fornito")
                return func(inputs[0], **kwargs)
        except Exception as e:
            logger.error(f"[CRASH] Errore esecuzione '{transform_id}': {e}", exc_info=True)
            raise

    def execute_with_params(
        self,
        transform_id: str,
        inputs: List[FrameLike],
        params: JsonObject
    ) -> FrameLike:
        """
        Esegue una trasformazione con parametri diretti (senza Node).

        Utile per test e uso programmatico dove non si ha un nodo canvas.

        Args:
            transform_id: ID trasformazione
            inputs: lista DataFrame input
            params: mapping parametri diretti (come se fossero node.data.config)

        Returns:
            DataFrame risultato
        """
        config = self.registry.get(transform_id)
        if not config:
            raise ValueError(f"Trasformazione '{transform_id}' non trovata")

        func = self.functions.get(transform_id)
        if not func:
            raise ValueError(f"Funzione per '{transform_id}' non caricata")

        kwargs = self._prepara_kwargs(config, params)

        if config.get('inputs') == 'multi':
            return func(inputs, **kwargs)
        else:
            return func(inputs[0], **kwargs)

    # PREPARAZIONE PARAMETRI

    def _prepara_kwargs(
        self,
        transform_config: JsonObject,
        node_config: JsonObject,
    ) -> JsonObject:
        """
        Prepara i kwargs per la funzione dalla configurazione del nodo.

        Logica per ogni parametro definito nel registry:
        1. Se depends_on non è soddisfatto → salta (parametro condizionale)
        2. Se presente in node_config → usa quel valore
        3. Se ha un default nel registry → usa il default
        4. Se è obbligatorio e mancante → log warning, usa None
        5. Se è opzionale e mancante → salta

        Tutti i valori vengono convertiti al tipo corretto tramite _converti_valore.
        """
        kwargs = {}
        parameters = transform_config.get('parameters', [])

        for param in parameters:
            param_id = param['id']

            # Es: il parametro "custom_value" dipende da "mode" == "custom"
            if 'depends_on' in param:
                dep = param['depends_on']
                dep_param_id = dep['param']
                dep_expected = dep['value']
                
                # Cerca il valore corrente, oppure il default del parametro dipendente
                if dep_param_id in node_config:
                    dep_value = node_config[dep_param_id]
                else:
                    # Cerca il default nel registry
                    dep_param_def = next((p for p in parameters if p['id'] == dep_param_id), None)
                    dep_value = dep_param_def.get('default') if dep_param_def else None
                
                # Supporta array di valori possibili
                if isinstance(dep_expected, list):
                    if dep_value not in dep_expected:
                        continue  # Dipendenza non soddisfatta
                else:
                    if dep_value != dep_expected:
                        continue  # Dipendenza non soddisfatta

            if param_id in node_config:
                value = node_config[param_id]
            elif 'default' in param:
                value = param['default']
            elif param.get('required'):
                logger.warning(f"Parametro obbligatorio '{param_id}' mancante, uso None")
                value = None
            else:
                continue  # Parametro opzionale non fornito

            # Conversione al tipo corretto
            value = self._converti_valore(value, param.get('type'))
            kwargs[param_id] = value

        return kwargs

    def _resolve_column_references(
        self,
        node_config: JsonObject,
        node: Node,
        transform_config: Optional[JsonObject] = None,
    ) -> JsonObject:
        """Resolve stored column ids to current runtime column names.

        The UI stores both the visible name and a stable ``*_column_id``
        companion. Names are still passed to transform functions because
        pandas DataFrames are name-based, but the id companion lets the
        config survive upstream alias/name changes.
        """
        if not isinstance(node_config, dict):
            return node_config

        resolved = dict(node_config)
        id_to_name, legacy_name_to_name = self._column_reference_maps(node)
        if not id_to_name and not legacy_name_to_name:
            return resolved

        def by_id(column_id: Any, fallback: Any = None) -> Any:
            if not column_id:
                return fallback
            return id_to_name.get(str(column_id), fallback)

        for key, value in list(resolved.items()):
            if key.endswith("_column_id"):
                base = key[:-10]
                if base in resolved:
                    resolved[base] = by_id(value, resolved[base])
            elif key.endswith("_column_ids"):
                base = key[:-11]
                if base in resolved and isinstance(value, list) and value:
                    current_values = resolved.get(base)
                    if not isinstance(current_values, list):
                        current_values = []
                    resolved[base] = [
                        by_id(column_id, current_values[idx] if idx < len(current_values) else None)
                        for idx, column_id in enumerate(value)
                    ]

        for key, value in list(resolved.items()):
            if isinstance(value, list):
                if key in {"aggregations", "aggs"}:
                    resolved[key] = [
                        self._resolve_dict_column_ref(item, id_to_name, "column")
                        for item in value
                    ]
                elif key == "renames":
                    resolved[key] = [
                        self._resolve_dict_column_ref(item, id_to_name, "old_name", "old_name_id")
                        for item in value
                    ]
                elif key == "maskings":
                    resolved[key] = [
                        self._resolve_dict_column_ref(item, id_to_name, "column")
                        for item in value
                    ]

        resolved = self._resolve_legacy_param_names(
            resolved,
            transform_config or {},
            legacy_name_to_name,
        )

        for key, value in list(resolved.items()):
            if not key.endswith("_column_refs") or not isinstance(value, list):
                continue
            expression_key = key[:-12]
            expression = resolved.get(expression_key)
            if isinstance(expression, str):
                resolved[expression_key] = self._rewrite_expression_refs(
                    expression,
                    value,
                    id_to_name,
                )

        for param in (transform_config or {}).get("parameters", []) or []:
            if not isinstance(param, dict) or param.get("type") != "expression":
                continue
            expression_key = param.get("id")
            expression = resolved.get(expression_key)
            if isinstance(expression, str):
                resolved[expression_key] = self._rewrite_expression_legacy_names(
                    expression,
                    legacy_name_to_name,
                )

        return resolved

    def _column_reference_maps(self, node: Node) -> tuple[StringMap, StringMap]:
        data = node.data if isinstance(node.data, dict) else {}
        raw_columns = []
        for field in ("inputColumns", "columns"):
            value = data.get(field)
            if isinstance(value, list):
                raw_columns.extend(value)

        id_to_name: StringMap = {}
        legacy_candidates: StringSetMap = {}
        for col in raw_columns:
            if not isinstance(col, dict):
                continue
            col_id = col.get("id") or col.get("column_id")
            name = col.get("name")
            if col_id and name:
                id_to_name[str(col_id)] = str(name)
            if not name:
                continue
            current_name = str(name)
            for legacy_key in ("source_name",):
                legacy_name = col.get(legacy_key)
                if legacy_name and str(legacy_name) != current_name:
                    legacy_candidates.setdefault(str(legacy_name), set()).add(current_name)

        legacy_name_to_name = {
            legacy: next(iter(names))
            for legacy, names in legacy_candidates.items()
            if len(names) == 1
        }
        return id_to_name, legacy_name_to_name

    def _resolve_legacy_param_names(
        self,
        resolved: JsonObject,
        transform_config: JsonObject,
        legacy_name_to_name: StringMap,
    ) -> JsonObject:
        if not legacy_name_to_name:
            return resolved

        out = dict(resolved)
        for param in transform_config.get("parameters", []) or []:
            if not isinstance(param, dict):
                continue
            param_id = param.get("id")
            param_type = param.get("type")
            if not param_id or param_id not in out:
                continue
            value = out.get(param_id)
            if param_type in {"column", "geometry_column"} and isinstance(value, str):
                out[param_id] = legacy_name_to_name.get(value, value)
            elif param_type in {"columns", "column_order"} and isinstance(value, list):
                out[param_id] = [
                    legacy_name_to_name.get(item, item) if isinstance(item, str) else item
                    for item in value
                ]
            elif param_type == "aggregation_list" and isinstance(value, list):
                out[param_id] = [
                    self._resolve_dict_legacy_name(item, legacy_name_to_name, "column")
                    for item in value
                ]

        for key, value in list(out.items()):
            if key in {"aggregations", "aggs"} and isinstance(value, list):
                out[key] = [
                    self._resolve_dict_legacy_name(item, legacy_name_to_name, "column")
                    for item in value
                ]
            elif key == "renames" and isinstance(value, list):
                out[key] = [
                    self._resolve_dict_legacy_name(item, legacy_name_to_name, "old_name")
                    for item in value
                ]
            elif key == "maskings" and isinstance(value, list):
                out[key] = [
                    self._resolve_dict_legacy_name(item, legacy_name_to_name, "column")
                    for item in value
                ]

        return out

    def _resolve_dict_column_ref(
        self,
        item: Any,
        id_to_name: StringMap,
        name_field: str,
        id_field: str = "column_id",
    ) -> Any:
        if not isinstance(item, dict):
            return item
        out = dict(item)
        column_id = out.get(id_field)
        if column_id and str(column_id) in id_to_name:
            out[name_field] = id_to_name[str(column_id)]
        return out

    def _resolve_dict_legacy_name(
        self,
        item: Any,
        legacy_name_to_name: StringMap,
        name_field: str,
    ) -> Any:
        if not isinstance(item, dict):
            return item
        out = dict(item)
        value = out.get(name_field)
        if isinstance(value, str) and value in legacy_name_to_name:
            out[name_field] = legacy_name_to_name[value]
        return out

    def _rewrite_expression_refs(
        self,
        expression: str,
        refs: list[Any],
        id_to_name: StringMap,
    ) -> str:
        rewritten = expression
        for ref in refs:
            if not isinstance(ref, dict):
                continue
            old_name = str(ref.get("name") or "")
            new_name = id_to_name.get(str(ref.get("id") or ""))
            if not old_name or not new_name or old_name == new_name:
                continue
            rewritten = rewritten.replace(f"`{old_name}`", f"`{new_name}`")
            rewritten = rewritten.replace(f"['{old_name}']", f"['{new_name}']")
            rewritten = rewritten.replace(f'["{old_name}"]', f'["{new_name}"]')
            if old_name.isidentifier():
                rewritten = re.sub(
                    rf"\b{re.escape(old_name)}\b",
                    new_name,
                    rewritten,
                )
        return rewritten

    def _rewrite_expression_legacy_names(
        self,
        expression: str,
        legacy_name_to_name: StringMap,
    ) -> str:
        rewritten = expression
        for old_name, new_name in legacy_name_to_name.items():
            if not old_name or not new_name or old_name == new_name:
                continue
            rewritten = rewritten.replace(f"`{old_name}`", f"`{new_name}`")
            rewritten = rewritten.replace(f"['{old_name}']", f"['{new_name}']")
            rewritten = rewritten.replace(f'["{old_name}"]', f'["{new_name}"]')
            if old_name.isidentifier():
                rewritten = re.sub(
                    rf"\b{re.escape(old_name)}\b",
                    new_name,
                    rewritten,
                )
        return rewritten

    def _converti_valore(self, value: Any, param_type: Optional[str]) -> Any:
        """
        Converte un valore al tipo corretto in base alla definizione nel registry.

        Conversioni supportate:
        - number: float se contiene '.', altrimenti int. Stringa vuota → None
        - boolean: "true"/"1"/"yes"/"si" → True (case-insensitive)
        - columns/string_list: stringa con virgole → lista di stringhe pulite
        """
        if value is None or param_type is None:
            return value

        try:
            if param_type == 'number':
                if isinstance(value, str) and value.strip() == '':
                    return None
                return float(value) if '.' in str(value) else int(value)

            elif param_type == 'boolean':
                if isinstance(value, str):
                    return value.lower() in ('true', '1', 'yes', 'si')
                return bool(value)

            elif param_type in ('columns', 'string_list'):
                if isinstance(value, str):
                    return [v.strip() for v in value.split(',') if v.strip()]
                return value if isinstance(value, list) else [value]

            elif param_type == 'aggregation_list':
                # Aggregazioni: deve essere una lista di dict {column, function, ...}
                if isinstance(value, str):
                    try:
                        parsed = _json.loads(value)
                        return parsed if isinstance(parsed, list) else []
                    except (ValueError, TypeError):
                        logger.warning(f"aggregation_list non parsabile: {value[:100]}")
                        return []
                if isinstance(value, list):
                    return value
                return []

            elif param_type == 'pivot_mapping':
                # Il mapping pivot arriva come dict {valore_sorgente: nome_output}
                # Se il frontend lo serializza come stringa JSON, deserializziamo
                if isinstance(value, str):
                    try:
                        parsed = _json.loads(value)
                        return parsed if isinstance(parsed, dict) else {}
                    except (ValueError, TypeError):
                        return {}
                return value if isinstance(value, dict) else {}

        except (ValueError, TypeError) as e:
            logger.warning(f"Errore conversione valore '{value}' a {param_type}: {e}")

        return value

    def get_default_config(self, transform_id: str) -> JsonObject:
        """
        Restituisce la configurazione di default per una trasformazione.

        Utile per pre-popolare la UI quando l'utente aggiunge un nodo trasformazione.

        Returns:
            dict con param_id → valore_default per tutti i parametri con default
        """
        config = self.registry.get(transform_id, {})
        defaults = {}

        for param in config.get('parameters', []):
            if 'default' in param:
                defaults[param['id']] = param['default']

        return defaults


# SINGLETON

_executor_instance: Optional[PluginExecutor] = None


def get_executor() -> PluginExecutor:
    """
    Restituisce l'istanza singleton dell'executor.

    L'istanza viene creata al primo accesso e riutilizzata per tutti
    i successivi, evitando di ricaricare il registry ad ogni trasformazione.

    Uso:
        from app.core.manipola.plugin_executor import get_executor

        executor = get_executor
        result = executor.execute('geo_buffer', [df], node)
    """
    global _executor_instance
    if _executor_instance is None:
        _executor_instance = PluginExecutor()
    return _executor_instance


def reload_executor():
    """Ricarica l'executor a caldo. Utile per sviluppo e test."""
    global _executor_instance
    if _executor_instance:
        _executor_instance.reload()
    else:
        _executor_instance = PluginExecutor()


# LAYER DI COMPATIBILITÀ

class PluginExecutorCompatibility:
    """
    Layer di compatibilità per integrare PluginExecutor con ManipolaExecutor.

    Permette una migrazione graduale dal vecchio sistema di dispatch
    al nuovo sistema basato su plugin. Se una trasformazione è definita
    nel registry, viene eseguita dal PluginExecutor; altrimenti si usa
    il fallback al vecchio sistema.

    Uso nel ManipolaExecutor._dispatch_transform:

        def _dispatch_transform(self, node, inputs):
            transform_op = node.transform_op or node.data.get('transform_op')
            result = PluginExecutorCompatibility.dispatch(transform_op, inputs, node)
            if result is not None:
                return result
            # Fallback al vecchio sistema...
    """

    @staticmethod
    def is_plugin_transform(transform_id: str) -> bool:
        """Verifica se una trasformazione è gestita dal plugin system."""
        executor = get_executor()
        return executor.get_transform_config(transform_id) is not None

    @staticmethod
    def dispatch(
        transform_id: str,
        inputs: List[FrameLike],
        node: Node
    ) -> Optional[FrameLike]:
        """
        Esegue la trasformazione se presente nel registry, altrimenti restituisce None.

        Returns:
            DataFrame risultato, o None se trasformazione non gestita dal plugin system
        """
        executor = get_executor()

        if not executor.get_transform_config(transform_id):
            return None

        return executor.execute(transform_id, inputs, node)
