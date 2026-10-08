# Structured data

When an API returns JSON, you usually want its fields in proper columns, not the raw response. Give reqstorm a schema: which column comes from which field, and what type it must have. Every value is checked before it is written, so a table never ends up with a string in a number column or a `NaN` in a price.

```python
import psycopg
import reqstorm
from reqstorm import Field

schema = {
    "id":       Field("id", int, required=True, key=True),
    "name":     Field("name", str, required=True),
    "price":    Field("pricing.amount", float),
    "currency": Field("pricing.currency", str),
    "tags":     Field("tags", "json"),
    "updated":  Field("updated_at", "datetime"),
}

with psycopg.connect("dbname=shop") as connection:
    summary = reqstorm.fetch_to_db_sync(
        urls, connection, table="products", schema=schema,
        rejects_table="products_rejects", rate_limit="100/min",
    )

print(summary.rows, "rows,", summary.rejected, "rejected")
```

## Fields

`Field(path, type, required=False, coerce=False, key=False)`

| Type | Accepts | PostgreSQL | MySQL | SQLite |
|---|---|---|---|---|
| `int` | integers in the 64-bit range | `BIGINT` | `BIGINT` | `INTEGER` |
| `float` | numbers, never NaN or Infinity | `DOUBLE PRECISION` | `DOUBLE` | `REAL` |
| `str` | strings | `TEXT` | `LONGTEXT` | `TEXT` |
| `bool` | `true` and `false` | `BOOLEAN` | `BOOLEAN` | `INTEGER` |
| `"datetime"` | ISO 8601 strings, such as `2026-10-08T10:30:00Z` | `TIMESTAMPTZ` | `DATETIME(6)` (UTC) | `TEXT` (ISO 8601) |
| `"json"` | any JSON value, kept as JSON | `JSONB` | `JSON` | `TEXT` |

- **`required=True`** rejects a record when the value is missing or `null`; the column is `NOT NULL`. Optional fields are written as `NULL` when missing.
- **Strict by default.** An `int` field rejects `10.5`, `"42"` and `true`; a `str` field rejects `12`. With **`coerce=True`**, compatible values are converted instead: `"42"` and `42.0` for an `int`, `"12.5"` for a `float`, `12` for a `str`, `1`, `"yes"` and `"false"` for a `bool`, and Unix timestamps for a `datetime`. Values that cannot be converted are still rejected.
- **NaN and Infinity are always rejected.** They are not valid JSON, but Python's `json` module accepts them, so reqstorm parses responses strictly.
- **`key=True`** makes the field part of the primary key. Writing a record whose key already exists updates that row, so running the same batch again does not create duplicates. Without key fields, the table gets an `id` column instead.

## Paths and nested JSON

A path is a dotted list of keys and array indexes into the JSON:

```json
{"id": 7, "pricing": {"amount": 19.9, "currency": "EUR"}, "tags": ["home", "kitchen"]}
```

| Path | Value |
|---|---|
| `"id"` | `7` |
| `"pricing.amount"` | `19.9` |
| `"tags.0"` | `"home"` |
| `"tags"` | `["home", "kitchen"]` (with type `"json"`) |

There are three ways to handle nested data:

1. **A column per nested value.** `Field("pricing.amount", float)` reads into nested objects. This is the usual choice.
2. **A JSON column.** Parts whose shape varies, such as `tags` or `metadata`, can be kept whole with type `"json"`. In PostgreSQL they are `JSONB` and can be queried: `SELECT * FROM products WHERE tags ? 'home'`.
3. **A row per array element.** When a response holds a list, `explode` turns each element into a row:

```python
# {"meta": {"page": 3}, "items": [{"id": 1, ...}, {"id": 2, ...}]}
schema = {
    "id":   Field("id", int, key=True),
    "name": Field("name", str),
    "page": Field("$.meta.page", int),   # from the response root
}
reqstorm.fetch_to_db_sync(urls, connection, table="items", schema=schema, explode="items")
```

With `explode`, paths are relative to each element. Start a path with `$.` to read from the root of the response, for example a page number or a timestamp that applies to every element. An empty array produces no rows.

## Rejected records

A record with a missing required field or a value of the wrong type is never written, not even partly. When exploding, only the bad element is rejected; the other elements of the same response are written.

Every rejected record is listed in `summary.errors` with its reasons:

```python
{"index": 12, "method": "GET", "url": "https://api.example.com/products?page=13",
 "status": 200, "reasons": ["price: NaN or Infinity is not allowed"]}
```

With `rejects_table`, rejected records are also written to that table, with `source_index`, `source_url`, `reasons` (a JSON list) and the `record` itself (as JSON text), so you can inspect and replay them. Failed requests and responses that are not valid JSON are recorded there too.

## Where the rows go

| Target | How |
|---|---|
| PostgreSQL, MySQL, SQLite connection | `fetch_to_db(urls, connection, schema=...)` |
| SQLite file | `fetch_to_file(urls, "shop.db", schema=...)` |
| JSON Lines | `fetch_to_file(urls, "rows.jsonl", schema=...)`: one row per line, datetimes as ISO 8601 |
| CSV | `fetch_to_file(urls, "rows.csv", schema=...)`: one column per field, JSON fields as JSON text |
| Python lists | `rows, rejected = reqstorm.extract(results, schema)` with results from `fetch_all` |

Each row also gets `source_index`, `source_method` and `source_url`, the request it came from. They make `resume=True` work: responses that already produced rows are skipped. Pass `include_source=False` to leave them out.

An existing table must already have every column of the schema; reqstorm never alters or drops a table, and it tells you which columns are missing.

## Drafting a schema from samples

Writing a schema by hand is the reliable way, because only you know whether `price` can ever be a decimal. To get started quickly, let reqstorm draft one from a few responses and edit it:

```python
results = reqstorm.fetch_all_sync(urls[:20])
schema = reqstorm.infer_schema(results, explode="items")
print(schema)
```

```python
{
    'id': reqstorm.Field('id', int, required=True),
    'name': reqstorm.Field('name', str, required=True),
    'pricing_amount': reqstorm.Field('pricing.amount', float, required=True),
    'pricing_currency': reqstorm.Field('pricing.currency', str, required=True),
    'tags': reqstorm.Field('tags', 'json', required=True),
    'updated_at': reqstorm.Field('updated_at', 'datetime', required=True),
}
```

The draft reflects only the samples: a field that was always an integer there may be a decimal in the next response, and a field that was always present may be optional. Check it against the API's documentation, add `key=True` to the identifying field, and paste it into your code.

## Pydantic models

If you already describe the API with Pydantic, pass the model as the schema (`pip install "reqstorm[pydantic]"`, Pydantic 2):

```python
from typing import Optional
from pydantic import BaseModel, Field

class Product(BaseModel):
    id: int = Field(json_schema_extra={"key": True})
    name: str
    price: Optional[float] = Field(None, json_schema_extra={"path": "pricing.amount"})
    tags: list[str] = []

reqstorm.fetch_to_file_sync(urls, "shop.db", table="products", schema=Product, explode="items")
```

- Each model field becomes a column of the same name. Its path is `json_schema_extra={"path": ...}`, else the field's alias, else its name.
- `json_schema_extra={"key": True}` makes it part of the primary key.
- `int`, `float`, `str`, `bool` and `datetime` (optional or not) get those column types; lists, dicts, nested models and unions are stored as JSON.
- Validation is Pydantic's own, including coercion such as `"12.5"` to `12.5` unless the model is strict. A record that fails validation is rejected with Pydantic's messages as the reasons.
