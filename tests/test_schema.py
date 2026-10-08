import json
from datetime import datetime, timezone
from urllib.parse import quote

import pytest

import reqstorm
from reqstorm import Field
from reqstorm._schema import convert, parse_json


def url(server, document):
    text = document if isinstance(document, str) else json.dumps(document)
    return f"{server}/json?body={quote(text)}"


# --- conversion ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value, spec, expected",
    [
        (5, Field("x", int), 5),
        (5.0, Field("x", int, coerce=True), 5),
        (" 42 ", Field("x", int, coerce=True), 42),
        (5, Field("x", float), 5.0),
        ("12.5", Field("x", float, coerce=True), 12.5),
        ("abc", Field("x", str), "abc"),
        (12, Field("x", str, coerce=True), "12"),
        (True, Field("x", bool), True),
        ("yes", Field("x", bool, coerce=True), True),
        (0, Field("x", bool, coerce=True), False),
        ("2026-10-08T10:00:00Z", Field("x", "datetime"), datetime(2026, 10, 8, 10, tzinfo=timezone.utc)),
        ("2026-10-08", Field("x", "datetime"), datetime(2026, 10, 8)),
        (0, Field("x", "datetime", coerce=True), datetime(1970, 1, 1, tzinfo=timezone.utc)),
        ({"a": [1, 2]}, Field("x", "json"), {"a": [1, 2]}),
        (None, Field("x", int), None),
    ],
)
def test_valid_values(value, spec, expected):
    assert convert(value, spec) == (expected, None)


@pytest.mark.parametrize(
    "value, spec, reason",
    [
        (5.5, Field("x", int), "expected an integer"),
        (5.0, Field("x", int), "expected an integer"),
        ("42", Field("x", int), "expected an integer"),
        (True, Field("x", int), "boolean"),
        (2**64, Field("x", int), "64-bit"),
        (float("nan"), Field("x", float), "NaN"),
        (float("inf"), Field("x", float), "NaN or Infinity"),
        ("nan", Field("x", float, coerce=True), "NaN"),
        ("12.5", Field("x", float), "expected a number"),
        (True, Field("x", float), "boolean"),
        (12, Field("x", str), "expected a string"),
        ({"a": 1}, Field("x", str, coerce=True), "expected a string"),
        (1, Field("x", bool), "expected a boolean"),
        ("maybe", Field("x", bool, coerce=True), "expected a boolean"),
        ("not a date", Field("x", "datetime"), "ISO 8601"),
        (1700000000, Field("x", "datetime"), "ISO 8601"),
        ({"a": [1.0, float("nan")]}, Field("x", "json"), "NaN"),
        (None, Field("x", int, required=True), "null"),
    ],
)
def test_invalid_values(value, spec, reason):
    converted, problem = convert(value, spec)
    assert converted is None and reason in problem


def test_missing_required_field():
    schema = reqstorm.Schema({"id": Field("id", int, required=True), "name": Field("name", str)})
    rows, rejected = schema.rows({"name": "a"})
    assert rows == [] and rejected[0]["reasons"] == ["id: missing"]


def test_strict_json_parsing_rejects_nan():
    assert parse_json(b'{"a": NaN}')[1] is not None
    assert parse_json(b'{"a": Infinity}')[1] is not None
    assert parse_json(b'{"a": 1.5}') == ({"a": 1.5}, None)
    assert "not valid JSON" in parse_json(b"<html>")[1]


# --- paths and explode -------------------------------------------------------------------


DOCUMENT = {
    "meta": {"page": 3},
    "items": [
        {"id": 1, "pricing": {"amount": 10.5, "currency": "TRY"}, "tags": ["a", "b"]},
        {"id": 2, "pricing": {"amount": 7, "currency": "EUR"}, "tags": []},
        {"id": "three", "pricing": {"amount": 1, "currency": "USD"}, "tags": []},
    ],
}

ITEM_SCHEMA = {
    "id": Field("id", int, required=True, key=True),
    "amount": Field("pricing.amount", float, required=True),
    "currency": Field("pricing.currency", str),
    "first_tag": Field("tags.0", str),
    "tags": Field("tags", "json"),
    "page": Field("$.meta.page", int),
}


def test_explode_nested_paths_and_root_paths():
    rows, rejected = reqstorm.Schema(ITEM_SCHEMA, explode="items[*]").rows(DOCUMENT)
    assert rows == [
        {"id": 1, "amount": 10.5, "currency": "TRY", "first_tag": "a", "tags": ["a", "b"], "page": 3},
        {"id": 2, "amount": 7.0, "currency": "EUR", "first_tag": None, "tags": [], "page": 3},
    ]
    assert rejected == [
        {"record": DOCUMENT["items"][2], "reasons": ["id: expected an integer, got a string 'three'"]}
    ]


def test_explode_missing_or_not_an_array():
    schema = reqstorm.Schema(ITEM_SCHEMA, explode="items")
    assert schema.rows({"meta": {}})[1][0]["reasons"] == ["items: missing"]
    assert schema.rows({"items": {"id": 1}})[1][0]["reasons"] == ["items: expected an array"]
    assert schema.rows({"items": []}) == ([], [])


@pytest.mark.parametrize(
    "bad", [{}, {"bad name": Field("x", int)}, {"x": Field("a..b", int)}, {"x": Field("a", list)}]
)
def test_invalid_schemas(bad):
    with pytest.raises(reqstorm.SchemaError):
        reqstorm.Schema(bad)


# --- infer_schema --------------------------------------------------------------------------


def test_infer_schema():
    schema = reqstorm.infer_schema(
        [
            {"id": 1, "name": "a", "price": {"amount": 10}, "tags": ["x"], "at": "2026-10-08T10:00:00Z"},
            {
                "id": 2,
                "name": "b",
                "price": {"amount": 10.5},
                "tags": [],
                "at": "2026-10-08T11:00:00Z",
                "note": None,
            },
        ]
    )
    types = {column: field.type for column, field in schema.fields.items()}
    assert types == {
        "id": int,
        "name": str,
        "price_amount": float,
        "tags": "json",
        "at": "datetime",
        "note": "json",
    }
    assert schema.fields["price_amount"].path == "price.amount"
    assert schema.fields["id"].required and not schema.fields["note"].required
    code = str(schema)
    assert "'price_amount': reqstorm.Field('price.amount', float, required=True)," in code
    assert eval(code, {"reqstorm": reqstorm}) == dict(schema.fields)


def test_infer_schema_with_explode_and_json_text():
    schema = reqstorm.infer_schema([json.dumps(DOCUMENT)], explode="items")
    assert schema.explode == "items" and schema.fields["id"].type == "json"  # int and str seen
    with pytest.raises(reqstorm.SchemaError):
        reqstorm.infer_schema(["not json"])


# --- extract -----------------------------------------------------------------------------


async def test_extract_from_results(server):
    results = await reqstorm.fetch_all(
        [
            url(server, DOCUMENT),
            url(server, '{"items": [{"id": 9, "pricing": {"amount": NaN}}]}'),
            f"{server}/status/500",
        ]
    )
    rows, rejected = reqstorm.extract(results, ITEM_SCHEMA, explode="items")
    assert [row["id"] for row in rows] == [1, 2] and rows[0]["source_index"] == 0
    reasons = [entry["reasons"][0] for entry in rejected]
    assert reasons[0].startswith("id: expected an integer")
    assert "not valid JSON" in reasons[1] and reasons[2] == "request failed: HTTP 500"


# --- files ---------------------------------------------------------------------------------


async def test_schema_to_jsonl_and_csv(server, tmp_path):
    jsonl, csv_path = tmp_path / "rows.jsonl", tmp_path / "rows.csv"
    summary = await reqstorm.fetch_to_file(
        [url(server, DOCUMENT)], jsonl, schema=ITEM_SCHEMA, explode="items"
    )
    assert (summary.rows, summary.rejected, summary.ok) == (2, 1, 1)
    lines = [json.loads(line) for line in jsonl.read_text().splitlines()]
    assert lines[0]["amount"] == 10.5 and lines[0]["tags"] == ["a", "b"] and lines[0]["source_index"] == 0
    await reqstorm.fetch_to_file([url(server, DOCUMENT)], csv_path, schema=ITEM_SCHEMA, explode="items")
    header, first = csv_path.read_text().splitlines()[:2]
    assert header.startswith("id,amount,currency,first_tag,tags,page,source_index")
    assert '"[""a"", ""b""]"' in first


async def test_rejects_table_needs_a_database(server, tmp_path):
    with pytest.raises(ValueError, match="rejects_table"):
        await reqstorm.fetch_to_file([], tmp_path / "x.jsonl", schema=ITEM_SCHEMA, rejects_table="bad")
