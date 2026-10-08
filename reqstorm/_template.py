"""Build requests from data: URL templates over rows from a list, a CSV file or a SQL query."""

from __future__ import annotations

import csv
import os
import string
from typing import Any, Callable, Dict, Iterable, Iterator, Mapping, Optional, Union
from urllib.parse import quote

from ._client import Request

__all__ = ["from_template", "read_csv", "read_sql"]

BodyTemplate = Union[None, Callable[[Mapping[str, Any]], Any], Mapping[str, Any]]


class _Escaped(string.Formatter):
    """str.format that percent-encodes every value, so "a b/c" stays one path segment."""

    def format_field(self, value: Any, format_spec: str) -> str:
        return quote(super().format_field(value, format_spec), safe="")


def _fill(template: Any, row: Mapping[str, Any]) -> Any:
    """Replace "{name}" strings inside a JSON-like template with the row's values."""
    if isinstance(template, str):
        if template.startswith("{") and template.endswith("}") and template[1:-1] in row:
            return row[template[1:-1]]  # keep the value's own type for "{field}" alone
        return template.format_map(row)
    if isinstance(template, Mapping):
        return {key: _fill(value, row) for key, value in template.items()}
    if isinstance(template, list):
        return [_fill(value, row) for value in template]
    return template


def from_template(
    url: str,
    rows: Iterable[Mapping[str, Any]],
    *,
    method: Optional[str] = None,
    json: BodyTemplate = None,
    params: BodyTemplate = None,
    headers: Optional[Mapping[str, str]] = None,
) -> Iterator[Request]:
    """One ``Request`` per row, with ``{field}`` placeholders filled from the row.

    Args:
        url: URL template such as ``"https://api.example.com/users/{id}/orders"``. Values
            are percent-encoded, so a value never changes the URL's structure.
        rows: Dicts, for example from ``read_csv`` or ``read_sql``.
        method: HTTP method; the default of ``fetch_all`` when omitted.
        json: A JSON body: a template dict whose ``"{field}"`` strings are filled from the
            row (a string that is only ``"{field}"`` keeps the value's type), or a function
            of the row.
        params: Query parameters, in the same forms as ``json``.
        headers: Headers sent with every request.

    The result is a generator, so millions of rows are read lazily.

        rows = reqstorm.read_csv("users.csv")
        requests = reqstorm.from_template("https://api.example.com/users/{id}", rows)
        reqstorm.fetch_to_file_sync(requests, "users.jsonl")
    """
    formatter = _Escaped()
    for row in rows:
        try:
            filled_url = formatter.vformat(url, (), row)
        except KeyError as error:
            raise KeyError(
                f"the URL template needs {error.args[0]!r}, which is missing from row {dict(row)}"
            ) from None

        yield Request(
            filled_url,
            method=method,
            headers=headers,
            params=_body(params, row),
            json=_body(json, row),
        )


def _body(template: BodyTemplate, row: Mapping[str, Any]) -> Any:
    if template is None:
        return None
    return template(row) if callable(template) else _fill(template, row)


def read_csv(path: str | os.PathLike[str], **options: Any) -> Iterator[Dict[str, str]]:
    """Rows of a CSV file as dicts keyed by the header row, read lazily.

    Extra keyword arguments go to ``csv.DictReader`` (for example ``delimiter=";"``).
    """
    with open(path, newline="", encoding=options.pop("encoding", "utf-8-sig")) as file:
        yield from csv.DictReader(file, **options)


def read_sql(
    connection: Any, query: str, parameters: Any = None, batch_size: int = 1000
) -> Iterator[Dict[str, Any]]:
    """Rows of a SQL query as dicts keyed by column name, fetched in batches.

    Works with any DB-API connection: sqlite3, psycopg, psycopg2, PyMySQL, ...
    """
    cursor = connection.cursor()
    try:
        cursor.execute(query, parameters) if parameters is not None else cursor.execute(query)
        names = [column[0] for column in cursor.description]
        while True:
            batch = cursor.fetchmany(batch_size)
            if not batch:
                break
            for values in batch:
                yield dict(zip(names, values))
    finally:
        cursor.close()
