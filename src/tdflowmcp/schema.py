"""Strict validator for the JSON Schema (2020-12) subset used by TDflowMCP tool schemas. Stdlib only.

Supported keywords: type (string or list), properties, required, additionalProperties (bool or
schema), enum, const, minLength, maxLength, pattern, minimum, maximum, items, minItems, maxItems,
maxProperties. Any other keyword in a schema is a programming error (``SchemaError``), so tool
schemas cannot silently rely on unsupported validation. Under TouchDesigner's Python the unit
tests cross-check every tool schema against the bundled ``jsonschema`` 4.23.
"""
from __future__ import annotations

import re
from typing import Any, List

KNOWN = {"type", "properties", "required", "additionalProperties", "enum", "const", "minLength", "maxLength",
         "pattern", "minimum", "maximum", "items", "minItems", "maxItems", "maxProperties", "description",
         "title", "default", "$schema"}


class SchemaError(Exception):
    pass


def _is_type(v: Any, t: str) -> bool:
    if t == "object":
        return isinstance(v, dict)
    if t == "array":
        return isinstance(v, list)
    if t == "string":
        return isinstance(v, str)
    if t == "boolean":
        return isinstance(v, bool)
    if t == "integer":
        return isinstance(v, int) and not isinstance(v, bool)
    if t == "number":
        return isinstance(v, (int, float)) and not isinstance(v, bool)
    if t == "null":
        return v is None
    raise SchemaError(f"unknown type {t}")


def check_schema(s: dict) -> None:
    if not isinstance(s, dict):
        raise SchemaError("schema must be an object")
    bad = set(s) - KNOWN
    if bad:
        raise SchemaError(f"unsupported keywords: {sorted(bad)}")
    for sub in (s.get("properties") or {}).values():
        check_schema(sub)
    if isinstance(s.get("additionalProperties"), dict):
        check_schema(s["additionalProperties"])
    if isinstance(s.get("items"), dict):
        check_schema(s["items"])


def errors(value: Any, s: dict, path: str = "$") -> List[str]:
    out: List[str] = []
    t = s.get("type")
    if t is not None:
        types = t if isinstance(t, list) else [t]
        if not any(_is_type(value, x) for x in types):
            return [f"{path}: expected {'/'.join(types)}"]
    if "const" in s and value != s["const"]:
        out.append(f"{path}: must equal {s['const']!r}")
    if "enum" in s and value not in s["enum"]:
        out.append(f"{path}: must be one of {s['enum']}")
    if isinstance(value, str):
        if "minLength" in s and len(value) < s["minLength"]:
            out.append(f"{path}: shorter than {s['minLength']}")
        if "maxLength" in s and len(value) > s["maxLength"]:
            out.append(f"{path}: longer than {s['maxLength']}")
        if "pattern" in s and not re.fullmatch(s["pattern"], value):        # no trailing-newline loophole
            out.append(f"{path}: does not match the required pattern")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in s and value < s["minimum"]:
            out.append(f"{path}: below {s['minimum']}")
        if "maximum" in s and value > s["maximum"]:
            out.append(f"{path}: above {s['maximum']}")
    if isinstance(value, list):
        if "minItems" in s and len(value) < s["minItems"]:
            out.append(f"{path}: fewer than {s['minItems']} items")
        if "maxItems" in s and len(value) > s["maxItems"]:
            out.append(f"{path}: more than {s['maxItems']} items")
        if isinstance(s.get("items"), dict):
            for i, v in enumerate(value):
                out += errors(v, s["items"], f"{path}[{i}]")
    if isinstance(value, dict):
        props = s.get("properties") or {}
        for k in s.get("required", []):
            if k not in value:
                out.append(f"{path}: missing required '{k}'")
        if "maxProperties" in s and len(value) > s["maxProperties"]:
            out.append(f"{path}: more than {s['maxProperties']} properties")
        ap = s.get("additionalProperties", True)
        for k, v in value.items():
            if k in props:
                out += errors(v, props[k], f"{path}.{k}")
            elif ap is False:
                out.append(f"{path}: unexpected property '{k}'")
            elif isinstance(ap, dict):
                out += errors(v, ap, f"{path}.{k}")
    return out
