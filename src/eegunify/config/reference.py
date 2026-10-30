"""Generate ``docs/config_reference.md`` from the pydantic schema (single source of truth)."""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel

from eegunify.config import schema as schema_module
from eegunify.config.schema import Config

HEADER = """# Config reference

Generated from the pydantic schema by `eegunify config schema --markdown`; do not edit by hand
(a test fails when this file is out of date). Unknown keys are errors. Start from a draft:
`eegunify config init <recording>`, or see `code/configs/erpcore_n170.yaml`.
"""


def _type(spec: dict[str, Any]) -> str:
    if "$ref" in spec:
        return str(spec["$ref"]).rsplit("/", 1)[-1]
    if "enum" in spec:
        return " \\| ".join(f"`{v}`" for v in spec["enum"])
    if "const" in spec:
        return f"`{spec['const']}`"
    if "anyOf" in spec:
        return " \\| ".join(_type(s) for s in spec["anyOf"])
    t = spec.get("type", "any")
    if t == "array":
        items = spec.get("items") or spec.get("prefixItems")
        inner = _type(items) if isinstance(items, dict) else "…"
        return f"list[{inner}]"
    if t == "object":
        extra = spec.get("additionalProperties")
        return f"map[str, {_type(extra)}]" if isinstance(extra, dict) else "map"
    return str(t)


def _default(model: type[BaseModel], key: str, required: set[str]) -> str:
    """The real default, including ``default_factory`` values the JSON schema omits."""
    if key in required:
        return "**required**"
    value = model.model_fields[key].get_default(call_default_factory=True)
    if isinstance(value, BaseModel):
        return "see section"
    return f"`{json.dumps(value, ensure_ascii=False, default=str)}`"


def _section(name: str, schema: dict[str, Any], model: type[BaseModel]) -> list[str]:
    lines = [f"## `{name}`", ""]
    if schema.get("description"):
        lines += [" ".join(schema["description"].split()), ""]
    lines += ["| Key | Type | Default | Description |", "|---|---|---|---|"]
    required = set(schema.get("required", []))
    for key, spec in schema.get("properties", {}).items():
        desc = " ".join(spec.get("description", "").split()).replace("|", "\\|")
        lines.append(f"| `{key}` | {_type(spec)} | {_default(model, key, required)} | {desc} |")
    return [*lines, ""]


def markdown() -> str:
    schema = Config.model_json_schema()
    defs = schema.get("$defs", {})
    out = [HEADER, *_section("(top level)", schema, Config)]
    for name in sorted(defs):
        out += _section(name, defs[name], getattr(schema_module, name))
    return "\n".join(out).rstrip() + "\n"
