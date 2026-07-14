"""Single canonical JSON encoding used by terminal and outbox identity."""

import json


def _require_primitive(value):
    if value is None or isinstance(value, (str, int, bool)):
        return
    if isinstance(value, list):
        for item in value:
            _require_primitive(item)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError("canonical payload keys must be strings")
            _require_primitive(item)
        return
    raise TypeError(f"canonical payload contains unsupported {type(value).__name__}")


def canonical_bytes(primitive_payload):
    _require_primitive(primitive_payload)
    return json.dumps(
        primitive_payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
