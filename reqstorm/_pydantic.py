"""Use a Pydantic model as a schema. Pydantic is optional; it is imported only when used."""

from __future__ import annotations

import types
import typing
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from ._schema import _MISSING, Field, Schema, SchemaError, _contains_non_finite, _get, _parse_path

_SIMPLE = {int: int, float: float, str: str, bool: bool, datetime: "datetime"}


def is_model(value: Any) -> bool:
    try:
        from pydantic import BaseModel
    except ImportError:
        return False
    return isinstance(value, type) and issubclass(value, BaseModel)


def _column_type(annotation: Any) -> Any:
    origin = typing.get_origin(annotation)
    if origin is typing.Union or (origin is not None and origin is getattr(types, "UnionType", None)):
        options = [arg for arg in typing.get_args(annotation) if arg is not type(None)]
        if len(options) == 1:
            return _column_type(options[0])
        return "json"
    return _SIMPLE.get(annotation, "json")


@dataclass(frozen=True)
class ModelSchema(Schema):
    """A schema whose fields and validation come from a Pydantic model."""

    model: Any = None

    def rows(self, document: Any) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        from pydantic import ValidationError

        if self.explode is None:
            records: List[Any] = [document]
        else:
            array = _get(document, _parse_path(self.explode)[1])
            if array is _MISSING or array is None:
                return [], [{"record": document, "reasons": [f"{self.explode}: missing"]}]
            if not isinstance(array, list):
                return [], [{"record": document, "reasons": [f"{self.explode}: expected an array"]}]
            records = array
        rows: List[Dict[str, Any]] = []
        rejected: List[Dict[str, Any]] = []
        for record in records:
            raw: Dict[str, Any] = {}
            for column in self.fields:
                from_root, parts = self._parsed[column]
                value = _get(document if from_root else record, parts)
                if value is not _MISSING:
                    raw[column] = value
            try:
                instance = self.model.model_validate(raw)
            except ValidationError as error:
                reasons = [
                    f"{'.'.join(str(part) for part in issue['loc']) or 'record'}: {issue['msg']}"
                    for issue in error.errors()
                ]
                rejected.append({"record": record, "reasons": reasons})
                continue
            row = instance.model_dump(mode="python")
            bad = [column for column, value in row.items() if _contains_non_finite(value)]
            if bad:
                rejected.append(
                    {"record": record, "reasons": [f"{c}: NaN or Infinity is not allowed" for c in bad]}
                )
                continue
            rows.append({column: row[column] for column in self.fields})
        return rows, rejected


def schema_from_model(model: Any, explode: Optional[str] = None) -> ModelSchema:
    """Fields from a Pydantic v2 model.

    Each model field becomes a column of the same name. Its path is
    ``json_schema_extra={"path": "..."}`` if given, else the field's alias, else its name.
    ``json_schema_extra={"key": True}`` makes it part of the primary key. ``int``, ``float``,
    ``str``, ``bool`` and ``datetime`` (optional or not) get those column types; anything
    else (lists, dicts, nested models) is stored as JSON. Validation follows the model,
    including its strictness settings.
    """
    if not hasattr(model, "model_fields"):
        raise SchemaError("only Pydantic v2 models are supported")
    fields: Dict[str, Field] = {}
    for name, info in model.model_fields.items():
        extra = info.json_schema_extra if isinstance(info.json_schema_extra, dict) else {}
        path = extra.get("path") or info.alias or name
        fields[name] = Field(
            str(path),
            _column_type(info.annotation),
            required=info.is_required(),
            key=bool(extra.get("key", False)),
        )
    return ModelSchema(fields, explode, model=model)
