"""Canvas field presence: explicit non-null flat values win, including empties."""
from typing import Any, Mapping


def node_field(node: Any, key: str, default: Any = None) -> Any:
    if isinstance(node, Mapping):
        value = node.get(key)
        data = node.get("data")
    else:
        fields = getattr(node, "model_fields_set", None)
        value = getattr(node, key, None) if fields is None or key in fields else None
        data = getattr(node, "data", None)
    if value is not None:
        return value
    if isinstance(data, Mapping) and key in data:
        return data[key]
    return default
