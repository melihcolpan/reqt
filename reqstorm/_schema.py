"""Turn JSON responses into typed rows: fields, validation, nested paths and arrays."""

from __future__ import annotations

import json as jsonlib
import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple, Union

from ._client import Result

__all__ = ["Field", "Schema", "SchemaError", "extract", "infer_schema"]

FieldType = Union[type, str]
_TYPE_NAMES = {int: "int", float: "float", str: "str", bool: "bool", datetime: "datetime"}
_VALID_TYPES = ("int", "float", "str", "bool", "datetime", "json")
_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_INT64 = (-(2**63), 2**63 - 1)
_MISSING = object()


class SchemaError(ValueError):
    """A schema definition is invalid (not raised for invalid data, which is rejected instead)."""


@dataclass(frozen=True)
class Field:
    """Where a column's value comes from in the JSON, and what type it must have.

    Args:
        path: Dotted path to the value, such as ``"pricing.amount"`` or ``"tags.0"``.
            When exploding an array, paths are relative to each element; start a path
            with ``"$."`` to read from the root of the response instead (``"$.meta.page"``).
        type: ``int``, ``float``, ``str``, ``bool``, ``"datetime"`` or ``"json"``
            (any JSON value, stored as JSON).
        required: Reject the record when the value is missing or null.
        coerce: Also accept compatible values of another type, such as ``"12.5"`` for a
            float or ``1`` for a bool. Without it, types must match exactly.
        key: Part of the table's primary key. Writing a record whose key already exists
            updates that row instead of adding a new one.
    """

    path: str
    type: FieldType = "json"
    required: bool = False
    coerce: bool = False
    key: bool = False

    @property
    def type_name(self) -> str:
        name = _TYPE_NAMES.get(self.type, self.type) if isinstance(self.type, type) else self.type
        if name not in _VALID_TYPES:
            raise SchemaError(f"unsupported field type {self.type!r}; use one of {', '.join(_VALID_TYPES)}")
        return str(name)


@dataclass(frozen=True)
class Schema:
    """Columns of a table and the fields they are read from.

    Args:
        fields: Column name to ``Field``.
        explode: Path to an array in the response; each element becomes a row
            (``"items"`` or ``"data.items"``; a trailing ``"[*]"`` is optional).
    """

    fields: Mapping[str, Field]
    explode: Optional[str] = None
    _parsed: Dict[str, Tuple[bool, List[str]]] = field(default_factory=dict, compare=False, repr=False)

    def __post_init__(self) -> None:
        if not self.fields:
            raise SchemaError("a schema needs at least one field")
        for column, spec in self.fields.items():
            if not _IDENTIFIER.fullmatch(column):
                raise SchemaError(f"invalid column name {column!r}; use letters, digits and underscores")
            if not isinstance(spec, Field):
                raise SchemaError(f"column {column!r} must be a reqstorm.Field")
            spec.type_name  # noqa: B018  (validates the type)
            self._parsed[column] = _parse_path(spec.path)
        if self.explode is not None:
            _parse_path(self.explode)

    @property
    def keys(self) -> List[str]:
        return [column for column, spec in self.fields.items() if spec.key]

    def rows(self, document: Any) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """Rows and rejected records from one parsed JSON document.

        A rejected record is ``{"record": <the JSON it came from>, "reasons": [...]}``.
        """
        if self.explode is None:
            records: List[Any] = [document]
        else:
            from_root, parts = _parse_path(self.explode)
            array = _get(document, parts)
            if array is _MISSING or array is None:
                return [], [{"record": document, "reasons": [f"{self.explode}: missing"]}]
            if not isinstance(array, list):
                return [], [{"record": document, "reasons": [f"{self.explode}: expected an array"]}]
            records = array
        rows: List[Dict[str, Any]] = []
        rejected: List[Dict[str, Any]] = []
        for record in records:
            row: Dict[str, Any] = {}
            reasons: List[str] = []
            for column, spec in self.fields.items():
                from_root, parts = self._parsed[column]
                raw = _get(document if from_root else record, parts)
                value, problem = convert(raw, spec)
                if problem is not None:
                    reasons.append(f"{column}: {problem}")
                else:
                    row[column] = value
            if reasons:
                rejected.append({"record": record, "reasons": reasons})
            else:
                rows.append(row)
        return rows, rejected

    def __str__(self) -> str:
        """The schema as Python code, ready to paste and edit."""
        lines = ["{"]
        for column, spec in self.fields.items():
            type_text = spec.type.__name__ if isinstance(spec.type, type) else repr(spec.type)
            options = "".join(
                f", {name}=True" for name in ("required", "coerce", "key") if getattr(spec, name)
            )
            lines.append(f"    {column!r}: reqstorm.Field({spec.path!r}, {type_text}{options}),")
        lines.append("}")
        return "\n".join(lines)


def _parse_path(path: str) -> Tuple[bool, List[str]]:
    if not isinstance(path, str) or not path.strip():
        raise SchemaError("a path must be a non-empty string")
    from_root = path.startswith("$.") or path == "$"
    text = path[2:] if path.startswith("$.") else ("" if path == "$" else path)
    if text.endswith("[*]"):
        text = text[:-3]
    parts = [part for part in text.split(".")] if text else []
    if any(part == "" for part in parts):
        raise SchemaError(f"invalid path {path!r}")
    return from_root, parts


def _get(document: Any, parts: List[str]) -> Any:
    current = document
    for part in parts:
        if isinstance(current, dict):
            if part not in current:
                return _MISSING
            current = current[part]
        elif isinstance(current, list) and part.lstrip("-").isdigit():
            index = int(part)
            if not -len(current) <= index < len(current):
                return _MISSING
            current = current[index]
        else:
            return _MISSING
    return current


def _contains_non_finite(value: Any) -> bool:
    if isinstance(value, float):
        return not math.isfinite(value)
    if isinstance(value, dict):
        return any(_contains_non_finite(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_non_finite(item) for item in value)
    return False


def _parse_datetime(text: str) -> Optional[datetime]:
    candidate = text.strip()
    if candidate.endswith(("Z", "z")):
        candidate = candidate[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(candidate)
    except ValueError:
        return None


def convert(value: Any, spec: Field) -> Tuple[Any, Optional[str]]:
    """``(converted value, None)``, or ``(None, reason)`` when the value is not valid for the field."""
    kind = spec.type_name
    if value is _MISSING or value is None:
        if spec.required:
            return None, "missing" if value is _MISSING else "null"
        return None, None

    if kind == "json":
        if _contains_non_finite(value):
            return None, "NaN or Infinity is not valid JSON"
        return value, None

    if kind == "int":
        if isinstance(value, bool):
            return None, "expected an integer, got a boolean"
        if isinstance(value, int):
            result = value
        elif spec.coerce and isinstance(value, float) and math.isfinite(value) and value.is_integer():
            result = int(value)
        elif spec.coerce and isinstance(value, str) and re.fullmatch(r"\s*[-+]?\d+\s*", value):
            result = int(value)
        else:
            return None, f"expected an integer, got {_describe(value)}"
        if not _INT64[0] <= result <= _INT64[1]:
            return None, "integer out of the 64-bit range"
        return result, None

    if kind == "float":
        if isinstance(value, bool):
            return None, "expected a number, got a boolean"
        if isinstance(value, (int, float)):
            number = float(value)
        elif spec.coerce and isinstance(value, str):
            try:
                number = float(value)
            except ValueError:
                return None, f"expected a number, got {_describe(value)}"
        else:
            return None, f"expected a number, got {_describe(value)}"
        if not math.isfinite(number):
            return None, "NaN or Infinity is not allowed"
        return number, None

    if kind == "str":
        if isinstance(value, str):
            return value, None
        if spec.coerce and isinstance(value, (int, float)) and not isinstance(value, bool):
            if isinstance(value, float) and not math.isfinite(value):
                return None, "NaN or Infinity is not allowed"
            return str(value), None
        return None, f"expected a string, got {_describe(value)}"

    if kind == "bool":
        if isinstance(value, bool):
            return value, None
        if spec.coerce:
            if value in (0, 1) and not isinstance(value, float):
                return bool(value), None
            if isinstance(value, str) and value.strip().lower() in ("true", "false", "1", "0", "yes", "no"):
                return value.strip().lower() in ("true", "1", "yes"), None
        return None, f"expected a boolean, got {_describe(value)}"

    # datetime
    if isinstance(value, str):
        parsed = _parse_datetime(value)
        if parsed is not None:
            return parsed, None
        return None, f"expected an ISO 8601 date and time, got {_describe(value)}"
    if (
        spec.coerce
        and isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    ):
        try:
            return datetime.fromtimestamp(value, tz=timezone.utc), None
        except (OverflowError, OSError, ValueError):
            return None, "timestamp out of range"
    return None, f"expected an ISO 8601 date and time, got {_describe(value)}"


def _describe(value: Any) -> str:
    kinds = {bool: "a boolean", int: "an integer", float: "a number", str: "a string", list: "an array",
             dict: "an object"}  # fmt: skip
    kind = kinds.get(type(value), type(value).__name__)
    if isinstance(value, (str, int, float)) and not isinstance(value, bool):
        text = repr(value)
        return f"{kind} {text if len(text) <= 40 else text[:37] + '...'}"
    return kind


def _reject_constant(name: str) -> Any:
    raise ValueError(f"{name} is not valid JSON")


def parse_json(body: bytes) -> Tuple[Any, Optional[str]]:
    """Strict JSON parsing: NaN and Infinity, which Python's json module accepts, are errors."""
    try:
        return jsonlib.loads(body, parse_constant=_reject_constant), None
    except (ValueError, UnicodeDecodeError) as error:
        return None, f"response is not valid JSON ({error})"


def as_schema(schema: Union[Schema, Mapping[str, Field]], explode: Optional[str] = None) -> Schema:
    if isinstance(schema, Schema):
        if explode is not None and explode != schema.explode:
            return Schema(schema.fields, explode)
        return schema
    if isinstance(schema, Mapping):
        return Schema(dict(schema), explode)
    raise SchemaError("schema must be a dict of column name to reqstorm.Field, or a reqstorm.Schema")


def extract(
    results: Iterable[Result],
    schema: Union[Schema, Mapping[str, Field]],
    *,
    explode: Optional[str] = None,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Typed rows from results you already have, for example from ``fetch_all``.

    Returns ``(rows, rejected)``. Each row is a dict of column to value plus
    ``source_index`` and ``source_url``. Each rejected entry has ``source_index``,
    ``source_url``, ``reasons`` and the ``record`` it came from. Failed requests are
    rejected with the reason ``request failed``.
    """
    spec = as_schema(schema, explode)
    rows: List[Dict[str, Any]] = []
    rejected: List[Dict[str, Any]] = []
    for result in results:
        source = {"source_index": result.index, "source_url": result.url}
        if not result.ok:
            reason = (
                f"request failed: {result.error!r}"
                if result.error
                else f"request failed: HTTP {result.status}"
            )
            rejected.append({**source, "reasons": [reason], "record": None})
            continue
        document, problem = parse_json(result.body)
        if problem is not None:
            rejected.append({**source, "reasons": [problem], "record": None})
            continue
        good, bad = spec.rows(document)
        rows.extend({**row, **source} for row in good)
        rejected.extend({**source, **entry} for entry in bad)
    return rows, rejected


def _infer_type(values: List[Any]) -> FieldType:
    present = [value for value in values if value is not None]
    if not present:
        return "json"
    if all(isinstance(value, bool) for value in present):
        return bool
    if all(isinstance(value, int) and not isinstance(value, bool) for value in present):
        return int
    if all(isinstance(value, (int, float)) and not isinstance(value, bool) for value in present):
        return float
    if all(isinstance(value, str) for value in present):
        if all(_parse_datetime(value) is not None and any(c in value for c in "-:") for value in present):
            return "datetime"
        return str
    return "json"


def _flatten(document: Any, prefix: str, depth: int, out: Dict[str, Any]) -> None:
    for key, value in document.items():
        path = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(value, dict) and value and depth > 1:
            _flatten(value, path, depth - 1, out)
        else:
            out[path] = value


def infer_schema(
    samples: Iterable[Any],
    *,
    explode: Optional[str] = None,
    max_depth: int = 3,
) -> Schema:
    """Draft a schema from sample responses, for you to review and edit.

    ``samples`` can be ``Result`` objects, parsed JSON documents, or JSON text. Nested
    objects become dotted paths (up to ``max_depth`` levels), arrays become ``"json"``
    fields, and a field present and non-null in every sample is marked required.
    ``print(schema)`` shows it as Python code.

    The draft only reflects the samples: a field that was always an integer there may
    be a float in other responses. Check it against the API's documentation.
    """
    records: List[Dict[str, Any]] = []
    for sample in samples:
        if isinstance(sample, Result):
            if not sample.ok:
                continue
            document, problem = parse_json(sample.body)
        elif isinstance(sample, (str, bytes)):
            document, problem = parse_json(sample.encode() if isinstance(sample, str) else sample)
        else:
            document, problem = sample, None
        if problem is not None:
            continue
        candidates = [document]
        if explode is not None:
            array = _get(document, _parse_path(explode)[1])
            candidates = array if isinstance(array, list) else []
        for record in candidates:
            if isinstance(record, dict):
                flat: Dict[str, Any] = {}
                _flatten(record, "", max_depth, flat)
                records.append(flat)

    columns: Dict[str, List[Any]] = {}
    for flat in records:
        for path in flat:
            columns.setdefault(path, [])
    for path, values in columns.items():
        values.extend(flat.get(path, _MISSING) for flat in records)

    if not columns:
        raise SchemaError("no usable samples: pass parsed JSON objects, JSON text or successful results")

    fields: Dict[str, Field] = {}
    for path, values in columns.items():
        observed = [None if value is _MISSING else value for value in values]
        column = re.sub(r"[^A-Za-z0-9_]", "_", path.replace(".", "_"))
        if not _IDENTIFIER.fullmatch(column):
            column = f"f_{column}"
        while column in fields:
            column += "_"
        required = all(value is not _MISSING and value is not None for value in values)
        fields[column] = Field(path, _infer_type(observed), required=required)
    return Schema(fields, explode)
