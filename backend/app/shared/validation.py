"""Reusable validation for serialized API data structures."""

import json
from typing import Any


def enforce_json_size(value: Any, *, max_bytes: int, field_name: str) -> Any:
    """Reject a JSON-compatible value whose encoded representation is too large."""

    try:
        encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must contain JSON-compatible values.") from exc
    if len(encoded) > max_bytes:
        raise ValueError(f"{field_name} exceeds the allowed size of {max_bytes} bytes.")
    return value
